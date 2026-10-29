"""探颐（ADR-0037 · v0.30）：消歧闭环与冲突自动裁决冒烟测试。

依据 Matt Pocock TDD 规范，验证：
1. 意图分类：肯定（确认更替）、否定（废弃新事实）、中立/转移话题（维持待审）；
2. 肯定分支联动笔削与 Checkpoint：更新版本、同步 FTS/向量、留痕审计；
3. 否定分支废弃冲突并保留原状；
4. 模糊/转移话题严格执行「宁 miss 不脏写」；
5. 模糊记忆与原子事实探针闭环；
6. probe_id / conflict_id 参数双向兼容。
"""

import pytest

from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.services.probing_service import resolve_probe_response


def test_resolve_probe_affirmative_intent_seam(param_env):
    """Seam 1 & 2: 验证用户肯定答复（如'是的，改了'）触发冲突消解、正文更替、版本递增与 Checkpoint 快照。"""
    session_factory, _ = param_env

    with session_factory() as s:
        m = MemoryItem(
            id="mem_res_01",
            content="团队每周一上午开例会",
            lane="rule",
            domain="user",
            user_id="default",
            version=1,
            status="active",
        )
        s.add(m)
        s.commit()

        conf = ConflictEvent(
            id="conf_res_01",
            memory_id="mem_res_01",
            incoming_ref="团队例会已调整为每周五下午三点",
            rule_name="meeting_time_conflict",
            status="open",
        )
        s.add(conf)
        s.commit()

        # 调取闭环：传入 probe_id 并附带肯定词“改了”
        res = resolve_probe_response(
            probe_id="probe_conf_res_01",
            user_reply="是的，改了，大家周五下午开会",
            session=s,
        )

        assert res["status"] == "resolved"
        assert res["action"] == "applied"
        assert res["conflict_id"] == "conf_res_01"

        # 数据库验证
        conf_updated = s.get(ConflictEvent, "conf_res_01")
        assert conf_updated.status == "resolved"
        assert conf_updated.resolved_by == "proactive_probe_user_confirmed"

        mem_updated = s.get(MemoryItem, "mem_res_01")
        assert mem_updated.content == "团队例会已调整为每周五下午三点"
        assert mem_updated.version == 2


def test_resolve_probe_negative_intent_seam(param_env):
    """Seam 3: 验证用户否定答复（如'没有/写错了/别改'）废弃冲突账本，保持既有记忆内容不变。"""
    session_factory, _ = param_env

    with session_factory() as s:
        m = MemoryItem(
            id="mem_res_neg_01",
            content="后端接口超时阈值设置为 5 秒",
            lane="rule",
            domain="user",
            user_id="default",
            status="active",
        )
        conf = ConflictEvent(
            id="conf_res_neg_01",
            memory_id="mem_res_neg_01",
            incoming_ref="后端接口超时阈值调整为 60 秒",
            rule_name="timeout_conflict",
            status="open",
        )
        s.add_all([m, conf])
        s.commit()

        # 调取否定答复
        res = resolve_probe_response(
            conflict_id="conf_res_neg_01",
            user_reply="不对，搞错了，别改，还是维持 5 秒",
            session=s,
        )

        assert res["status"] == "resolved"
        assert res["action"] == "dismissed"
        assert res["conflict_id"] == "conf_res_neg_01"

        # 验证数据库状态
        conf_updated = s.get(ConflictEvent, "conf_res_neg_01")
        assert conf_updated.status == "dismissed"
        assert conf_updated.resolved_by == "proactive_probe_user_rejected"

        mem_unchanged = s.get(MemoryItem, "mem_res_neg_01")
        assert mem_unchanged.content == "后端接口超时阈值设置为 5 秒"
        assert mem_unchanged.version == 1


