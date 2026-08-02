"""Shell Hook 契约测试：超时与静默降级。"""
import importlib.util
import io
import json
import os
import time

import pytest

HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "shell_hook.py")


def _load_hook(monkeypatch, embed_delay=0.0, timeout=0.2):
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Patch settings timeout (singleton already loaded)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", timeout)

    # Patch os._exit to raise SystemExit (so pytest can catch it)
    def fake_exit(code=0):
        raise SystemExit(code)
    monkeypatch.setattr(mod.os, "_exit", fake_exit)

    def slow_embed(texts):
        time.sleep(embed_delay)
        return [[0.1] * 8]

    monkeypatch.setattr(mod, "embed", slow_embed)
    return mod


def test_build_context_short_query_returns_empty(monkeypatch):
    mod = _load_hook(monkeypatch)
    assert mod.build_context("你好") == {}


def test_main_timeout_returns_empty_json(monkeypatch, capsys):
    mod = _load_hook(monkeypatch, embed_delay=1.0, timeout=0.2)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"query": "这是一个超过三字符的查询"})))
    start = time.perf_counter()
    with pytest.raises(SystemExit):
        mod.main()
    elapsed = time.perf_counter() - start
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {}
    assert elapsed < 2.0  # 硬超时生效（远小于 embed 的 5s）
