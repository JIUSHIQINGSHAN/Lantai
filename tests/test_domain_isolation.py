"""辨域（ADR-0034）：User-Session-Agent 三维硬隔离与域分治测试。

验证：
1. MemoryItem 新增 domain 字段与自适应默认映射；
2. hybrid_search 支持 domain 精确过滤与全域召回；
3. REST POST /search 与 MCP search 工具支持 domain 参数。
"""
from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search


class TestDomainIsolationDB:
    """真实 SQLite 数据库不 mock 冒烟测试。"""

    def test_domain_filtering_in_hybrid_search(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            # 插入不同域的记忆
            m_user = MemoryItem(
                id="mem_user_01",
                user_id="u1",
                content="大哥喜欢喝龙井茶",
                lane="preference",
                domain="user",
                decay_score=1.0,
            )
            m_session = MemoryItem(
                id="mem_session_01",
                user_id="u1",
                content="本次调试会话的临时端口是 8995",
                lane="experience",
                domain="session",
                decay_score=1.0,
            )
            m_agent = MemoryItem(
                id="mem_agent_01",
                user_id="u1",
                content="核心函数必须至少编写一个不 mock 的冒烟测试",
                lane="rule",
                domain="agent",
                decay_score=1.0,
            )
            s.add_all([m_user, m_session, m_agent])
            s.commit()
            
            from lantai.storage.fts import sync_fts
            sync_fts(s, m_user.id, m_user.content)
            sync_fts(s, m_session.id, m_session.content)
            sync_fts(s, m_agent.id, m_agent.content)
            s.commit()

            # 1. 过滤 user 域
            res_user = hybrid_search("茶 端口 冒烟测试", session=s, domain="user")
            res_ids = [m.get("id") or m.get("memory", {}).get("id") for m in res_user]
            assert "mem_user_01" in res_ids
            assert "mem_session_01" not in res_ids
            assert "mem_agent_01" not in res_ids

            # 2. 过滤 session 域
            res_sess = hybrid_search("茶 端口 冒烟测试", session=s, domain="session")
            res_ids = [m.get("id") or m.get("memory", {}).get("id") for m in res_sess]
            assert "mem_session_01" in res_ids
            assert "mem_user_01" not in res_ids
            assert "mem_agent_01" not in res_ids

            # 3. 过滤 agent 域
            res_agent = hybrid_search("茶 端口 冒烟测试", session=s, domain="agent")
            res_ids = [m.get("id") or m.get("memory", {}).get("id") for m in res_agent]
            assert "mem_agent_01" in res_ids
            assert "mem_user_01" not in res_ids
            assert "mem_session_01" not in res_ids

            # 4. 全域召回 (domain=None)
            res_all = hybrid_search("茶 端口 冒烟测试", session=s, domain=None)
            res_ids = [m.get("id") or m.get("memory", {}).get("id") for m in res_all]
            assert "mem_user_01" in res_ids
            assert "mem_session_01" in res_ids
            assert "mem_agent_01" in res_ids


class TestDomainEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_search_with_domain(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            m = MemoryItem(
                id="mem_user_rest",
                user_id="default",
                content="华硕天选三搭载 RTX 3050 显卡",
                domain="user",
                decay_score=1.0,
            )
            s.add(m)
            s.commit()
            from lantai.storage.fts import sync_fts
            sync_fts(s, m.id, m.content)
            s.commit()

        client = TestClient(app)
        # 查询 user 域 (force=True 绕过闸门)
        resp = client.post("/search", json={"query": "RTX 3050", "domain": "user", "force": True})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(i.get("id") == "mem_user_rest" or i.get("memory", {}).get("id") == "mem_user_rest" for i in results)

        # 查询 agent 域（应为空）
        resp_agent = client.post("/search", json={"query": "RTX 3050", "domain": "agent", "force": True})
        assert resp_agent.status_code == 200
        results_agent = resp_agent.json()["results"]
        assert not any(i.get("id") == "mem_user_rest" or i.get("memory", {}).get("id") == "mem_user_rest" for i in results_agent)

    def test_mcp_search_with_domain(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            m = MemoryItem(
                id="mem_agent_mcp",
                user_id="default",
                content="记住：必须遵守宁 miss 不脏写戒律准则",
                domain="agent",
                decay_score=1.0,
            )
            s.add(m)
            s.commit()
            
            from lantai.storage.fts import sync_fts
            sync_fts(s, m.id, m.content)
            s.commit()

        from scripts.mcp_server import handle_search
        res = handle_search({"query": "记住：戒律准则", "domain": "agent", "force": True})
        assert any(i.get("id") == "mem_agent_mcp" or i.get("memory", {}).get("id") == "mem_agent_mcp" for i in res["results"])

        res_user = handle_search({"query": "记住：戒律准则", "domain": "user", "force": True})
        assert not any(i.get("id") == "mem_agent_mcp" or i.get("memory", {}).get("id") == "mem_agent_mcp" for i in res_user["results"])
