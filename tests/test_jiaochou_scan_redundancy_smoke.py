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

    def test_scan_groups_reported_pairs_into_stable_clusters(self, test_db):
        t = utcnow()
        cluster = [
            MemoryItem(
                id=f"mem-cluster-{i}",
                content=text,
                confidence=0.9,
                user_id="default",
                created_at=t,
                updated_at=t,
            )
            for i, text in enumerate(
                [
                    "生产数据库主库使用 PostgreSQL 配置端口 5432",
                    "生产环境 PostgreSQL 主数据库连接端口 5432",
                    "PostgreSQL 生产数据库主库端口设置为 5432",
                ]
            )
        ]
        test_db.add_all(cluster)
        test_db.commit()

        result = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=10, similarity_threshold=0.3
        )

        expected_ids = {item.id for item in cluster}
        matching = [c for c in result["redundant_clusters"] if set(c["memory_ids"]) == expected_ids]
        assert len(matching) == 1
        assert matching[0]["member_count"] == 3
        assert matching[0]["pair_count"] == 3
        assert len(matching[0]["pairs"]) == matching[0]["pair_count"]
        assert {
            "memory_id_a",
            "memory_id_b",
            "similarity",
            "action_suggestion",
            "divergence_summary",
            "divergence_tokens_a",
            "divergence_tokens_b",
        } == set(matching[0]["pairs"][0])
        assert matching[0]["suggested_action"] in {"merge", "update"}
        assert matching[0]["common_tokens"]
        assert matching[0]["divergence_tokens"]

    def test_dense_cluster_uses_all_candidates_without_expanding_pair_details(self, test_db):
        t = utcnow()
        duplicate_memories = [
            MemoryItem(
                id=f"mem-dense-{i:03d}",
                content="生产主库使用 PostgreSQL 端口 5432",
                confidence=0.9,
                user_id="default",
                created_at=t,
                updated_at=t,
            )
            for i in range(100)
        ]
        test_db.add_all(duplicate_memories)
        test_db.commit()

        result = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=100, similarity_threshold=1.0
        )

        assert result["total_pairs"] == 100
        assert result["total_clusters"] == 1
        assert result["candidate_pair_count"] == 4950
        cluster = result["redundant_clusters"][0]
        assert cluster["member_count"] == 100
        assert cluster["pair_count"] == 4950
        assert cluster["reported_pair_count"] == 100
        assert cluster["omitted_pair_count"] == 4850
        assert len(cluster["pairs"]) == result["total_pairs"]
        assert cluster["suggested_action"] == "retain_both"
        assert "未逐对校勘" in cluster["correction_suggestion"]

    def test_candidate_cluster_without_reported_edges_gets_representative_review(self, test_db):
        t = utcnow()
        memories = [
            MemoryItem(
                id=f"mem-dense-{group}-{i:03d}",
                content=(
                    "生产主库使用 PostgreSQL 端口 5432"
                    if group == "a"
                    else "用户偏好开启护眼深色模式 22 点后"
                ),
                confidence=0.9,
                user_id="default",
                created_at=t,
                updated_at=t,
            )
            for group, count in (("z", 98), ("a", 2))
            for i in range(count)
        ]
        test_db.add_all(memories)
        test_db.commit()

        result = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=100, similarity_threshold=1.0
        )

        assert result["total_pairs"] == 100
        omitted_cluster = next(
            cluster
            for cluster in result["redundant_clusters"]
            if cluster["memory_ids"] == ["mem-dense-a-000", "mem-dense-a-001"]
        )
        assert omitted_cluster["reported_pair_count"] == 0
        assert omitted_cluster["omitted_pair_count"] == 1
        assert omitted_cluster["pairs"] == []
        assert omitted_cluster["representative_pair"]["action_suggestion"] == "merge"
        assert omitted_cluster["suggested_action"] == "retain_both"
        assert "未逐对校勘" in omitted_cluster["correction_suggestion"]

    def test_conflicting_value_pair_suggests_review_not_merge(self, test_db):
        t = utcnow()
        pair = [
            MemoryItem(
                id=f"mem-conflict-{i}",
                content=text,
                confidence=0.9,
                user_id="default",
                created_at=t,
                updated_at=t,
            )
            for i, text in enumerate(
                [
                    "生产环境当前数据库主库核心网络服务端口配置为 5432",
                    "生产环境当前数据库主库核心网络服务端口配置为 5433",
                ]
            )
        ]
        test_db.add_all(pair)
        test_db.commit()

        result = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=10, similarity_threshold=0.7
        )

        assert result["total_pairs"] == 1
        assert result["redundant_pairs"][0]["action_suggestion"] == "retain_both"
        assert result["total_clusters"] == 1
        assert result["redundant_clusters"][0]["suggested_action"] == "retain_both"
        assert "核验" in result["redundant_clusters"][0]["correction_suggestion"]

    def test_scan_does_not_bridge_dissimilar_pair_through_a_neighbor(self, test_db):
        t = utcnow()
        bridge = [
            MemoryItem(
                id=f"mem-bridge-{letter}",
                content=text,
                confidence=0.9,
                user_id="default",
                created_at=t,
                updated_at=t,
            )
            for letter, text in [
                ("a", "alpha bravo charlie delta"),
                ("b", "alpha bravo charlie delta echo foxtrot"),
                ("c", "alpha bravo echo foxtrot"),
            ]
        ]
        test_db.add_all(bridge)
        test_db.commit()

        result = JiaochouEngine(principal=_principal("default")).scan_redundancy(
            limit=10, similarity_threshold=0.6
        )

        expected_pairs = {
            frozenset((bridge[0].id, bridge[1].id)),
            frozenset((bridge[1].id, bridge[2].id)),
        }
        actual_pairs = {
            frozenset((pair["memory_id_a"], pair["memory_id_b"]))
            for pair in result["redundant_pairs"]
        }
        assert actual_pairs == expected_pairs
        cluster_pairs = {
            frozenset((pair["memory_id_a"], pair["memory_id_b"]))
            for cluster in result["redundant_clusters"]
            for pair in cluster["pairs"]
        }
        assert cluster_pairs == expected_pairs
        assert all(cluster["member_count"] == 2 for cluster in result["redundant_clusters"])


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
