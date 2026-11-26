"""Client entry contracts; real subprocesses, no host configuration writes."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.test_host_matrix import _child_env, _seed_memory, hook_home

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "integrations" / "mirasim-lantai"
CONFIGURE = BUNDLE / "configure.py"
CORE = {"add", "core_memory_get", "feedback", "mem_recent", "offload_read", "search"}


def configure(host, home, *extra):
    return subprocess.run(
        [sys.executable, str(CONFIGURE), "--host", host, "--home", str(home), *extra],
        cwd=home.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_packaged_mcp_entry_is_portable_and_explicitly_minimal():
    config = json.loads((BUNDLE / ".mcp.json").read_text(encoding="utf-8"))
    assert config["mcpServers"] == {
        "lantai": {
            "command": "lantai-mcp",
            "args": [],
            "env": {"PYTHONUTF8": "1", "LANTAI_MCP_PROFILE": "core"},
        }
    }


@pytest.mark.parametrize("host", ["claude-code", "codex", "dsh"])
def test_generated_configuration_routes_to_same_minimal_entry(tmp_path, host):
    home = tmp_path / "data with spaces"
    completed = configure(host, home)
    assert completed.returncode == 0, completed.stderr
    assert not home.exists(), "preview must not create data directories"
    files = json.loads(completed.stdout)
    if host == "claude-code":
        server = json.loads(files[".mcp.json"])["mcpServers"]["lantai"]
    elif host == "codex":
        server = tomllib.loads(files[".codex/config.toml"])["mcp_servers"]["lantai"]
    else:
        row = json.loads(files["cordis.patch.yml"])[0]["insert"][0]
        assert row["disabled"] is True
        assert row["name"] == "@local/dsh-lantai-attribution"
        server = row["config"]
        assert server["transport"] == "stdio"
        assert server["serverName"] == "lantai"
    assert Path(server["command"]) == Path(sys.executable)
    assert Path(server["args"][0]) == BUNDLE / "entry.py"
    assert server["args"][1:] == ["mcp", "--host", host]
    assert server["env"]["LANTAI_MCP_PROFILE"] == "core"
    assert server["env"]["LANTAI_HOME"] == str(home)
    assert "API_KEY" not in server["env"], "credentials must stay outside generated files"


def test_codex_configuration_preserves_non_bmp_unicode_paths(tmp_path):
    home = tmp_path / "data-\U0001f4c1"
    completed = configure("codex", home)
    assert completed.returncode == 0, completed.stderr
    files = json.loads(completed.stdout)
    server = tomllib.loads(files[".codex/config.toml"])["mcp_servers"]["lantai"]
    assert server["env"]["LANTAI_HOME"] == str(home)
    assert not home.exists()


@pytest.mark.parametrize("host", ["claude-code", "codex", "dsh"])
def test_generated_entry_drives_real_stdio_and_keeps_default_contract(tmp_path, host):
    home = tmp_path / "data"
    completed = configure(host, home)
    assert completed.returncode == 0, completed.stderr
    files = json.loads(completed.stdout)
    if host == "claude-code":
        server = json.loads(files[".mcp.json"])["mcpServers"]["lantai"]
    elif host == "codex":
        server = tomllib.loads(files[".codex/config.toml"])["mcp_servers"]["lantai"]
    else:
        server = json.loads(files["cordis.patch.yml"])[0]["insert"][0]["config"]
    env = dict(os.environ, **server["env"])
    env["API_KEY"] = "test-adapter-key"
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "core_memory_put",
                "arguments": {},
            },
        },
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    process = subprocess.run(
        [server["command"], *server["args"]],
        input="\n".join(json.dumps(frame) for frame in frames) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        timeout=90,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [item["id"] for item in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["serverInfo"]["name"] == "lantai"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == CORE
    assert responses[2]["error"]["code"] == -32602
    assert responses[3]["result"] == {}


def test_plugin_manifests_resolve_same_mcp_and_existing_skill():
    manifests = [
        json.loads((BUNDLE / name).read_text(encoding="utf-8"))
        for name in ("plugin.json", ".claude-plugin/plugin.json")
    ]
    assert manifests[0]["name"] == manifests[1]["name"] == "lantai"
    assert manifests[0]["version"] == manifests[1]["version"] == "1.1.0"
    for manifest in manifests:
        assert (BUNDLE / manifest["mcpServers"]).is_file()
        assert manifest["skills"] == ["./skills/lantai-memory"]
        skill = (BUNDLE / manifest["skills"][0] / "SKILL.md").read_text(encoding="utf-8")
        assert "core_memory_get" in skill
        assert "LANTAI_MCP_PROFILE=full" in skill


@pytest.mark.parametrize("host", ["claude-code", "codex", "dsh"])
def test_export_matches_preview_and_refuses_existing_output(tmp_path, host):
    home = tmp_path / "home"
    output = tmp_path / "review"
    preview = configure(host, home)
    exported = configure(host, home, "--output", str(output))
    assert preview.returncode == exported.returncode == 0
    assert json.loads(preview.stdout) == json.loads(exported.stdout)
    for name, content in json.loads(preview.stdout).items():
        assert (output / name).read_text(encoding="utf-8") == content
    sentinel = output / "keep.txt"
    sentinel.write_text("user-owned", encoding="utf-8")
    refused = configure(host, home, "--output", str(output))
    assert refused.returncode == 2
    assert refused.stdout == ""
    assert sentinel.read_text(encoding="utf-8") == "user-owned"


def test_public_builder_is_pure_and_rejects_invalid_configuration(tmp_path):
    spec = importlib.util.spec_from_file_location("client_config_builder", CONFIGURE)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    home = tmp_path / "data"
    assert ".mcp.json" in adapter.build_bundle("claude-code", home)
    assert not home.exists()
    for kwargs in (
        {"host": "other", "home": home},
        {"host": "codex", "home": Path("relative")},
        {"host": "codex", "home": home, "python": tmp_path / "missing-python"},
        {"host": "dsh", "home": home, "hooks": True},
    ):
        with pytest.raises(ValueError):
            adapter.build_bundle(**kwargs)
    with pytest.raises(ValueError, match="inside output"):
        adapter.write_bundle({"../escape": "bad"}, tmp_path / "review")
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("name", ["data&command", "data%ADAPTER%"])
def test_hook_configuration_handles_shell_expansion_paths(tmp_path, name):
    completed = configure("codex", tmp_path / name, "--hooks")
    if os.name == "nt":
        assert completed.returncode == 2
        assert completed.stdout == ""
        assert "shell" in completed.stderr
    else:
        assert completed.returncode == 0, completed.stderr
        files = json.loads(completed.stdout)
        server = tomllib.loads(files[".codex/config.toml"])["mcp_servers"]["lantai"]
        assert server["env"]["LANTAI_HOME"] == str(tmp_path / name)
    assert not (tmp_path / name).exists()


@pytest.mark.parametrize("missing", ["API_KEY", "LANTAI_HOME"])
def test_missing_runtime_configuration_stops_before_service_import(tmp_path, missing):
    env = dict(os.environ, API_KEY="synthetic-secret", LANTAI_HOME=str(tmp_path / "data"))
    env.pop(missing)
    process = subprocess.run(
        [sys.executable, str(BUNDLE / "entry.py"), "mcp", "--host", "codex"],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        timeout=20,
    )
    assert process.returncode == 2
    assert missing in process.stderr
    assert process.stdout == ""
    assert "synthetic-secret" not in process.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_generated_hook_command_injects_and_acknowledges_real_memory(request, host):
    home = request.getfixturevalue("hook_home")
    mem_id = f"adapter-{host}"
    _seed_memory(home, "入口适配专用记忆 龙井茶冲泡水温 85度", mem_id)
    completed = configure(host, home, "--hooks")
    assert completed.returncode == 0, completed.stderr
    files = json.loads(completed.stdout)
    if host == "codex":
        assert tomllib.loads(files[".codex/config.toml"])["features"]["hooks"] is True
    path = ".claude/settings.json" if host == "claude-code" else ".codex/hooks.json"
    hooks = json.loads(files[path])["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit"}
    command = hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
    env = _child_env(home, "wrong-host")
    # sitecustomize imports settings before entry.py; declare its documented budget first.
    env["SHELL_HOOK_TIMEOUT"] = "8"

    def call(frame):
        process = subprocess.run(
            command,
            shell=True,
            input=json.dumps(frame, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=home.parent,
            timeout=90,
        )
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)

    response = call(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "龙井茶冲泡水温是多少",
            "session_id": f"adapter-session-{host}",
        }
    )
    injected = response["hookSpecificOutput"]
    assert injected["hookEventName"] == "UserPromptSubmit"
    assert "85度" in injected["additionalContext"]
    assert mem_id in {item["id"] for item in response["evidence"]}
    ack = call({"type": "backfill", "event_id": response["event_id"], "used_ids": [mem_id]})
    assert ack.get("receipt_status") == "acked", ack


def test_generated_dsh_entry_through_installed_registry_and_sdk(tmp_path):
    if not os.environ.get("DSH_NODE_EXECUTABLE") or not os.environ.get("DSH_INSTALL_DIR"):
        pytest.skip("select installed DSH runtime for real registry/SDK verification")
    executable = os.environ["DSH_NODE_EXECUTABLE"]
    home = tmp_path / "data"
    home.mkdir()
    completed = configure("dsh", home)
    assert completed.returncode == 0, completed.stderr
    files = json.loads(completed.stdout)
    config = json.loads(files["cordis.patch.yml"])[0]["insert"][0]["config"]
    # Only synthetic credentials enter the child; no profile or deployed conf is read.
    config["env"].update(
        {
            "API_KEY": "test-adapter-key",
            "OPENAI_API_KEY": "test-key",
            "PYTHONPATH": str(ROOT),
            "OPENAI_BASE_URL": "http://127.0.0.1:1/v1",
        }
    )
    fixture = tmp_path / "dsh-entry.json"
    fixture.write_text(json.dumps(config), encoding="utf-8")
    init = subprocess.run(
        [sys.executable, "-c", "from lantai.storage.db import init_db; init_db()"],
        env=dict(os.environ, **config["env"]),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert init.returncode == 0, init.stderr
    process = subprocess.run(
        [
            executable,
            str(ROOT / "integrations/dsh-lantai/tests/run-installed.cjs"),
            "adapter-entry.test.js",
            str(fixture),
        ],
        env=dict(os.environ, ELECTRON_RUN_AS_NODE="1"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "adapter-entry: verified" in process.stdout
