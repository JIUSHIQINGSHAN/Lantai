"""双宿主入口契约：真实进程边界，内部逻辑不 mock。"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes-plugin" / "lantai-hook" / "__init__.py"


@pytest.fixture
def load_hermes():
    loaded = []

    def load():
        spec = importlib.util.spec_from_file_location("adapter_hermes", PLUGIN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        loaded.append(mod)
        return mod

    yield load
    for mod in loaded:
        if mod._proc is not None:
            mod._proc.kill()
            mod._proc.wait(timeout=5)


@pytest.fixture
def responder(tmp_path):
    script = tmp_path / "responder.py"
    script.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    data = json.loads(line)\n"
        "    print(json.dumps({'context': os.environ.get('LANTAI_HOST', '') "
        "+ ':' + data.get('query', ''), 'event_id': 'evt_1', "
        "'evidence': [{'id': 'mem_1'}]}, ensure_ascii=False), flush=True)\n",
        encoding="utf-8",
    )
    return script


def test_hermes_child_uses_own_host_even_when_parent_is_codex(monkeypatch, responder, load_hermes):
    monkeypatch.setenv("LANTAI_HOST", "codex")
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    mod = load_hermes()
    result = mod._on_pre_llm_call(user_message="部署方案", session_id="s1", platform="cli")
    assert result == {"context": "hermes:\n\nhermes:部署方案"}


def test_hermes_explicit_bad_hook_never_falls_back(monkeypatch, tmp_path, load_hermes):
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(tmp_path / "missing.py"))
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    mod = load_hermes()
    hooks = {}

    class Host:
        def register_hook(self, name, callback):
            hooks[name] = callback

    mod.register(Host())
    assert hooks == {}
    assert mod._proc is None


@pytest.mark.parametrize("bad", [[], {}, 123, "bad\nidentity", "x" * 129])
def test_hermes_bad_session_fails_closed(monkeypatch, responder, bad, load_hermes):
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    mod = load_hermes()
    assert mod._on_pre_llm_call(user_message="部署方案", session_id=bad) is None
    mod._on_session_end(session_id=bad)
    assert mod._session_buffers == {}
    assert mod._proc is None


def test_hermes_native_multimodal_input_preserves_only_user_text(
    monkeypatch, responder, load_hermes
):
    monkeypatch.setenv("LANTAI_PYTHON", sys.executable)
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    mod = load_hermes()
    result = mod._on_pre_llm_call(
        user_message=[
            {"type": "text", "text": "部署方案"},
            {"type": "image_url", "image_url": {"url": "data:image/private"}},
            {"type": "text", "text": "下一步"},
        ],
        session_id="multimodal",
    )
    assert result == {"context": "hermes:\n\nhermes:部署方案\n下一步"}
    assert mod._session_buffers["multimodal"][0]["text"] == "部署方案\n下一步"


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "invalid"])
def test_hermes_invalid_wall_is_bounded(monkeypatch, responder, load_hermes, value):
    monkeypatch.setenv("LANTAI_SHELL_HOOK", str(responder))
    monkeypatch.setenv("SHELL_HOOK_TIMEOUT", value)
    monkeypatch.setenv("SHELL_HOOK_DIALOGUE_TIMEOUT", value)
    mod = load_hermes()
    assert (mod._REQUEST_TIMEOUT, mod._DIALOGUE_TIMEOUT) == (5.0, 35.0)


def test_openclaw_native_contracts(tmp_path):
    """Run the native JS public entry and bridge against a real Python NDJSON peer."""
    node = shutil.which("node")
    assert node, "OpenClaw contract tests require Node.js"
    script = tmp_path / "peer.py"
    script.write_text(
        "import json, os, sys, time\n"
        "for line in sys.stdin:\n"
        "    data = json.loads(line)\n"
        "    with open(os.environ['LANTAI_ADAPTER_TEST_LOG'], 'a', encoding='utf-8') as f:\n"
        "        f.write(json.dumps(data, ensure_ascii=False) + '\\n')\n"
        "    query = data.get('query', '')\n"
        "    if query == 'timeout': time.sleep(10)\n"
        "    if query == 'exit': sys.exit(0)\n"
        "    if query == 'malformed':\n"
        "        print('not-json', flush=True)\n"
        "        continue\n"
        "    if query == 'array':\n"
        "        print('[]', flush=True)\n"
        "        continue\n"
        "    if data.get('type') == 'backfill' and data.get('event_id') == 'bad-ack':\n"
        "        print('not-json', flush=True)\n"
        "        continue\n"
        "    if query == 'oversize':\n"
        "        print('x' * 1100000, flush=True)\n"
        "        continue\n"
        "    result = {'ok': True, 'host': os.environ.get('LANTAI_HOST'),\n"
        "        'owner': os.environ.get('LANTAI_HOOK_USER_ID'),\n"
        "        'role': os.environ.get('LANTAI_HOOK_ROLE'), 'echo': data}\n"
        "    if data.get('type') == 'backfill': result['receipt_status'] = 'acked'\n"
        "    if not data.get('type'):\n"
        "        result.update(context='<memory>' + query + '</memory>',\n"
        "            event_id='bad-ack' if query == 'bad-ack' else 'rev_' + data.get('session_id', ''),\n"
        "            evidence=[{'id': 'mem_1'}, {'id': 'mem_1'}, {'id': None}])\n"
        "    print(json.dumps(result, ensure_ascii=False), flush=True)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        LANTAI_TEST_PYTHON=sys.executable,
        LANTAI_TEST_HOOK=str(script),
        LANTAI_ADAPTER_TEST_LOG=str(tmp_path / "frames.jsonl"),
    )
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests/test_adapter_hermes_openclaw_node.mjs")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    (tmp_path / "node-contracts.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_openclaw_real_lantai_injection_and_receipt(tmp_path):
    """Native plugin -> real shell hook -> real SQLite/Chroma, replacing external LLM only."""
    from tests.test_host_matrix import _child_env, _seed_memory

    home = tmp_path / "home"
    home.mkdir()
    env = _child_env(home, "hermes")
    init = subprocess.run(
        [sys.executable, "-c", "from lantai.storage.db import init_db; init_db()"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert init.returncode == 0, init.stderr
    _seed_memory(home, "真实双宿主冒烟：龙井茶冲泡水温85度", "mem_native_bridge")
    # Bridge strips PYTHONPATH by design. A test-only wrapper installs ONLY the external-network peer.
    wrapper = tmp_path / "real_hook.py"
    wrapper.write_text(
        f"import os, runpy, sys\nsys.path.insert(0, {str(ROOT)!r})\n"
        "os.environ['LANTAI_TEST_STUB_EMBED'] = '1'\n"
        f"runpy.run_path({str(ROOT / 'tests/support/host_matrix_stub/sitecustomize.py')!r})\n"
        f"runpy.run_path({str(ROOT / 'scripts/shell_hook.py')!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    config = {
        "python": sys.executable,
        "shellHook": str(wrapper),
        "userId": "default",
        "agentId": "main",
        "channels": ["webchat"],
        "sessionKeys": ["agent:main:main"],
    }
    code = """