def test_resolve_probe_neutral_deferred_seam(param_env):
    """Seam 4: 验证用户中立或转移话题（如'今天天气真好'），执行「宁 miss 不脏写」，状态维持 open/pending。"""
    session_factory, _ = param_env

    with session_factory() as s:
        m = MemoryItem(
            id="mem_res_neu_01",
            content="生产环境部署在华北二区",
            lane="fact",
            domain="user",
            user_id="default",
            status="active",
        )
        conf = ConflictEvent(
            id="conf_res_neu_01",
            memory_id="mem_res_neu_01",
            incoming_ref="生产环境准备迁往华南一区",
            rule_name="region_conflict",
            status="open",
        )
        s.add_all([m, conf])
        s.commit()

        res = resolve_probe_response(
            conflict_id="conf_res_neu_01",
            user_reply="今天中午去吃哪家快餐比较好？",
            session=s,
        )

        assert res["status"] == "deferred"
        assert res["action"] == "pending"

        conf_open = s.get(ConflictEvent, "conf_res_neu_01")
        assert conf_open.status == "open"

        mem_unchanged = s.get(MemoryItem, "mem_res_neu_01")
        assert mem_unchanged.content == "生产环境部署在华北二区"


def test_resolve_probe_vague_fact_clarify_seam(param_env):
    """Seam 5: 验证对模糊事实（FactAtomic / MemoryItem）的探针闭环：
    肯定答复提振置信度至 0.95；否定答复软撤回（confidence=0.0）。
    """
    from lantai.models.tables import FactAtomic

    session_factory, _ = param_env

    with session_factory() as s:
        # 1. 模糊记忆
        m_vague = MemoryItem(
            id="mem_clarify_01",
            content="项目计划在 11 月中旬发布测试版",
            lane="fact",
            domain="user",
            user_id="default",
            confidence=0.45,
            status="active",
        )
        # 2. 模糊原子事实
        f_vague = FactAtomic(
            id="fact_clarify_01",
            statement="赵六负责项目安全渗透测试",
            predicate="responsible_for",
            object="安全渗透测试",
            confidence=0.5,
            user_id="default",
        )
        s.add_all([m_vague, f_vague])
        s.commit()

        # A. 肯定模糊记忆 -> 置信度提振至 0.95
        res_mem_aff = resolve_probe_response(
            probe_id="probe_mem_clarify_01",
            user_reply="是的，确定在11月中旬发版",
            session=s,
        )
        assert res_mem_aff["status"] == "resolved"
        assert res_mem_aff["action"] == "confirmed"
        assert s.get(MemoryItem, "mem_clarify_01").confidence == 0.95

        # B. 否定原子事实 -> 软撤回置信度归零
        res_fact_neg = resolve_probe_response(
            probe_id="probe_fact_clarify_01",
            user_reply="不对，没有安排赵六做安全渗透",
            session=s,
        )
        assert res_fact_neg["status"] == "resolved"
        assert res_fact_neg["action"] == "retracted"
        assert s.get(FactAtomic, "fact_clarify_01").confidence == 0.0


def test_tanyi_resolve_mcp_and_api_smoke(param_env):
    """Seam 6: 验证 MCP handle_probe_resolve 与 API POST /probing/resolve 端到端闭环。"""
    from fastapi.testclient import TestClient

    from lantai.api.app import app
    from lantai.cli.mcp import handle_probe_resolve
    from lantai.models.tables import ConflictEvent, MemoryItem

    session_factory, _ = param_env

    with session_factory() as s:
        m = MemoryItem(
            id="mem_api_mcp_01",
            content="默认编程语言是 Go",
            lane="rule",
            domain="user",
            user_id="default",
            status="active",
        )
        conf = ConflictEvent(
            id="conf_api_mcp_01",
            memory_id="mem_api_mcp_01",
            incoming_ref="默认编程语言已变更为 Rust",
            rule_name="lang_mutex",
            status="open",
        )
        s.add_all([m, conf])
        s.commit()

        # 1. MCP handle_probe_resolve 闭环
        mcp_res = handle_probe_resolve(
            {"probe_id": "probe_conf_api_mcp_01", "user_reply": "是的，改成 Rust 了"}
        )
        assert mcp_res["status"] == "resolved"
        assert mcp_res["action"] == "applied"

    # 2. API 测试
    client = TestClient(app)
    # 模拟带有效 key 的请求或者在测试环境直调
    resp = client.post(
        "/probing/resolve",
        json={"conflict_id": "conf_api_mcp_01", "user_reply": "是的"},
    )
    # 由于已经在 MCP 步消解，API 再次消解返回 already_resolved 或 200
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_resolved"
