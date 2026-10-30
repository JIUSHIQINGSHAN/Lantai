"""底本（Diben）核心定本锚定与意图漂移分析引擎冒烟测试 (Ticket 01)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测核心引擎，不 mock 存储与分词计算；
- 覆盖 4 个公共接缝：
  1. 会话定本提炼（SessionAnchor）与结构化提取；
  2. 意图延续与漂移偏转判定（analyze_drift）；
  3. 樊篱 wrap_as_data 隔离标记防护；
  4. 租户与属主行级 ACL 隔离（跨租户底本定本禁止读取与推演）。
"""

from __future__ import annotations

import pytest

from lantai.cognition.diben import DibenEngine, DriftAnalysis, SessionAnchor
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import SessionCheckpoint
from lantai.storage import db


def _seed_test_checkpoint(
    session_id: str = "sess-diben-001",
    user_id: str = "user-diben-1",
    tenant_id: str | None = "tenant-diben-a",
) -> None:
    t = utcnow()
    with db.get_session() as s:
        blocks = {
            "cp_active_intent": "重构分布式网关限流与熔断架构",
            "cp_next_action": "在 Kubernetes 上配置 EnvoyFilter 速率限制",
            "cp_current_work": "编写 ingress-nginx 到 Envoy 的迁移测试用例",
            "cp_key_decisions": "采用 Envoy 官方限流服务取代原 Lua 脚本",
            "cp_open_notes": "压测验证突发流量下的 P99 延迟指标",
        }
        for k, v in blocks.items():
            cp = SessionCheckpoint(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                block_key=k,
                content=v,
                created_at=t,
            )
            s.add(cp)
        s.commit()


class TestDibenEngineSmoke:
    """Diben 核心引擎 4 大接缝冒烟测试集。"""

    def test_seam1_synthesize_anchor(self):
        """Seam 1: 会话定本提炼（SessionAnchor）与结构化提取。"""
        p = Principal(user_id="user-diben-1", tenant_id="tenant-diben-a")
        engine = DibenEngine(principal=p)

        blocks = {
            "cp_active_intent": "部署 PostgreSQL 高可用 Patroni 集群",
            "cp_next_action": "配置 etcd 键值存储心跳检查",
            "cp_current_work": "调整 patroni.yml 中的 DCS 超时参数",
            "cp_key_decisions": "选用 3 节点 raft 拓扑确保仲裁法定人数",
            "cp_open_notes": "验证脑裂自愈与主备自动切换耗时",
        }

        anchor = engine.synthesize_anchor(session_id="sess-patroni-99", blocks=blocks)
        assert anchor["session_id"] == "sess-patroni-99"
        assert anchor["active_intent"] == "部署 PostgreSQL 高可用 Patroni 集群"
        assert anchor["next_action"] == "配置 etcd 键值存储心跳检查"
        assert len(anchor["key_decisions"]) == 1
        assert "3 节点" in anchor["key_decisions"][0]
        assert anchor["user_id"] == "user-diben-1"
        assert anchor["tenant_id"] == "tenant-diben-a"

    def test_seam2_analyze_drift(self):
        """Seam 2: 意图延续与漂移偏转判定（analyze_drift）。"""
        _seed_test_checkpoint(session_id="sess-drift-test", user_id="user-drift-test")
        p = Principal(user_id="user-drift-test", tenant_id="tenant-diben-a")
        engine = DibenEngine(principal=p)

        # 1. 意图延续请求（同网关限流与 Envoy 相关）
        cont_res = engine.analyze_drift(
            current_query="请帮我检查 EnvoyFilter 限流配置的速率参数",
            session_id="sess-drift-test",
        )
        assert cont_res["is_continuation"] is True
        assert cont_res["continuity_score"] > 0.15
        assert cont_res["drift_level"] in ("none", "slight")
        assert "延续" in cont_res["drift_reason"]

        # 2. 意图漂移请求（完全不相关的厨艺/菜谱话题）
        drift_res = engine.analyze_drift(
            current_query="周末做红烧肉需要准备哪些调料和大料",
            session_id="sess-drift-test",
        )
        assert drift_res["is_continuation"] is False
        assert drift_res["continuity_score"] <= 0.1
        assert drift_res["drift_level"] == "significant"
        assert "漂移" in drift_res["drift_reason"] or "偏离" in drift_res["drift_reason"]

    def test_seam3_fencing_protection(self):
        """Seam 3: 樊篱 wrap_as_data 隔离标记防护。"""
        _seed_test_checkpoint(session_id="sess-fencing-test", user_id="user-fencing-test")
        p = Principal(user_id="user-fencing-test", tenant_id="tenant-diben-a")
        engine = DibenEngine(principal=p)

        anchor = engine.get_active_anchor(session_id="sess-fencing-test")
        assert anchor is not None
        assert "fenced_content" in anchor
        assert '<memory_data id="anchor-sess-fencing-test"' in anchor["fenced_content"]
        assert "</memory_data>" in anchor["fenced_content"]

        drift = engine.analyze_drift(
            current_query="EnvoyFilter 限流策略",
            session_id="sess-fencing-test",
        )
        assert "fenced_reason" in drift
        assert '<memory_data id="' in drift["fenced_reason"]
        assert "</memory_data>" in drift["fenced_reason"]

    def test_seam4_row_level_acl_isolation(self):
        """Seam 4: 租户与属主行级 ACL 隔离（跨租户底本定本禁止读取与推演）。"""
        _seed_test_checkpoint(
            session_id="sess-tenant-a-only",
            user_id="user-tenant-a",
            tenant_id="tenant-alpha",
        )

        # 租户 Beta 用户访问
        p_beta = Principal(user_id="user-tenant-b", tenant_id="tenant-beta")
        engine_beta = DibenEngine(principal=p_beta)

        # 1. 尝试获取租户 Alpha 的底本 -> 应当返回 None
        anchor_b = engine_beta.get_active_anchor(session_id="sess-tenant-a-only")
        assert anchor_b is None

        # 2. 尝试基于租户 Alpha 的 session 分析意图 -> 降级为无激活底本的安全提示
        drift_b = engine_beta.analyze_drift(
            current_query="继续执行 EnvoyFilter 部署",
            session_id="sess-tenant-a-only",
        )
        assert drift_b["drift_level"] == "none"
        assert "无激活底本" in drift_b["drift_reason"]
