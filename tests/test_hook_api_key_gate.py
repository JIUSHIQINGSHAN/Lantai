"""第二道闸:shell hook 必须配置 API_KEY,禁止 DEV MODE 回退。

真实 Settings,只改 API_KEY。拒绝必须抛错(不静默返回空,否则宿主会误判正常)。
"""

import importlib.util
from pathlib import Path

import pytest

from lantai.core.settings import settings

_HOOK = Path(__file__).parent.parent / "scripts" / "shell_hook.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("shell_hook_gate_mod", _HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hook_refuses_when_api_key_empty(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    hook = _load_hook()
    with pytest.raises(hook.HookAuthError, match="未配置 API_KEY"):
        hook._handle_one('{"type": "checkpoint"}')


def test_hook_allows_when_api_key_set(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "some-key")
    hook = _load_hook()
    assert hook._handle_one('{"type": "checkpoint"}') == {}
