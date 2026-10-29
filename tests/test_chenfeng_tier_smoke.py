"""尘封·冷热分级存储与自动封藏策略引擎冒烟测试 (v0.36 Ticket 01)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 与无 mock 冒烟测试纪律。
覆盖公共接口 Seams：
1. evaluate_cold_candidates: 多维冷数据检测（艾宾浩斯极低分、长期未访问零命中、退役/软取代状态）；
2. archive_cold_memories: 批量自动封藏执行、原子状态推进（active -> archived）、MemoryCheckpoint 底本快照；
3. hybrid_search 隔离: 验证尘封记忆被标准热检索隔离排除，但拓扑关联与物理数据完整保留；
4. 行级 ACL 与租户防御: 验证非属主/非管理员无法探查或封藏他人私密记忆。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from lantai.cognition.chenfeng import ChenfengEngine
from lantai.core.auth import Principal
from lantai.models.tables import LifecycleStatus, MemoryCheckpoint, MemoryItem
from lantai.retrieval.hybrid import hybrid_search


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def test_evaluate_cold_candidates_multi_dimensional(param_env):
    """Seam 1: 验证多维冷数据识别规则。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")

    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    old_time = now - timedelta(days=60)
    recent_time = now - timedelta(days=2)

    with session_factory() as s:
        # 1. 活跃热记忆：高分且近期使用 -> 不应被封藏
        s.add(
            MemoryItem(
                id="mem_hot",
                content="经常使用的重要核心配置",
                status="active",
                user_id="alice",
                decay_score=0.95,
                use_count=15,
                last_used_at=recent_time,
                created_at=old_time,
            )
        )
        # 2. 冷记忆 A：decay_score 极低 (< 0.05)
        s.add(
            MemoryItem(
                id="mem_cold_decay",
                content="很久以前的过时事实碎片",
                status="active",
                user_id="alice",
                decay_score=0.02,
                use_count=1,
                last_used_at=old_time,
                created_at=old_time,
            )
        )
        # 3. 冷记忆 B：长期未访问 (超过30天) 且 0 次使用
        s.add(
            MemoryItem(
                id="mem_cold_idle",
                content="一次性写入后从未被唤醒的临时对话",
                status="active",
                user_id="alice",
                decay_score=0.20,
                use_count=0,
                last_used_at=None,
                created_at=old_time,
            )
        )
        # 4. 冷记忆 C：生命周期状态为 retired
        s.add(
            MemoryItem(
                id="mem_cold_retired",
                content="已被新架构取代并废弃的旧规程",
                status="active",
                user_id="alice",
                decay_score=0.40,
                lifecycle_status=LifecycleStatus.RETIRED,
                created_at=recent_time,
            )
        )
        # 5. 铁律 procedural 记忆：永不衰减，即使久远也不进入冷候选
        s.add(
            MemoryItem(
                id="mem_procedural_rule",
                content="系统核心安全准则：绝不泄露密钥",
                status="active",
                user_id="alice",
                decay_class="procedural",
                decay_score=1.0,
                use_count=0,
                created_at=old_time,
            )
        )
        # 6. Bob 的冷记忆（用于隔离测试）
        s.add(
            MemoryItem(
                id="mem_bob_cold",
                content="Bob 的过期无用账单记录",
                status="active",
                user_id="bob",
                decay_score=0.01,
                created_at=old_time,
            )
        )
        s.commit()

    with session_factory() as s:
        engine = ChenfengEngine(session=s, principal=user_alice)
        candidates = engine.evaluate_cold_candidates(
            decay_threshold=0.05,
            inactive_days=30,
            now=now,
        )

        candidate_ids = {c["id"] for c in candidates}
        assert "mem_cold_decay" in candidate_ids
        assert "mem_cold_idle" in candidate_ids
        assert "mem_cold_retired" in candidate_ids
        # 热数据与铁律绝不封藏
        assert "mem_hot" not in candidate_ids
        assert "mem_procedural_rule" not in candidate_ids
        # 跨租户数据不可见
        assert "mem_bob_cold" not in candidate_ids


def test_archive_cold_memories_and_checkpoint(param_env):
    """Seam 2: 验证批量自动封藏操作、状态变更与快照记录。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem_to_archive_1",
                content="待封存旧记录1",
                status="active",
                user_id="alice",
                decay_score=0.03,
            )
        )
        s.add(
            MemoryItem(
                id="mem_to_archive_2",
                content="待封存旧记录2",
                status="active",
                user_id="alice",
                decay_score=0.01,
            )
        )
        s.commit()

    with session_factory() as s:
        engine = ChenfengEngine(session=s, principal=user_alice)
        res = engine.archive_cold_memories(
            memory_ids=["mem_to_archive_1", "mem_to_archive_2"],
            reason="测试批量封藏",
        )

        assert res["ok"] is True
        assert res["archived_count"] == 2
        assert set(res["archived_ids"]) == {"mem_to_archive_1", "mem_to_archive_2"}

        # 验证数据库状态流转为 archived
        item1 = s.get(MemoryItem, "mem_to_archive_1")
        item2 = s.get(MemoryItem, "mem_to_archive_2")
        assert item1.status == "archived"
        assert item2.status == "archived"

        # 验证产生了 MemoryCheckpoint 快照底本
        cps = s.exec(
            select(MemoryCheckpoint).where(
                MemoryCheckpoint.memory_id.in_(["mem_to_archive_1", "mem_to_archive_2"])
            )
        ).all()
        assert len(cps) == 2
        for cp in cps:
            assert cp.trigger == "chenfeng_archive"
            assert cp.before["status"] == "active"
            assert cp.after["status"] == "archived"


def test_hybrid_search_cold_isolation(param_env):
    """Seam 3: 验证尘封记忆完全从常规检索中隔离。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem_active_lang",
                content="Python 3.12 引入了更强大的泛型语法",
                status="active",
                user_id="alice",
                decay_score=0.9,
            )
        )
        s.add(
            MemoryItem(
                id="mem_cold_lang",
                content="Python 2.7 在 2020 年正式停止支持与维护",
                status="active",
                user_id="alice",
                decay_score=0.02,
            )
        )
        s.commit()

    with session_factory() as s:
        engine = ChenfengEngine(session=s, principal=user_alice)
        engine.archive_cold_memories(memory_ids=["mem_cold_lang"], reason="过时历史归档")

    # 执行混合检索（返回 list[dict]）
    results = hybrid_search(query="Python 语法与支持", top_k=5, principal=user_alice)
    found_ids = {r["memory"]["id"] for r in results}

    assert "mem_active_lang" in found_ids
    assert "mem_cold_lang" not in found_ids


def test_chenfeng_acl_and_tenant_defense(param_env):
    """Seam 4: 验证跨租户行级安全防御。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="bob_private_item",
                content="Bob 的秘密账单",
                status="active",
                user_id="bob",
                decay_score=0.01,
            )
        )
        s.commit()

    with session_factory() as s:
        # Alice 试图封藏 Bob 的记忆
        engine_alice = ChenfengEngine(session=s, principal=user_alice)
        res_hacked = engine_alice.archive_cold_memories(
            memory_ids=["bob_private_item"],
            reason="恶意封藏他人记忆",
        )
        # 应被阻断或未命中，绝不污染他人记忆
        assert res_hacked["archived_count"] == 0
        assert "bob_private_item" in res_hacked.get("failed_ids", [])

        # 检查 Bob 的记忆仍然健康且 active
        bob_item = s.get(MemoryItem, "bob_private_item")
        assert bob_item.status == "active"
