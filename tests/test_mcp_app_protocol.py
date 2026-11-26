"""MCP Apps stdio protocol contract and frozen MCP tool surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from lantai.cli import mcp
from tests.support.harness_state import env_context

ROOT = Path(__file__).resolve().parents[1]
UI_EXTENSION = "io.modelcontextprotocol/ui"
UI_MIME = "text/html;profile=mcp-app"
UI_RESOURCE = "ui://lantai/memory-panel"
DEFAULT_TOOLS = {
    "add",
    "core_memory_get",
    "feedback",
    "mem_recent",
    "offload_read",
    "search",
}
UI_TOOLS = {"search", "core_memory_get", "mem_recent", "crystals_list", "crystal_decide"}


def _initialize_frame(*, supports_ui: bool) -> dict:
    extensions = {UI_EXTENSION: {"mimeTypes": [UI_MIME]}} if supports_ui else {}
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"capabilities": {"extensions": extensions}, "clientInfo": {"name": "test-host"}},
    }


def _run_stdio(
    tmp_path: Path,
    *,
    supports_ui: bool,
    profile: str | None = None,
    read_resources: tuple[str, ...] = (UI_RESOURCE,),
    extra_frames: tuple[dict, ...] = (),
) -> dict[int, dict]:
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["LANTAI_HOME"] = str(home)
    env["REMEMBRANCE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env["DATABASE_URL"] = f"sqlite:///{(home / 'remembrance.db').as_posix()}"
    env["CHROMADB_PATH"] = str(home / ".chromadb")
    env.pop("API_KEY", None)
    env.pop("RERANKER_API_KEY", None)
    env.pop("LANTAI_MCP_PROFILE", None)
    env.pop("MCP_TOOL_PROFILE", None)
    if profile is not None:
        env["LANTAI_MCP_PROFILE"] = profile

    frames = [
        _initialize_frame(supports_ui=supports_ui),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
    ]
    frames.extend(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "resources/read",
            "params": {"uri": uri},
        }
        for request_id, uri in enumerate(read_resources, start=4)
    )
    frames.extend(extra_frames)
    # Keep the existing wall; a stalled child must leave evidence of where it waits.
    bootstrap = (
        "import faulthandler; faulthandler.dump_traceback_later(20, repeat=True); "
        "from lantai.cli.mcp import main; main(); "
        "faulthandler.cancel_dump_traceback_later()"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", bootstrap],
            input="\n".join(json.dumps(frame) for frame in frames) + "\n",
            capture_output=True,
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        trace = error.stderr or b""
        if isinstance(trace, bytes):
            trace = trace.decode("utf-8", errors="replace")
        pytest.fail(f"MCP stdio exceeded 60 seconds; child traceback:\n{trace[-6000:]}")
    assert proc.returncode == 0, proc.stderr[-2000:]
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert all(response.get("jsonrpc") == "2.0" for response in responses)
    response_ids = [response["id"] for response in responses]
    expected_ids = {frame["id"] for frame in frames if "id" in frame}
    assert len(response_ids) == len(set(response_ids)), "MCP 子进程返回了重复响应"
    assert set(response_ids) == expected_ids, "MCP 子进程遗漏或多发响应"
    return {response["id"]: response for response in responses}


def test_negotiated_app_client_gets_ui_resource_and_closed_csp(tmp_path: Path) -> None:
    responses = _run_stdio(tmp_path, supports_ui=True)
    initialized = responses[1]["result"]
    assert "resources" in initialized["capabilities"]

    tools = responses[2]["result"]["tools"]
    assert {tool["name"] for tool in tools} == DEFAULT_TOOLS
    assert len(mcp.TOOLS) == 96
    for tool in tools:
        assert tool["inputSchema"] == mcp.TOOLS[tool["name"]]["inputSchema"]
        if tool["name"] in UI_TOOLS:
            assert tool["_meta"]["ui"]["resourceUri"] == UI_RESOURCE

    resources = responses[3]["result"]["resources"]
    assert [resource["uri"] for resource in resources] == [UI_RESOURCE]
    assert resources[0]["mimeType"] == UI_MIME

    content = responses[4]["result"]["contents"][0]
    assert content["uri"] == UI_RESOURCE
    assert content["mimeType"] == UI_MIME
    assert content["_meta"]["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": [],
        "baseUriDomains": [],
    }
    assert "兰台记忆" in content["text"]
    assert "https://" not in content["text"]
    assert "http://" not in content["text"]


def test_unnegotiated_client_sees_no_app_resource_or_tool_ui(tmp_path: Path) -> None:
    responses = _run_stdio(tmp_path, supports_ui=False)
    tools = responses[2]["result"]["tools"]
    assert {tool["name"] for tool in tools} == DEFAULT_TOOLS
    assert all("ui" not in tool.get("_meta", {}) for tool in tools)
    assert responses[3]["result"]["resources"] == []
    assert responses[4]["error"]["code"] == -32602


def test_full_profile_links_crystal_panel_without_changing_tool_schemas(tmp_path: Path) -> None:
    responses = _run_stdio(tmp_path, supports_ui=True, profile="full")
    tools = {tool["name"]: tool for tool in responses[2]["result"]["tools"]}
    assert len(tools) == 96
    assert set(tools) == set(mcp.TOOLS)
    for name, original in mcp.TOOLS.items():
        assert tools[name]["inputSchema"] == original["inputSchema"], name
    for name in ("crystals_list", "crystal_decide"):
        assert tools[name]["_meta"]["ui"]["resourceUri"] == UI_RESOURCE


def test_full_profile_registers_xuanjing_app_through_stdio_protocol(tmp_path: Path) -> None:
    xuanjing_uri = "ui://lantai/xuanjing-observatory"
    responses = _run_stdio(
        tmp_path,
        supports_ui=True,
        profile="full",
        read_resources=(UI_RESOURCE, xuanjing_uri),
    )

    tools = responses[2]["result"]["tools"]
    xuanjing_tool = next(tool for tool in tools if tool["name"] == "get_xuanjing_graph")
    assert xuanjing_tool["inputSchema"] == mcp.TOOLS["get_xuanjing_graph"]["inputSchema"]
    assert xuanjing_tool["_meta"]["ui"] == {
        "resourceUri": xuanjing_uri,
        "visibility": ["model", "app"],
    }

    resources = responses[3]["result"]["resources"]
    resource_uris = [resource["uri"] for resource in resources]
    assert resource_uris == [UI_RESOURCE, xuanjing_uri]
    assert len(resource_uris) == len(set(resource_uris))
    assert resources[1]["mimeType"] == UI_MIME

    content = responses[5]["result"]["contents"][0]
    assert content["uri"] == xuanjing_uri
    assert content["mimeType"] == UI_MIME
    assert "ui/initialize" in content["text"]
    assert content["_meta"]["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": [],
        "baseUriDomains": [],
    }


def test_unnegotiated_full_profile_keeps_xuanjing_tool_plain(tmp_path: Path) -> None:
    responses = _run_stdio(tmp_path, supports_ui=False, profile="full")

    tools = responses[2]["result"]["tools"]
    xuanjing_tool = next(tool for tool in tools if tool["name"] == "get_xuanjing_graph")
    assert "ui" not in xuanjing_tool.get("_meta", {})
    assert responses[3]["result"]["resources"] == []
    assert responses[4]["error"]["code"] == -32602


def test_core_profile_cannot_read_unlisted_xuanjing_resource(tmp_path: Path) -> None:
    xuanjing_uri = "ui://lantai/xuanjing-observatory"
    responses = _run_stdio(
        tmp_path,
        supports_ui=True,
        read_resources=(xuanjing_uri,),
    )

    assert [resource["uri"] for resource in responses[3]["result"]["resources"]] == [UI_RESOURCE]
    assert responses[4]["error"] == {
        "code": -32602,
        "message": "unknown resource",
    }


def test_default_profile_stays_exactly_six_without_app_negotiation(tmp_path: Path) -> None:
    responses = _run_stdio(tmp_path, supports_ui=False)
    names = {tool["name"] for tool in responses[2]["result"]["tools"]}
    assert names == DEFAULT_TOOLS
    assert len(names) == 6
    assert "crystals_list" not in names
    assert "crystal_decide" not in names


@pytest.mark.parametrize("profile", ["core", "full"])
def test_concurrent_connections_keep_app_negotiation_and_registry_isolated(profile):
    start = Barrier(2)

    def discover(supports_ui):
        session = mcp.McpAppSession()
        assert mcp.handle(_initialize_frame(supports_ui=supports_ui), session=session)["result"]
        start.wait(timeout=10)
        tools = mcp.handle({"id": 2, "method": "tools/list"}, session=session)["result"]["tools"]
        resources = mcp.handle({"id": 3, "method": "resources/list"}, session=session)["result"]
        return tools, resources["resources"]

    with env_context("LANTAI_MCP_PROFILE", profile), ThreadPoolExecutor(max_workers=2) as executor:
        app = executor.submit(discover, True)
        plain = executor.submit(discover, False)
        app_tools, app_resources = app.result(timeout=10)
        plain_tools, plain_resources = plain.result(timeout=10)

    assert {tool["name"] for tool in app_tools} == {tool["name"] for tool in plain_tools}
    assert any("ui" in tool.get("_meta", {}) for tool in app_tools)
    assert all("ui" not in tool.get("_meta", {}) for tool in plain_tools)
    assert app_resources and plain_resources == []
    app_tools[0]["inputSchema"]["caller-owned"] = True
    assert all("caller-owned" not in tool["inputSchema"] for tool in mcp.TOOLS.values())
    assert all("ui" not in tool.get("_meta", {}) for tool in mcp.TOOLS.values())


def test_stdio_invalid_method_and_hidden_tool_leave_session_alive(tmp_path):
    responses = _run_stdio(
        tmp_path,
        supports_ui=True,
        profile="core",
        extra_frames=(
            {"jsonrpc": "2.0", "id": 5, "method": None},
            {"jsonrpc": "2.0", "id": 6, "method": []},
            {"jsonrpc": "2.0", "id": None, "method": None},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "mem_stats"}},
            {"jsonrpc": "2.0", "id": 8, "method": "ping"},
        ),
    )
    assert responses[5]["error"]["code"] == responses[6]["error"]["code"] == -32600
    assert responses[None]["error"]["code"] == -32600
    assert responses[7]["error"]["code"] == -32602
    assert responses[8]["result"] == {}
