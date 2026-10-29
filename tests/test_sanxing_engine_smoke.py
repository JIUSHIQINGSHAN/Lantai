"""三省·认知层反思自省引擎与记忆健康审视冒烟测试 (v0.37 Ticket 01)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用任何内部逻辑 mock。
验证公共接口 Seams：
1. evaluate_health 多维健康规则扫描：
   - R1: 被取代残留检测（已被 supersedes 但仍为 active）；
   - R2: 时效过期检测（valid_to 已过且非 procedural）；
   - R3: 未决冲突隐患检测（ConflictEvent 状态为 open）；
   - R4: 低帮助度碎片检测（高使用率但 0 采纳/0 帮助）；
2. 规则健全度与铁律豁免：procedural 铁律即使过期也受保护；
3. 行级 ACL 与租户防御：非属主/跨租户记忆严格隔离，不可越权审视；
4. generate_audit_report 审计快照聚合：统计指标与问题清单自洽。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from lantai.cognition.sanxing import (
    SanxingAuditResult,
    SanxingEngine,
    SanxingHealthSnapshot,
    inspect_memory_health,
)
from lantai.core.auth import Principal
from lantai.models.tables import ConflictEvent, MemoryItem


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes or ["fact", "rule", "general"],
        role="admin" if admin else "user",
    )


def test_sanxing_evaluate_health_multi_dimensional_seam(param_env):
    """Seam 1: 验证多维健康规则扫描（R1 被取代残留、R2 时效过期、R3 未决冲突、R4 低帮助度）。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    past_time = now - timedelta(days=10)
    future_time = now + timedelta(days=30)

    with session_factory() as s:
        # 1. 正常健康记忆
        s.add(
            MemoryItem(
                id="mem_healthy",
                content="团队固定每周一上午10点举行周会",
                status="active",
                user_id="alice",
                valid_from=past_time,
                valid_to=future_time,
                use_count=5,
                helpful_count=4,
            )
        )
        # 2. R1: 被取代残留（已标明 superseded_by 但状态仍为 active）
        s.add(
            MemoryItem(
                id="mem_superseded_stale",
                content="旧周会时间：每周二下午3点",
                status="active",
                user_id="alice",
                structure={"superseded_by": "mem_healthy"},
            )
        )
        # 3. R2: 时效过期（valid_to < now）
        s.add(
            MemoryItem(
                id="mem_expired",
                content="临时活动优惠码 PROMO2025 有效期至上周",
                status="active",
                user_id="alice",
                valid_from=past_time - timedelta(days=20),
                valid_to=past_time,
                decay_class="episodic",
            )
        )
        # 4. R2 豁免项: procedural 铁律即使 valid_to 过期也豁免失效判定
        s.add(
            MemoryItem(
                id="mem_procedural_exempt",
                content="安全铁律：生产环境绝不硬编码明文密钥",
                status="active",
                user_id="alice",
                valid_to=past_time,
                decay_class="procedural",
            )
        )
        # 5. R4: 低帮助度碎片（使用次数 >= 5 但 helpful_count == 0）
        s.add(
            MemoryItem(
                id="mem_unhelpful",
                content="某次偶发网络超时的调试建议碎语",
                status="active",
                user_id="alice",
                use_count=6,
                helpful_count=0,
            )
        )
        # 6. R3: Alice 的未决冲突账本记录
        s.add(
            ConflictEvent(
                id="conf_alice_open",
                memory_id="mem_healthy",
                status="open",
                rule_name="mutex_meeting_time",
                created_at=past_time,
            )
        )
        # 7. Bob 的未决冲突与过期记忆（隔离检验）
        s.add(
            MemoryItem(
                id="bob_expired",
                content="Bob 的过期项目密钥",
                status="active",
                user_id="bob",
                valid_to=past_time,
            )
        )
        s.add(
            ConflictEvent(
                id="conf_bob_open",
                memory_id="bob_expired",
                status="open",
                rule_name="mutex_secret_key",
                created_at=past_time,
            )
        )
        s.commit()

    with session_factory() as s:
        engine = SanxingEngine(session=s, principal=user_alice)
        snapshot = engine.evaluate_health(now=now)

        assert isinstance(snapshot, SanxingHealthSnapshot)
        assert snapshot.total_checked >= 4

        # 检验 R1 被取代残留捕获
        superseded_ids = {item["id"] for item in snapshot.superseded_stale}
        assert "mem_superseded_stale" in superseded_ids
        assert "mem_healthy" not in superseded_ids

        # 检验 R2 时效过期捕获及 procedural 铁律豁免
        expired_ids = {item["id"] for item in snapshot.expired_items}
        assert "mem_expired" in expired_ids
        assert "mem_procedural_exempt" not in expired_ids

        # 检验 R3 未决冲突捕获
        conflict_ids = {c["id"] for c in snapshot.open_conflicts}
        assert "conf_alice_open" in conflict_ids
        assert "conf_bob_open" not in conflict_ids

        # 检验 R4 低帮助度捕获
        unhelpful_ids = {item["id"] for item in snapshot.unhelpful_items}
        assert "mem_unhelpful" in unhelpful_ids


def test_sanxing_acl_tenant_defense_seam(param_env):
    """Seam 2: 验证三省自省引擎严格遵守租户防火墙与行级属主隔离。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="bob_stale_secret",
                content="Bob 的内部敏感测试指令",
                status="active",
                user_id="bob",
                structure={"superseded_by": "bob_new"},
            )
        )
        s.commit()

    with session_factory() as s:
        # Alice 执行三省健康审视，绝不可感知或越权检测 Bob 的私密异常
        engine = SanxingEngine(session=s, principal=user_alice)
        snapshot = engine.evaluate_health(now=now)
        all_detected_ids = {
            item["id"]
            for item in (
                snapshot.superseded_stale
                + snapshot.expired_items
                + snapshot.unhelpful_items
            )
        }
        assert "bob_stale_secret" not in all_detected_ids


def test_sanxing_generate_audit_report_seam(param_env):
    """Seam 3: 验证 generate_audit_report 报告生成与快捷函数 inspect_memory_health。"""
    session_factory, _ = param_env
    user_alice = _principal("alice")
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    with session_factory() as s:
        s.add(
            MemoryItem(
                id="alice_stale_cfg",
                content="已废弃的旧集群端口配置",
                status="active",
                user_id="alice",
                valid_to=now - timedelta(days=5),
            )
        )
        s.commit()

    with session_factory() as s:
        # 通过快捷函数调用
        report = inspect_memory_health(principal=user_alice, session=s, now=now)
        assert isinstance(report, dict)
        assert "health_score" in report
        assert "summary" in report
        assert "issues_count" in report
        assert report["issues_count"] >= 1
        assert any(issue["id"] == "alice_stale_cfg" for issue in report["details"])
