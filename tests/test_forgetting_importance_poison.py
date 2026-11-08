r"""票 35（.scratch/v0.68-next-window-2026-11/issues/35）：importance 写入面无界 → 遗忘引擎确定性崩溃且全库停摆。

病灶链（立案轮 6 探针 `.scratch/_v068_probe_r6_forgetting_poison.py` 实测）：
chat 泳道 profile（base_s=3, boost=5）下，importance=-0.6 → `_lane_strength`=0.0
→ `math.exp(-days/0.0)` ZeroDivisionError；importance=-0.61 → strength<0 →
正指数 OverflowError。`apply_forgetting` keyset 循环零容错，毒行 id 及其后
全部记忆的衰减/归档永久停摆（同页对照行饿死）。

五臂（红→绿）：
1. 引擎臂 ×2：真 SQLite + 真 `apply_forgetting` 直调，复刻探针同页对照设计
   （ctl → poison → victim），毒行插中间锁「饿死」形态；
2. REST 写面臂：`PUT /terminal/memory/{id}` importance=-0.6/NaN/inf 一律 422，
   合法值照常通过且落库（真 HTTP + 真 DB + 真 FTS）；
3. LLM 提取臂：沉潜 `chat_json` 返回负值/超 1 → 落库值钳入 [0,1]；
4. apply 臂：手工构造 `proposed_patch` 毒值提案（绕过生成面，防「只修生成面
   不修 apply 面」的假绿），`apply_proposal` 落库值钳入 [0,1]。

mock 纪律：被测链路零 mock——`db.get_session` 换绑是本仓既有测试形态（替换
会话工厂指向测试引擎，不替换任何计算逻辑）；替身仅 embed/向量库/chat_json
外部 LLM 边界。
"""

import math
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import get_current_user
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.memory.forgetting import apply_forgetting
from lantai.models.tables import MemoryItem, MemoryProposal

_PURIFIED_CONTENT = "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度"
_FRAG_IDS = ["mem_frag_01", "mem_frag_02", "mem_frag_03"]


# ── 引擎臂 harness（同 test_forgetting_keyset 形态）──────────────────────────


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _general_strength() -> float:
    """general lane、importance=0.5、use_count=0 的保持强度（对齐 _lane_strength）。"""
    profile = settings.LANE_DECAY_PROFILES["general"]
    return profile["base_s"] + profile["importance_boost"] * 0.5


def _seed_poison_page(engine, *, poison_importance: float, poison_age_days: float):
    """同页三行：ctl（id 最小，正常）→ poison（毒行，chat 泳道）→ victim（id 最大，正常）。

    复刻立案探针对照设计：毒行插中间，饿死形态 = ctl 被处理后撞异常，
    victim 的 decay 分毫未动、永不被衰减。
    """
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="pz-ctl",
                content="同页对照行",
                memory_type="text",
                tier="longterm",
                lane="general",
                importance=0.5,
                status="active",
                decay_score=1.0,
                last_used_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
            )
        )
        s.add(
            MemoryItem(
                id="pz-poison",
                content="毒行",
                memory_type="text",
                tier="longterm",
                lane="chat",
                importance=poison_importance,
                status="active",
                decay_score=1.0,
                last_used_at=now - timedelta(days=poison_age_days),
                created_at=now - timedelta(days=poison_age_days),
            )
        )
        s.add(
            MemoryItem(
                id="pz-victim",
                content="饿死受害行",
                memory_type="text",
                tier="longterm",
                lane="general",
                importance=0.5,
                status="active",
                decay_score=1.0,
                last_used_at=now - timedelta(days=1),
                created_at=now - timedelta(days=1),
            )
        )
        s.commit()


def _states(engine):
    with Session(engine) as s:
        rows = s.exec(select(MemoryItem).order_by(MemoryItem.id)).all()
        return {r.id: (r.status, r.decay_score) for r in rows}


@pytest.fixture
def poison_env():
    engine = _make_engine()
    with patch.object(db_module, "get_session", lambda: Session(engine)):
        yield SimpleNamespace(engine=engine)


