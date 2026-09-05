"""札记（ADR-0032）：Working Memory Scratchpad 测试。

验证：
1. SessionScratchpad 表写入、查询与覆盖更新；
2. 1000 字符超长截断安全（宁 miss 不脏写）；
3. scratchpad_service 格式化与底本协同注入；
4. REST 路由 GET/POST /scratchpad/{session_id} 与 MCP scratchpad_get / scratchpad_write 工具。
"""
from fastapi.testclient import TestClient

from api_server import app
from lantai.services.scratchpad_service import (
    format_scratchpad_context,
    get_scratchpad,
    write_scratchpad,
)


class TestScratchpadServiceDB:
    """真实 SQLite 数据库不 mock 冒烟测试。"""

    def test_scratchpad_crud_and_truncation(self, param_env):
        session_factory, _ = param_env
        session_id = "sess_smoke_01"

        with session_factory() as s:
            # 1. 初始查询应为空
            assert get_scratchpad(session_id, session=s) == ""

            # 2. 首次写入
            res1 = write_scratchpad(session_id, "当前调试任务: 401降级; 端口: 8990", session=s)
            assert res1["content"] == "当前调试任务: 401降级; 端口: 8990"
            assert get_scratchpad(session_id, session=s) == "当前调试任务: 401降级; 端口: 8990"

            # 3. 覆盖写入
            res2 = write_scratchpad(session_id, "当前调试任务: 401降级; 端口修改为 8995", session=s)
            assert res2["content"] == "当前调试任务: 401降级; 端口修改为 8995"
            assert get_scratchpad(session_id, session=s) == "当前调试任务: 401降级; 端口修改为 8995"

            # 4. 超长截断测试（1000 字符上限）
            long_text = "A" * 1200
            res3 = write_scratchpad(session_id, long_text, session=s)
            assert len(res3["content"]) == 1000

    def test_format_scratchpad_context(self, param_env):
        session_factory, _ = param_env
        session_id = "sess_smoke_02"

        with session_factory() as s:
            write_scratchpad(session_id, "排查 master 分支构建", session=s)
            ctx = format_scratchpad_context(session_id, session=s)
            assert "【札记 (Scratchpad)】" in ctx
            assert "排查 master 分支构建" in ctx


class TestScratchpadEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_scratchpad(self, param_env):
        client = TestClient(app)

        # 写入
        r1 = client.post("/scratchpad/default", json={"content": "REST 札记写入测试"})
        assert r1.status_code == 200
        assert r1.json()["content"] == "REST 札记写入测试"

        # 读取
        r2 = client.get("/scratchpad/default")
        assert r2.status_code == 200
        assert r2.json()["content"] == "REST 札记写入测试"

    def test_mcp_scratchpad_tools(self, param_env):
        from scripts.mcp_server import handle_scratchpad_get, handle_scratchpad_write

        # 写入
        w_res = handle_scratchpad_write({"session_id": "mcp_sess", "content": "MCP 札记写入测试"})
        assert w_res["content"] == "MCP 札记写入测试"

        # 读取
        g_res = handle_scratchpad_get({"session_id": "mcp_sess"})
        assert g_res["content"] == "MCP 札记写入测试"
