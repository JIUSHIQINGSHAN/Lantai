"""MCP 默认最小暴露安全门禁（票 05 续31，验收底线 5）。

病灶（实测，非推测——探针 `.scratch/gate/r31/` 与本文件负向对照互为印证）：
`lantai/cli/mcp.py::_get_active_tools()` 的默认档直接 `return TOOLS`——不设
`LANTAI_MCP_PROFILE` 时 `tools/list` 返回全量 96/96，且 `tools/call` 的分发面
（`handle()` 对 `active_tools` 判 membership）随之放行全部 96 个工具的真实执行。
评审线 N-04/C-15/R-02 记录该暴露面 69→94→96 一路涨，从未收窄。

本文件是修复的红→绿锁，四条判据各咬一个面：

1. **默认面 = 最小集**：不设任何 profile 变量时，活跃面恰好等于 `HERMES_CORE_TOOLS`
   （核心集，见 `HERMES_CORE_TOOLS`），绝不等于全量注册表。
2. **未知取值 fail-closed**：`safe`/`none`/拼错的档位此前**静默回落全量**——一个
   大小写笔误就把暴露面从 5 放回 96。修复后未知/非法取值必须落到最小集，
   并且**可观测地告警**（不许静默）。
3. **显式全量必须是显式动作**：`LANTAI_MCP_PROFILE=full` 这类明确声明才允许全量
   ——「要 96 个工具」得有人白纸黑字选它，不能是没人配置时的惯性结果。
4. **分发面与广告面同源**：`tools/call` 对默认档之外的工具必须拒（-32602
   unknown tool），handler 不得执行——收窄的不是列表观感，是可执行攻击面。

不 mock 口径：全部直调生产 `_get_active_tools()` / `handle()`；分发面判据用一个
真实注册的工具（`shishi_vault`，默认档之外）走完整 `handle()` 链路，只在其
handler 上打「有没有被调到」的桩——桩是**观测器**，不是被替换的计算逻辑。
profile 变量用登记式写入器 `tests/support/harness_state.env_context` 隔离，
绝不裸写 os.environ（G2/AST 扫描锁盯着）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lantai.cli import mcp
from tests.support.harness_state import env_context


def _active_set() -> set[str]:
    return set(mcp._get_active_tools())


@pytest.fixture(autouse=True)
def _isolated_profiles():
    with env_context("LANTAI_MCP_PROFILE", None), env_context("MCP_TOOL_PROFILE", None):
        yield


def test_default_active_face_is_minimal_not_full():
    """判据 1：什么都不设 ⇒ 活跃面 == 核心集，且必须显著小于全量注册表。"""
    active = _active_set()
    assert active == set(mcp.HERMES_CORE_TOOLS), f"默认活跃面不是最小核心集：{sorted(active)}"
    assert len(active) < len(mcp.TOOLS), (
        f"默认活跃面等于全量（{len(active)}/{len(mcp.TOOLS)}）——默认最小暴露被改回去了"
    )


def test_unknown_profile_fails_closed_to_minimal_and_warns():
    """判据 2：未知/非法档位（safe/none/大小写笔误）必须落最小集，不得静默全量。

    两条都要咬：(a) 活跃面 fail-closed 到核心集；(b) 可观测地告警——`warnings` 走
    warnings 系统不走 logging，故用 `pytest.warns`（进 catch_warnings/simplefilter
    always，不受同位置去重影响）。抽掉 `warnings.warn` 调用这条会红，正是续21「测了个
    空转」教训的防复发：只断言回落不断言告警，等于没钉住「不静默」。
    """
    for bad in ("safe", "none", "typo-value", "HERMES-TYPO"):
        with env_context("LANTAI_MCP_PROFILE", bad), pytest.warns(UserWarning, match="fail-closed"):
            assert _active_set() == set(mcp.HERMES_CORE_TOOLS), (
                f"未知档位 {bad!r} 没有 fail-closed 到最小集——暴露面悄悄放回全量"
            )


def test_explicit_full_is_the_only_way_to_get_full_registry():
    """判据 3：全量只许显式声明（profile=full），没人配置时拿不到全量。"""
    assert len(_active_set()) == len(mcp.HERMES_CORE_TOOLS)
    with env_context("LANTAI_MCP_PROFILE", "full"):
        assert _active_set() == set(mcp.TOOLS), "显式 full 档应给全量注册表"


def test_tools_list_default_face_matches_active_registry():
    """判据 4a：`tools/list` 的响应面 == `_get_active_tools()`（广告与真相同源）。"""
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    advertised = {t["name"] for t in resp["result"]["tools"]}
    assert advertised == _active_set(), (
        f"tools/list 与活跃面不一致：广告 {sorted(advertised)} vs 活跃 {sorted(_active_set())}"
    )
    assert advertised == set(mcp.HERMES_CORE_TOOLS)


def test_tools_call_refuses_non_core_tool_and_never_runs_handler():
    """判据 4b：默认档之外的工具调用必须被拒且 handler 一行不执行。

    用真实注册表里的 `shishi_vault`（核心集之外）走完整 `handle()` 分发链路。
    `patch.dict` 只把 handler 换成「调用即留痕」的观测桩——被检验的是 handle() 的
    membership 判定本身，不是 handler 的业务逻辑，因此不构成「mock 掉失败路径」。
    """
    from unittest.mock import MagicMock, patch

    assert "shishi_vault" in mcp.TOOLS, "前提：该工具在全量注册表里"
    assert "shishi_vault" not in mcp.HERMES_CORE_TOOLS, "前提：该工具在默认档之外"
    spy = MagicMock(name="shishi_vault_spy")
    with patch.dict(mcp.TOOL_HANDLERS, {"shishi_vault": spy}):
        resp = mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "shishi_vault", "arguments": {}},
            }
        )
    assert not spy.called, "默认档之外的工具 handler 被真实执行了——收窄只改了列表没改分发面"
    assert resp["error"]["code"] == -32602, f"默认档之外的工具必须按 unknown tool 拒绝，实得 {resp}"


def test_core_tools_still_callable_under_default():
    """反向保险：收窄不许把核心工具自己也关在门外。"""
    with env_context("LANTAI_MCP_PROFILE", "core"):
        for name in mcp.HERMES_CORE_TOOLS:
            assert name in _active_set(), f"核心工具 {name} 被错杀"


@pytest.mark.parametrize("method", [None, 0, False, [], {}])
def test_invalid_method_returns_protocol_error_without_killing_dispatch(method):
    response = mcp.handle({"jsonrpc": "2.0", "id": "invalid-method", "method": method})
    assert response["id"] == "invalid-method"
    assert response["error"]["code"] == -32600
    assert mcp.handle({"jsonrpc": "2.0", "id": "next", "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": "next",
        "result": {},
    }


@pytest.mark.parametrize(
    "primary,alias,full,warns",
    [
        (None, None, False, False),
        (None, "full", True, False),
        ("", "full", True, False),
        ("  ", "full", False, False),
        (" CoRe ", "full", False, False),
        (" Full ", "core", True, False),
        ("typo", "full", False, True),
        (None, "typo", False, True),
    ],
)
@pytest.mark.usefixtures("core_service_state")
def test_profile_priority_applies_to_discovery_and_real_dispatch(primary, alias, full, warns):
    from contextlib import nullcontext

    with env_context("LANTAI_MCP_PROFILE", primary), env_context("MCP_TOOL_PROFILE", alias):
        warning_context = pytest.warns(UserWarning, match="fail-closed") if warns else nullcontext()
        with warning_context:
            listed = mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
            response = mcp.handle(
                {"id": 2, "method": "tools/call", "params": {"name": "mem_stats", "arguments": {}}}
            )
        assert {tool["name"] for tool in listed} == (
            set(mcp.TOOLS) if full else set(mcp.HERMES_CORE_TOOLS)
        )
        if full:
            assert "error" not in response
            assert isinstance(json.loads(response["result"]["content"][0]["text"]), dict)
        else:
            assert response["error"] == {"code": -32602, "message": "unknown tool: mem_stats"}
            assert mcp._guard_instance is None, "拒绝调用不应初始化守卫或进入 handler"


def test_every_hidden_tool_is_rejected_before_business_dispatch():
    for request_id, name in enumerate(sorted(set(mcp.TOOLS) - mcp.HERMES_CORE_TOOLS)):
        response = mcp.handle(
            {"id": request_id, "method": "tools/call", "params": {"name": name, "arguments": {}}}
        )
        assert response["error"] == {"code": -32602, "message": f"unknown tool: {name}"}
    assert mcp._guard_instance is None


def test_default_profile_test_restores_ambient_values_before_next_test(tmp_path):
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "child-home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        LANTAI_HOME=str(home),
        REMEMBRANCE_HOME=str(home),
        DATABASE_URL=f"sqlite:///{(home / 'remembrance.db').as_posix()}",
        CHROMADB_PATH=str(home / ".chromadb"),
        LANTAI_MCP_PROFILE="full",
        MCP_TOOL_PROFILE="slim",
        PYTHONPATH=str(root),
    )
    bootstrap = """
import os
import sys
import pytest

class ProfileAudit:
    leaks = []

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        yield
        values = (os.environ.get('LANTAI_MCP_PROFILE'), os.environ.get('MCP_TOOL_PROFILE'))
        if values != ('full', 'slim'):
            self.leaks.append((item.nodeid, values))

audit = ProfileAudit()
result = pytest.main(sys.argv[1:], plugins=[audit])
print('PROFILE_LEAKS=' + repr(audit.leaks))
raise SystemExit(int(result) or int(bool(audit.leaks)))
"""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "tests/test_mcp_default_minimal_exposure.py::test_default_active_face_is_minimal_not_full",
            "--basetemp",
            str(tmp_path / "child-pytest"),
            "-q",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert "1 passed" in proc.stdout
    assert "PROFILE_LEAKS=[]" in proc.stdout
