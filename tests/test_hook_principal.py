"""钩子检索属主（票 90）不 mock 冒烟。

病灶（2026-11-24 真机验收实证）：`build_context` 调 `hybrid_search` 不传
`principal`，收敛成 `user_id="default"`。真实库 265/276 条记忆的属主是
`api_key`（HTTP 环境密钥的 admin 形态，auth.py:149），钩子一条都看不见——
只剩 9 条可见，其中 8 条是 `A`/`B` 测试数据。检索「通了」但实际空转。

修法：显式配置 `LANTAI_HOOK_USER_ID` / `LANTAI_HOOK_ROLE`，不猜身份。
空值保持旧行为（None），并对既有调用零漂移。
"""

import importlib.util
import os

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


def _load_hook(monkeypatch, user_id: str = "", role: str = "user"):
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_USER_ID", user_id)
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_ROLE", role)
    return mod


def test_empty_config_keeps_old_behavior(monkeypatch):
    """未配置 → None（hybrid 收敛成 default），旧行为零漂移。"""
    mod = _load_hook(monkeypatch, user_id="")
    assert mod._hook_principal() is None


def test_whitespace_only_config_treated_as_empty(monkeypatch):
    """纯空白不当作身份（宁 miss 不脏写）。"""
    mod = _load_hook(monkeypatch, user_id="   ")
    assert mod._hook_principal() is None


def test_configured_user_builds_principal(monkeypatch):
    """配置了 user_id → 构造出正确属主的 Principal。"""
    mod = _load_hook(monkeypatch, user_id="api_key", role="admin")
    p = mod._hook_principal()
    assert p is not None
    assert p.user_id == "api_key"
    assert p.role == "admin"
    assert bool(p.is_admin) is True


def test_non_admin_role_stays_scoped(monkeypatch):
    """role 默认 user → is_admin False，仍受归属过滤（不给管理员口径）。"""
    mod = _load_hook(monkeypatch, user_id="alice", role="user")
    p = mod._hook_principal()
    assert p.user_id == "alice"
    assert bool(p.is_admin) is False


def test_verbatim_switch_none_keeps_global(monkeypatch):
    """三元开关 None → 不传 params（沿用全局 settings），旧行为零漂移。"""
    mod = _load_hook(monkeypatch, user_id="", role="user")
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_VERBATIM_IN_RECALL", None)
    captured = {}

    def fake_hybrid(query, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(mod, "relevance_check", lambda q, user_id=None: {"needs_memory": True})
    monkeypatch.setattr(mod, "_build_resident_persona", lambda: "画像")
    mod.build_context("任意查询内容", session_id="s1")

    assert "params" not in captured, "None 时不该传 params，否则会覆盖全局配置"


def test_verbatim_switch_true_overrides_params(monkeypatch):
    """显式 True → 传 params 且 verbatim_in_recall=True（真实库业务记忆全靠这一档）。"""
    mod = _load_hook(monkeypatch, user_id="", role="user")
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_VERBATIM_IN_RECALL", True)
    captured = {}

    def fake_hybrid(query, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(mod, "relevance_check", lambda q, user_id=None: {"needs_memory": True})
    monkeypatch.setattr(mod, "_build_resident_persona", lambda: "画像")
    mod.build_context("任意查询内容", session_id="s1")

    params = captured.get("params")
    assert params is not None, "显式 True 时应传 params"
    assert params.verbatim_in_recall is True


def test_principal_reaches_hybrid_search(monkeypatch):
    """配置的属主必须真的传到 hybrid_search（真调用链，不 mock 逻辑）。"""
    mod = _load_hook(monkeypatch, user_id="api_key", role="admin")
    captured = {}

    def fake_hybrid(query, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(mod, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(mod, "relevance_check", lambda q, user_id=None: {"needs_memory": True})
    monkeypatch.setattr(mod, "_build_resident_persona", lambda: "画像")

    mod.build_context("InstaBox 探针是什么", session_id="s1")

    principal = captured.get("principal")
    assert principal is not None, "配置的属主没传到 hybrid_search"
    assert principal.user_id == "api_key"
    assert bool(principal.is_admin) is True


def test_unconfigured_call_keeps_legacy_signature(monkeypatch):
    """未配置时按**旧签名**调用（不传 principal/params）——既有替身零漂移。

    回归锚点：2026-11-24 首版修法无条件传 `principal=`，打挂了
    `tests/test_mokan_items_budget_smoke.py`（其替身签名只收
    `query, top_k, budget_s`），钩子整体异常 → build_context 返回 {} → 注入全丢。
    """
    mod = _load_hook(monkeypatch, user_id="", role="user")
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_VERBATIM_IN_RECALL", None)

    def legacy_sig_hybrid(query, top_k=5, budget_s=None):
        # 严格旧签名：多一个关键字就 TypeError，模拟既有评测替身
        return []

    monkeypatch.setattr(mod, "hybrid_search", legacy_sig_hybrid)
    monkeypatch.setattr(mod, "relevance_check", lambda q, user_id=None: {"needs_memory": True})
    monkeypatch.setattr(mod, "_build_resident_persona", lambda: "画像")

    out = mod.build_context("任意查询内容", session_id="s1")
    # 没崩、走了画像兜底路径即为通过（零命中分支）
    assert "context" in out