def _assert_control_rows_alive(states):
    """同页两条正常行必须被正常处理（毒行不得拖死整页）。"""
    one_day_decay = math.exp(-1.0 / _general_strength())
    assert states["pz-ctl"][0] == "active", "对照行不应被归档"
    assert states["pz-ctl"][1] == pytest.approx(one_day_decay, rel=1e-2), "对照行应正常衰减"
    assert states["pz-victim"][1] == pytest.approx(one_day_decay, rel=1e-2), (
        "毒行同页的受害行不得饿死（decay 停在 1.0 即停摆实锤）"
    )


def test_zero_strength_poison_no_longer_crashes_or_starves_page(poison_env):
    """strength=0.0（importance=-0.6）：修复前 ZeroDivisionError，同页饿死。"""
    _seed_poison_page(poison_env.engine, poison_importance=-0.6, poison_age_days=10)
    apply_forgetting()  # 修复前此处抛 ZeroDivisionError: float division by zero
    states = _states(poison_env.engine)
    _assert_control_rows_alive(states)
    assert states["pz-poison"][0] == "archived", "毒行应按兜底强度快速衰减并被归档"
    assert states["pz-poison"][1] < settings.ARCHIVE_DECAY_THRESHOLD


def test_negative_strength_poison_overflow_no_longer_crashes(poison_env):
    """strength<0（importance=-0.61，40 天龄）：修复前 OverflowError: math range error。"""
    _seed_poison_page(poison_env.engine, poison_importance=-0.61, poison_age_days=40)
    apply_forgetting()  # 修复前此处抛 OverflowError: math range error
    states = _states(poison_env.engine)
    _assert_control_rows_alive(states)
    assert states["pz-poison"][0] == "archived"


# ── REST 写面臂 harness（同 test_terminal_write_guards 形态）─────────────────


@pytest.fixture()
def rest_env(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    dummy = type(
        "D",
        (),
        {
            "add": lambda self, *, ids, embeddings, metadatas: None,
            "delete": lambda self, *a, **k: None,
        },
    )()
    monkeypatch.setattr("lantai.storage.vector_store.get_vector_store", lambda: dummy)
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: dummy)
    import lantai.services.record_ops_service as ros

    monkeypatch.setattr(ros, "embed", lambda texts: [[0.1] * 1536 for _ in texts])

    with TestClient(app) as client:
        yield engine, client


def _principal(user_id="u1", role="user", lanes=("general",)):
    from lantai.core.auth import Principal

    return Principal(user_id=user_id, role=role, allowed_lanes=list(lanes), tenant_id=None)


def _override(client, principal, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: principal)


def _rest_memory(engine, memory_id):
    with Session(engine) as s:
        return s.get(MemoryItem, memory_id).importance


def _add_rest_memory(engine, *, id="pz-rest"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=id,
                memory_type="semantic",
                key=f"k-{id}",
                content=f"content-{id}",
                lane="general",
                user_id="u1",
                status="active",
            )
        )
        s.commit()


