"""票 59：校雠 scan_redundancy O(n²) 全表炸弹——全库 active 记忆 Python 侧两两 jieba 相似度。

`.scratch/v0.68-next-window-2026-11/issues/59-jiaochou-scan-redundancy-quadratic.md`

**先说影响**（MCP 面无钳制，LLM 可自主调用的工具一次调用即可拖死单线程 stdio 宿主）：

| 病灶 | 修复前 |
|---|---|
| 全表加载 + O(n²) 两两 jieba | `limit` 只截输出对数（凑够即 break），计算量不减——1 万条 ≈ 5000 万次分词比对 |
| limit/threshold 零钳制 | threshold=0 全对报告、limit=-1/500 原样透传 |
| `_check_access` Python 侧逐行判 | 全表先加载再过滤，principal=None 时照样全表 |

**修法**：SQL 侧身份下推（票 57 收敛口径：None→`viewer_of`="default"，admin/`SYSTEM_VIEWER`
全表，`swarm_shared` 公共结晶人人可读）+ 候选池硬上界 `SCAN_CANDIDATES_MAX=100`（最近创建
优先），池内预分词一次再做廉价 set-Jaccard，报告对复用 `compare_and_collate` 单一真源；
`limit` 同时钳制计算池与输出，`threshold` 越界 (0,1] 拒绝（ValueError，MCP 派发器转
-32602 错误载荷，REST pydantic 422——不许 500）。

**口径变更（如实申报）**：`limit` 语义从「先扫到的 N 对」（插入序敏感，票 05 续20 的
flaky 根因）改为「最近创建的 min(limit, 100) 行候选池内、全对按相似度排序取最相似 N 对」。
老于池窗口的冗余对不再可见——宁界不爆，资源安全优先于完备性；`test_jiaochou_api_smoke`
的既有机制锁随本票重写钉新契约。
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cognition.jiaochou import JiaochouEngine
from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


@pytest.fixture()
def test_db():
    """真 SQLite 内存库 + get_session 指向它（不 mock 计算逻辑）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    original = db_module.get_session

    def session_factory() -> Session:
        return Session(eng)

    db_module.get_session = session_factory
    yield Session(eng)
    # 必须还原成原来的可调用对象（同 test_mcp_none_scope_leaks 教训）
    db_module.get_session = original
    eng.dispose()


def _principal(user_id: str | None):
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=None, role="user")


def _filler(tag: str, i: int, t, *, owner: str = "default") -> MemoryItem:
    """互不相识的填充行：数字 token 全库唯一，两两 Jaccard≈0（配额内杂讯低于阈值）。

    不能用相似文案凑数：凑够 limit 对就 break 的旧实现会在相似语料上提前逃逸，
    规模红臂就测不到全表 O(n²) 的真实形态（真实大库的主体正是互不相识的杂讯）。
    """
    return MemoryItem(
        id=f"mem-{tag}-{i}-{uuid.uuid4().hex[:8]}",
        content=f"杂项{i}：{i * 1000} {i * 1000 + 1} {i * 1000 + 2} {i * 1000 + 3}",
        confidence=0.9,
        user_id=owner,
        created_at=t,
        updated_at=t,
    )


def _pair(tag: str, t, *, owner: str = "default") -> list[MemoryItem]:
    """一对真实近重复记忆（同 engine smoke seam2 语料，实测相似度 ≥ 0.6）。"""
    return [
        MemoryItem(
            id=f"mem-{tag}-a-{uuid.uuid4().hex[:8]}",
            content="生产集群已开启两级本地与分布式缓存",
            confidence=0.92,
            user_id=owner,
            created_at=t,
            updated_at=t,
        ),
        MemoryItem(
            id=f"mem-{tag}-b-{uuid.uuid4().hex[:8]}",
            content="生产环境集群配置了两级本地与分布式缓存架构",
            confidence=0.92,
            user_id=owner,
            created_at=t,
            updated_at=t,
        ),
    ]


class TestScanBoundedScale:
    """修复目标 1+2（TDD 要求 1）：规模红臂——300 条互不相识记忆，扫描耗时恒定。"""

    def test_scan_300_rows_stays_within_bounded_time(self, test_db):
        t = utcnow()
        for i in range(300):
            test_db.add(_filler("scale", i, t))
        test_db.commit()

        engine = JiaochouEngine(principal=_principal(None))
        start = time.perf_counter()
        res = engine.scan_redundancy(limit=10, similarity_threshold=0.5)
        elapsed = time.perf_counter() - start

        # 计算池钳制：只扫最近 min(limit, SCAN_CANDIDATES_MAX) 行，不再全表
        assert res["scanned_pool"] == 10, res.get("scanned_pool")
        assert res["total_pairs"] == 0
        # 规模红臂（阈值取本机基线的保守倍数：红测实测修复前 300 行全表 O(n²) 8.57s，
        # 修复后池内 45 对 set-Jaccard 约 0.1s——2.0s 上下都有 10 倍余量）：
        assert elapsed < 2.0, f"scan_redundancy 300 行耗时 {elapsed:.2f}s——全表两两分词未除"