import assert from 'node:assert/strict';
import plugin from './integrations/openclaw-lantai/index.mjs';
const hooks = new Map(); let service;
plugin.register({ pluginConfig: JSON.parse(process.argv[1]),
  on(name, handler) { hooks.set(name, handler); },
  registerService(value) { service = value; }, logger: { warn() {} } });
const ctx = {agentId:'main', channel:'webchat', sessionKey:'agent:main:main',
  sessionId:'native-real-session', runId:'native-real-run'};
try {
  const result = await hooks.get('before_prompt_build')({prompt:'龙井茶冲泡水温是多少'}, ctx);
  assert.ok(result?.prependContext.includes('龙井茶冲泡水温85度'), JSON.stringify(result));
  await hooks.get('agent_end')({success:true}, ctx);
} finally { await service.stop(); }
"""
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", code, json.dumps(config)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from sqlmodel import select; "
            "from lantai.storage.db import get_session; from lantai.models.tables import RetrievalEvent; "
            "s=get_session(); rows=s.exec(select(RetrievalEvent).where("
            "RetrievalEvent.session_id=='native-real-session')).all(); "
            "print(json.dumps([{'status':r.receipt_status,'used_ids':r.used_ids} for r in rows]))",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout.strip()) == [
        {"status": "acked", "used_ids": ["mem_native_bridge"]}
    ]
