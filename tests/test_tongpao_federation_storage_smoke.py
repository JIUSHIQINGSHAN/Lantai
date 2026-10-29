"""同袍·多智能体记忆联邦分级存储与公共知识殿堂冒烟测试 (Ticket 01)

基于真实 in-memory SQLite，不使用 mock。
遵循 ADR-0058（同袍）设计决策：
1. MemoryItem 新增 shared_scope 字段（默认 "private"，支持 "swarm_shared"）；
2. 数据库迁移链 v35 增量迁移支持；
3. 多 Agent 检索硬隔离：
   - Agent A 的 private 记忆，Agent B 无法检索；
   - Agent A 的 swarm_shared 记忆，Agent B 正常召回共享；
   - 跨租户物理隔离防线：异租户 Agent 无法跨租户读取 swarm_shared 记忆；
4. REST API 与 MCP 工具支持 shared_scope 读写透传。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.core.acl import Principal
from lantai.models.schemas import AddMemoryReq
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.services.memory_service import add_memory


def _agent_principal(agent_id: str, user_id: str = "u1", tenant_id: str = "t1") -> Principal:
    return Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        role="user",
        allowed_lanes=["general", "fact", "rule"],
    )


def test_memory_item_shared_scope_default_and_persistence(param_env):
    """验证 MemoryItem 对 shared_scope 字段的定义与持久化"""
    session_factory, _ = param_env
    with session_factory() as s:
        m1 = MemoryItem(
            id="mem_priv_01",
            content="Agent A 的私有密钥与独立执行状态",
            user_id="u1",
            agent_id="agent-A",
            shared_scope="private",
            lane="fact",
        )
        m2 = MemoryItem(
            id="mem_shared_01",
            content="Swarm 集群公共规约：所有子智能体遵守宁 miss 不脏写原则",
            user_id="u1",
            agent_id="agent-A",
            shared_scope="swarm_shared",
            lane="rule",
        )
        s.add_all([m1, m2])
        s.commit()

        # 读取验证
        ret_m1 = s.get(MemoryItem, "mem_priv_01")
        assert ret_m1 is not None
        assert ret_m1.shared_scope == "private"

        ret_m2 = s.get(MemoryItem, "mem_shared_01")
        assert ret_m2 is not None
        assert ret_m2.shared_scope == "swarm_shared"


def test_federated_hybrid_search_agent_isolation_and_sharing(param_env):
    """验证 hybrid_search 在同袍机制下的私有隔离与公共知识共享"""
    session_factory, _ = param_env

    p_agent_a = _agent_principal("agent-A", user_id="user-1", tenant_id="tenant-1")
    p_agent_b = _agent_principal("agent-B", user_id="user-1", tenant_id="tenant-1")
    p_agent_c_other_tenant = _agent_principal("agent-C", user_id="user-2", tenant_id="tenant-2")

    with session_factory() as s:
        # 种子 1：Agent A 的私有记忆
        m_a_priv = MemoryItem(
            id="mem_a_priv",
            content="Agent A 独占任务中间计算状态缓存",
            user_id="user-1",
            tenant_id="tenant-1",
            agent_id="agent-A",
            shared_scope="private",
            lane="fact",
            decay_score=1.0,
        )
        # 种子 2：Agent A 贡献的公共知识殿堂记忆
        m_a_shared = MemoryItem(
            id="mem_a_shared",
            content="Swarm 协作法门：数据同步前需调用勘合原子锁进行一致性校验",
            user_id="user-1",
            tenant_id="tenant-1",
            agent_id="agent-A",
            shared_scope="swarm_shared",
            lane="rule",
            decay_score=1.0,
        )
        s.add_all([m_a_priv, m_a_shared])
        s.commit()

        from lantai.storage.fts import sync_fts

        sync_fts(s, m_a_priv.id, m_a_priv.content)
        sync_fts(s, m_a_shared.id, m_a_shared.content)
        s.commit()

        # 1. Agent A 检索：应同时召回自身私有记忆与公共记忆
        res_a = hybrid_search("任务中间状态 协作法门", session=s, principal=p_agent_a)
        ids_a = [m.get("id") or m.get("memory", {}).get("id") for m in res_a]
        assert "mem_a_priv" in ids_a
        assert "mem_a_shared" in ids_a

        # 2. Agent B 检索：只能召回公共记忆 mem_a_shared，严格隔离 mem_a_priv
        res_b = hybrid_search("任务中间状态 协作法门", session=s, principal=p_agent_b)
        ids_b = [m.get("id") or m.get("memory", {}).get("id") for m in res_b]
        assert "mem_a_shared" in ids_b
        assert "mem_a_priv" not in ids_b

        # 3. 跨租户 Agent C 检索：即使是 swarm_shared 也被租户边界阻断
        res_c = hybrid_search("任务中间状态 协作法门", session=s, principal=p_agent_c_other_tenant)
        ids_c = [m.get("id") or m.get("memory", {}).get("id") for m in res_c]
        assert "mem_a_priv" not in ids_c
        assert "mem_a_shared" not in ids_c

        # 4. 指定 shared_scope="swarm_shared" 检索
        res_shared_only = hybrid_search(
            "任务 协作法门", session=s, principal=p_agent_a, shared_scope="swarm_shared"
        )
        ids_shared_only = [m.get("id") or m.get("memory", {}).get("id") for m in res_shared_only]
        assert "mem_a_shared" in ids_shared_only
        assert "mem_a_priv" not in ids_shared_only


def test_add_raw_memory_with_shared_scope(param_env):
    """验证原文直存 add_raw_memory 写入 shared_scope 与 agent_id"""
    session_factory, _ = param_env
    p_agent = _agent_principal("agent-X", user_id="u1", tenant_id="t1")

    from lantai.models.schemas import RawMemoryReq
    from lantai.services.memory_service import add_raw_memory

    req = RawMemoryReq(
        content="全体 Agent 必须在会话结束前萃取会话精华（含英咀华）",
        lane="rule",
        shared_scope="swarm_shared",
    )
    res = add_raw_memory(
        req,
        user_id=p_agent.user_id,
        tenant_id=p_agent.tenant_id,
        agent_id=p_agent.agent_id,
        principal=p_agent,
    )
    assert res.get("memory_id") is not None

    with session_factory() as s:
        item = s.get(MemoryItem, res["memory_id"])
        assert item is not None
        assert item.shared_scope == "swarm_shared"
        assert item.agent_id == "agent-X"


def test_api_and_mcp_shared_scope_integration(param_env):
    """验证 REST API 与 MCP 工具在同袍机制下的兼容与调用"""
    session_factory, _ = param_env

    # 1. 种子入库
    with session_factory() as s:
        m_shared = MemoryItem(
            id="mem_mcp_shared",
            content="同袍公共知识：分布式向量召回超时重试必须遵循指数退避",
            user_id="default",
            agent_id="agent-1",
            shared_scope="swarm_shared",
            lane="rule",
            decay_score=1.0,
        )
        s.add(m_shared)
        s.commit()
        from lantai.storage.fts import sync_fts

        sync_fts(s, m_shared.id, m_shared.content)
        s.commit()

    # 2. REST API /search 传递 shared_scope
    client = TestClient(app)
    resp = client.post(
        "/search",
        json={
            "query": "指数退避 向量召回",
            "shared_scope": "swarm_shared",
            "force": True,
        },
    )
    assert resp.status_code == 200
    res_items = resp.json()["results"]
    assert any(
        i.get("id") == "mem_mcp_shared" or i.get("memory", {}).get("id") == "mem_mcp_shared"
        for i in res_items
    )

    # 3. MCP handle_search 传递 shared_scope
    from lantai.cli.mcp import handle_search

    mcp_res = handle_search(
        {
            "query": "指数退避 向量召回",
            "shared_scope": "swarm_shared",
            "force": True,
        }
    )
    assert any(
        i.get("id") == "mem_mcp_shared" or i.get("memory", {}).get("id") == "mem_mcp_shared"
        for i in mcp_res["results"]
    )