@pytest.mark.parametrize(
    "raw_body",
    ['{"importance": -0.6}', '{"importance": NaN}', '{"importance": Infinity}'],
    ids=["negative", "nan", "inf"],
)
def test_rest_put_rejects_poison_importance_with_422(rest_env, monkeypatch, raw_body):
    """毒值一律 422（宁 miss 不脏写），且拒绝即不落库——行内 importance 保持原值。

    NaN/inf 必须走原生 JSON 文本体（httpx 的 json= 拒绝序列化非有限值，
    那条路毒值根本到不了服务器）；Python json.loads 接受 NaN/Infinity 字面量，
    Starlette 服务端真实解析到 pydantic——才是对验证面的真打。
    """
    engine, client = rest_env
    _add_rest_memory(engine)
    _override(client, _principal(), monkeypatch)
    resp = client.put(
        "/terminal/memory/pz-rest", content=raw_body, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422, resp.text
    assert _rest_memory(engine, "pz-rest") == 0.5, "422 拒绝后不得有半写"


def test_rest_put_accepts_valid_importance(rest_env, monkeypatch):
    """正面对照：合法值照常 200 并落库（写面收紧不得误伤正常面）。"""
    engine, client = rest_env
    _add_rest_memory(engine)
    _override(client, _principal(), monkeypatch)
    resp = client.put("/terminal/memory/pz-rest", json={"importance": 0.9})
    assert resp.status_code == 200, resp.text
    assert _rest_memory(engine, "pz-rest") == 0.9


# ── LLM 提取臂 + apply 臂 harness（param_env：内存库 + 真 FTS + 外部面替身）──


def _seed_fragments(s):
    """3 条同主题偏好碎片记忆（与 test_consolidation 同构，保证 jieba 聚类稳定命中）。"""
    s.add_all(
        [
            MemoryItem(
                id="mem_frag_01",
                content="大哥今天早上泡了明前大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
            ),
            MemoryItem(
                id="mem_frag_02",
                content="大哥喜欢喝大佛龙井茶，水温要求85度",
                lane="preference",
                domain="user",
                decay_score=0.88,
                status="active",
            ),
            MemoryItem(
                id="mem_frag_03",
                content="大哥日常饮品偏好为浙江新昌大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.85,
                status="active",
            ),
        ]
    )
    s.commit()


@pytest.mark.parametrize(
    ("raw", "expected"), [(-0.6, 0.0), (1.7, 1.0)], ids=["negative-to-0", "over-to-1"]
)
def test_consolidation_clamps_llm_extracted_importance(param_env, raw, expected):
    """LLM 提取无钳制（提示注入可诱导负值）：落库值必须钳入 [0,1]。

    替身仅 chat_json（外部 LLM 边界）；沉潜主链（聚类/獬豸闸/TrustMem/落库）全真。
    """
    from lantai.services.consolidation_service import run_consolidation_cycle

    session_factory, _ = param_env
    with session_factory() as s:
        _seed_fragments(s)
        with patch(
            "lantai.services.consolidation_service.chat_json",
            return_value={"consolidated_content": _PURIFIED_CONTENT, "importance": raw, "confidence": 0.95},
        ):
            rep = run_consolidation_cycle(session=s)
    assert rep["status"] == "success", rep
    assert rep["new_memories"] == 1, "前提失效：主记忆未落库"
    with session_factory() as s:
        master = s.exec(select(MemoryItem).where(MemoryItem.content == _PURIFIED_CONTENT)).first()
        assert master is not None, "主记忆未落库"
        assert master.importance == expected, (
            f"LLM 返回 importance={raw}，落库应钳到 {expected}，实得 {master.importance}"
        )


@pytest.mark.parametrize(
    ("raw", "expected"), [(-0.6, 0.0), (1.7, 1.0)], ids=["negative-to-0", "over-to-1"]
)
def test_apply_clamps_poison_patch_importance(param_env, raw, expected):
    """手工构造毒值 proposed_patch（绕过生成面）：apply 落库值必须钳入 [0,1]。

    防「只修生成面（consolidation 钳制）不修 apply 面（promoter 平移）」的假绿：
    patch 是 DB 里的 JSON，异源/存量提案可携带任意值。
    """
    from lantai.evolution.promoter import apply_proposal

    session_factory, _ = param_env
    with session_factory() as s:
        _seed_fragments(s)
        s.add(
            MemoryProposal(
                id="prop-pz",
                proposal_type="consolidation",
                evidence_ids=list(_FRAG_IDS),
                reason="毒值提案（测试直构，绕过生成面）",
                proposed_patch={
                    "content": _PURIFIED_CONTENT,
                    "importance": raw,
                    "confidence": 0.95,
                    "domain": "user",
                    "lane": "preference",
                    "source_ids": list(_FRAG_IDS),
                    "decay_class": "semantic",
                },
                confidence=0.95,
                status="pending",
            )
        )
        s.commit()
    res = apply_proposal("prop-pz")
    assert res["ok"] is True, res
    with session_factory() as s:
        master = s.exec(select(MemoryItem).where(MemoryItem.content == _PURIFIED_CONTENT)).first()
        assert master is not None, "apply 后主记忆未落库"
        assert master.importance == expected, (
            f"patch importance={raw}，apply 落库应钳到 {expected}，实得 {master.importance}"
        )