class TestScanParamClamp:
    """修复目标 2（TDD 要求 2）：钳制臂——越界参数语义明确，不许 500。"""

    def test_threshold_out_of_range_rejected(self, test_db):
        engine = JiaochouEngine(principal=_principal("default"))
        with pytest.raises(ValueError, match="similarity_threshold"):
            engine.scan_redundancy(limit=10, similarity_threshold=0)
        with pytest.raises(ValueError, match="similarity_threshold"):
            engine.scan_redundancy(limit=10, similarity_threshold=2)
        # 合法上界恰为 1.0：不拒绝（报告 token 集完全一致的对）
        res = engine.scan_redundancy(limit=10, similarity_threshold=1.0)
        assert res["total_pairs"] == 0

    def test_limit_clamped_into_pool_window(self, test_db):
        t = utcnow()
        for i in range(150):
            test_db.add(_filler("clamp", i, t))
        test_db.commit()

        engine = JiaochouEngine(principal=_principal("default"))
        # 下界钳制：limit=-1 → 1 行池（1 行构不成对）
        res = engine.scan_redundancy(limit=-1, similarity_threshold=0.5)
        assert res["scanned_pool"] == 1, res.get("scanned_pool")
        assert res["total_pairs"] == 0
        # 上界钳制：limit=500 → SCAN_CANDIDATES_MAX 行池（资源安全上界不该被调大）
        res = engine.scan_redundancy(limit=500, similarity_threshold=0.5)
        assert res["scanned_pool"] == 100, res.get("scanned_pool")

    def test_limit_must_be_int_not_bool(self, test_db):
        engine = JiaochouEngine(principal=_principal("default"))
        with pytest.raises(ValueError, match="limit"):
            engine.scan_redundancy(limit=True, similarity_threshold=0.5)


class TestScanStillFindsNearDup:
    """修复目标 1 反面（TDD 要求 3）：语义回归臂——池口径收窄不得吞掉明显冗余。"""

    def test_near_dup_pair_in_recent_window_is_reported(self, test_db):
        t_old = utcnow()
        t_new = t_old + timedelta(days=1)
        for i in range(300):
            test_db.add(_filler("reg", i, t_old))
        pair = _pair("reg", t_new)
        for m in pair:
            test_db.add(m)
        test_db.commit()

        res = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
        hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
        assert {pair[0].id, pair[1].id} in hits, (
            f"池窗口收窄吞掉了明显冗余对：{res['total_pairs']} 对里没有它"
        )


class TestScanIdentity:
    """修复目标 1（TDD 要求 4）：身份臂——None 收敛 default（票 57 口径交叉引用）。

    两个 None 形态分开测：引擎直收 `principal=None`（MCP 宿主不透传身份的真路径，
    修复前 `_check_access` None→True 全放行）与 `Principal(user_id=None)`（具名对象的
    user_id 缺省，`viewer_of` 本来就收敛——现状锁，两态同判）。
    """

    def test_engine_principal_none_scans_default_only(self, test_db):
        t_old = utcnow()
        t_new = t_old + timedelta(hours=1)
        alice_pair = _pair("alice", t_old, owner="user-alice")
        default_pair = _pair("deft", t_new, owner="default")
        for m in [*alice_pair, *default_pair]:
            test_db.add(m)
        test_db.commit()

        res = JiaochouEngine(principal=None).scan_redundancy(limit=10, similarity_threshold=0.5)
        hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
        assert {default_pair[0].id, default_pair[1].id} in hits
        assert {alice_pair[0].id, alice_pair[1].id} not in hits, (
            "无身份扫描越权看见他人记忆——票 57 收敛未在本引擎落实"
        )

    def test_named_principal_none_user_id_same_convergence(self, test_db):
        t_old = utcnow()
        t_new = t_old + timedelta(hours=1)
        alice_pair = _pair("alice", t_old, owner="user-alice")
        default_pair = _pair("deft", t_new, owner="default")
        for m in [*alice_pair, *default_pair]:
            test_db.add(m)
        test_db.commit()

        res = JiaochouEngine(principal=_principal(None)).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
        hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
        assert {default_pair[0].id, default_pair[1].id} in hits
        assert {alice_pair[0].id, alice_pair[1].id} not in hits

    def test_system_viewer_scans_full_table(self, test_db):
        t_old = utcnow()
        t_new = t_old + timedelta(hours=1)
        alice_pair = _pair("alice", t_old, owner="user-alice")
        default_pair = _pair("deft", t_new, owner="default")
        for m in [*alice_pair, *default_pair]:
            test_db.add(m)
        test_db.commit()

        res = JiaochouEngine(principal=_principal(SYSTEM_VIEWER)).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
        hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
        assert {alice_pair[0].id, alice_pair[1].id} in hits
        assert {default_pair[0].id, default_pair[1].id} in hits
