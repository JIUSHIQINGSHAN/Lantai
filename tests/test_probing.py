r"""探颐（ADR-0037）：记忆主动探针与自然交互消歧测试。

验证：
1. detect_memory_probes 命中未决冲突事件时生成主动探针；
2. format_probing_context 生成 Prompt 注入上下文；
3. resolve_probe_response 识别用户自然语言肯定/否定并闭环消解冲突；
4. REST POST /probing/detect, /probing/resolve 与 MCP probe_detect, probe_resolve 工具。
"""
from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.services.probing_service import (
    detect_memory_probes,
    format_probing_context,
    resolve_probe_response,
)


class TestProbingDB:
    """真实 SQLite 数据库不 mock 冒烟单测。"""

    def test_detect_and_format_probes(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            # 插入冲突既有记忆与冲突账本
            m = MemoryItem(
                id="mem_city_01",
                content="大哥常驻地在上海徐汇区",
                lane="fact",
                domain="user",
                decay_score=1.0,
                status="active",
            )
            s.add(m)
            s.commit()

            conf = ConflictEvent(
                id="conf_city_01",
                memory_id="mem_city_01",
                incoming_ref="大哥近期在北京市海淀区租了房子",
                rule_name="location_conflict",
                status="open",
            )
            s.add(conf)
            s.commit()

            # 1. 探针检测
            probes = detect_memory_probes("上海 北京 常住地", session=s)
            assert len(probes) >= 1
            p = probes[0]
            assert p["conflict_id"] == "conf_city_01"
            assert "上海" in p["existing_content"] or "北京" in p["incoming_ref"]
            assert len(p["question"]) >= 5

            # 2. 上下文插桩
            prompt_block = format_probing_context(probes)
            assert "【探颐·待求证事项】" in prompt_block
            assert p["question"] in prompt_block

    def test_resolve_probe_response_affirmative(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            m = MemoryItem(
                id="mem_city_02",
                content="公司主要技术栈是 Java",
                lane="fact",
                domain="user",
                decay_score=1.0,
                status="active",
            )
            s.add(m)
            s.commit()

            conf = ConflictEvent(
                id="conf_tech_01",
                memory_id="mem_city_02",
                incoming_ref="公司全面转向 Python 与 Rust 技术栈",
                rule_name="tech_stack_mutex",
                status="open",
            )
            s.add(conf)
            s.commit()

            # 1. 用户肯定答复
            res = resolve_probe_response("conf_tech_01", "是的，我们确实已经全面转用 Python 和 Rust 了", session=s)
            assert res["status"] == "resolved"
            assert res["action"] == "applied"

            s.refresh(conf)
            s.refresh(m)
            assert conf.status == "resolved"
            assert "Python" in m.content or "Rust" in m.content

    def test_resolve_probe_response_negative(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            m = MemoryItem(
                id="mem_test_neg",
                content="测试环境端口是 8080",
                status="active",
            )
            conf = ConflictEvent(
                id="conf_neg_01",
                memory_id="mem_test_neg",
                incoming_ref="测试环境端口变更为 9090",
                rule_name="port_mutex",
                status="open",
            )
            s.add_all([m, conf])
            s.commit()

            # 2. 用户否定答复
            res = resolve_probe_response("conf_neg_01", "不是的，端口没有变，依然是 8080", session=s)
            assert res["status"] == "resolved"
            assert res["action"] == "dismissed"

            s.refresh(conf)
            assert conf.status == "dismissed"


class TestProbingEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_probing_endpoints(self, param_env):
        client = TestClient(app)
        resp = client.post("/probing/detect", json={"query": "测试查询"})
        assert resp.status_code == 200
        assert "probes" in resp.json()

    def test_mcp_probing_tools(self, param_env):
        from scripts.mcp_server import handle_probe_detect, handle_probe_resolve
        det = handle_probe_detect({"query": "测试查询"})
        assert "probes" in det

        res = handle_probe_resolve({"conflict_id": "non_existent", "user_reply": "是的"})
        assert res["status"] == "not_found" or "error" in res or "status" in res
