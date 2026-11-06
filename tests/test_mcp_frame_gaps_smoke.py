"""MCP 广告工具真执行补口（v0.68 测试诚实性审计补强）。

审计结论：`crystal_promote`（同袍晋升）与 `recall_report`（零召回监控）
在 TOOLS 里广告、TOOL_HANDLERS 里登记，但 tests/ 里**没有任何一处**以
JSON-RPC 帧驱动过——「广告 ⇔ handler」结构护栏
（test_every_advertised_tool_has_handler）只查对应关系，不执行。

本文件按 test_mcp.py 的帧口径真调 handle()，handler 与其后端服务全部
真实执行（仅 conftest 既定外部替身）。模块用 exec 副本加载（_load_mcp，
test_mcp.py 同款）：handle() 会实例化 LoopGuard 单例，直连真模块会弄脏
conftest「模块级生产状态」护栏通道（_guard_instance）。两工具都不在默认
核心档（HERMES_CORE_TOOLS），故显式声明全量档。
"""

import importlib.util
import json
from pathlib import Path

import pytest

MCP_PATH = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"

pytestmark = pytest.mark.mcp_full_surface()


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server_frame_gaps", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(mod, name, arguments, mid=1):
    return mod.handle(
        {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def test_recall_report_frame_smoke(param_env):
    """recall_report 帧：真调 observability.recall_report，空库返回监控报告。"""
    mod = _load_mcp()
    resp = _call(mod, "recall_report", {})
    assert "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert isinstance(payload, dict)


def test_recall_report_rejects_bad_days(param_env):
    """days 越界在 schema 层即拒（-32602 invalid params），不落到 handler。"""
    mod = _load_mcp()
    resp = _call(mod, "recall_report", {"days": 0}, mid=2)
    assert "error" in resp, resp
    assert resp["error"]["code"] == -32602


def test_crystal_promote_frame_promotes_eligible_crystal(param_env):
    """crystal_promote 帧：合格结晶真晋升——status=promoted + 公共殿堂资产 + leads_to 边。"""
    from sqlmodel import select

    from lantai.models.tables import MemoryEdge, MemoryItem, SkillCrystal

    session_factory, _ = param_env
    crystal_id = "crystal-frame-smoke-01"
    with session_factory() as s:
        s.add(
            SkillCrystal(
                id=crystal_id,
                user_id="crystal-frame-smoke",
                skill_name="帧驱动晋升冒烟技能",
                procedure="第一步：校验\n第二步：晋升",
                success_count=1,
            )
        )
        s.commit()

    mod = _load_mcp()
    resp = _call(mod, "crystal_promote", {"crystal_id": crystal_id, "steps": ["校验", "晋升"]})
    assert "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload.get("ok") is True, payload

    with session_factory() as s:
        crystal = s.get(SkillCrystal, crystal_id)
        assert crystal.status == "promoted"
        assert crystal.shared_scope == "swarm_shared"
        shared = s.exec(select(MemoryItem).where(MemoryItem.shared_scope == "swarm_shared")).all()
        assert shared, "公共殿堂 MemoryItem 未落库"
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.relation == "leads_to")).all()
        assert edges, "leads_to 溯源边未落库"
