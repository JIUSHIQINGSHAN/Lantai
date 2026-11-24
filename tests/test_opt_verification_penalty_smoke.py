"""等价性与 N+1 锁：`/verification/stats` 的降权系数。

修前列表页逐行调 `reliability_penalty(venue_class)`，而该函数每次
`with db.get_session()` 重查同一行——N 行 N 次开会话+查询。现按已取到的行
用纯函数 `penalty_from_stat` 计算，判定体原样搬过去。

本文件锁两件事：
1. 等价性：`penalty_from_stat(行)` 与 `reliability_penalty(类别)` 对同一行
   逐条一致（含阈值内/外、TTL 过期、样本不足、None 五种形态）；
2. 计数：列表页一次请求只发一次 SELECT（不靠耗时的运气）。

不 mock 产品逻辑：真实内存库 + 真判定体。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.parameters.trust_models import SignalReliabilityStat


@pytest.fixture
def penalty_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

    from datetime import timedelta

    now = utcnow()
    min_samples = settings.PENALTY_MIN_SAMPLES
    with Session(engine) as s:
        s.add(
            SignalReliabilityStat(
                id="srs-good",
                venue_class="good",
                pass_count=max(min_samples * 10, 100),
                fail_count=0,
                fail_streak=0,
                last_verified_at=now,
            )
        )
        s.add(
            SignalReliabilityStat(
                id="srs-bad",
                venue_class="bad",
                pass_count=max(min_samples, 3),
                fail_count=max(min_samples, 2) * 3,
                fail_streak=max(settings.PENALTY_FAIL_STREAK, 1) * 2,
                last_verified_at=now,
            )
        )
        s.add(
            SignalReliabilityStat(
                id="srs-stale",
                venue_class="stale",
                pass_count=max(min_samples, 3),
                fail_count=max(min_samples, 2) * 3,
                fail_streak=max(settings.PENALTY_FAIL_STREAK, 1) * 2,
                last_verified_at=now - timedelta(days=settings.PENALTY_TTL_DAYS + 5),
            )
        )
        s.add(
            SignalReliabilityStat(
                id="srs-tiny",
                venue_class="tiny",
                pass_count=1,
                fail_count=1,
                fail_streak=5,
                last_verified_at=now,
            )
        )
        s.commit()
    return engine


def test_penalty_from_stat_matches_lookup(penalty_env):
    """等价性：两种算法对同一行逐条一致。"""
    from lantai.parameters import reliability

    with Session(penalty_env) as s:
        rows = s.exec(__import__("sqlmodel").select(SignalReliabilityStat)).all()
        by_class = {r.venue_class: r for r in rows}
    assert len(by_class) >= 4, "夹具没造全"
    for vc, row in by_class.items():
        assert reliability.penalty_from_stat(row) == reliability.reliability_penalty(vc), (
            f"{vc} 两种算法不一致"
        )


def test_penalty_none_stat_is_one():
    """没有统计行时按 1.0（不动手）——与查库版返回 None 时的口径一致。"""
    from lantai.parameters.reliability import penalty_from_stat

    assert penalty_from_stat(None) == 1.0


def test_penalty_values_are_clamped(penalty_env):
    """反向保险：降权不得低于 0.5 下限，且 must 真的降（不是恒 1.0 的假绿）。"""
    from lantai.parameters import reliability

    with Session(penalty_env) as s:
        bad = s.exec(
            __import__("sqlmodel")
            .select(SignalReliabilityStat)
            .where(SignalReliabilityStat.venue_class == "bad")
        ).first()
    value = reliability.penalty_from_stat(bad)
    assert 0.5 <= value < 1.0, f"bad 类未按预期降权：{value}"


def test_stats_route_does_one_query(penalty_env, monkeypatch):
    """列表页一次请求只发一次 SELECT（原为每行一次 get_session + 查询）。"""
    from fastapi.testclient import TestClient

    from lantai.api.app import app

    opens = {"n": 0}
    real_get_session = db_module.get_session

    def counting_get_session():
        opens["n"] += 1
        return real_get_session()

    monkeypatch.setattr(db_module, "get_session", counting_get_session)
    with TestClient(app) as client:
        resp = client.get("/verification/stats")
    assert resp.status_code == 200, resp.text
    # 固定开销：路由本身一次 + 鉴权/中间件若干（与行数无关）。判据是「与行数
    # 无关」而不是某个绝对次数：夹具 4 行时若仍是逐行查库，会是 4+ 次。
    # 这里断言不超过 3 次，且再做一次「行数翻倍、次数不变」的对照。
    baseline = opens["n"]
    assert baseline <= 3, f"列表页开了 {baseline} 次 session，N+1 未除"

    # 对照：加 12 行后次数不得增长（真 N+1 会线性增长）
    with Session(penalty_env) as s2:
        for i in range(12):
            s2.add(
                SignalReliabilityStat(
                    id=f"srs-extra-{i}",
                    venue_class=f"extra-{i}",
                    pass_count=5,
                    fail_count=1,
                    fail_streak=0,
                    last_verified_at=utcnow(),
                )
            )
        s2.commit()
    opens["n"] = 0
    with TestClient(app) as client:
        resp2 = client.get("/verification/stats")
    assert resp2.status_code == 200
    assert opens["n"] == baseline, (
        f"行数从 4 增到 16 时 session 次数 {baseline}→{opens['n']}，说明按行查库（N+1）"
    )
