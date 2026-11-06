"""注入帧点名的 MCP 工具，必须在默认档里真能调（巡检 2026-11-06 22:00）。

病灶（实测）：票 05 续31（`ff1dd12d`）把 MCP 默认档收窄到 `HERMES_CORE_TOOLS`（5 个），
但 `scripts/shell_hook.py` 给超长记忆生成的注入行仍然写着
「…（已卸载全文；可调用 offload_read 工具查看）」（`_OFFLOAD_SUFFIX`），截断指南也写
「已卸载全文可调用 offload_read 查看」。而不设 `LANTAI_MCP_PROFILE` 时（`docs/hermes-install-handoff.md`
的 Hermes 配置就没设），`tools/list` 里没有 offload_read，`tools/call offload_read`
返回 -32602 unknown tool。续31 给 `tests/test_offload.py` 打了 `mcp_full_surface`
标记，所以全量测试一直是绿的——测试拿全量面跑，用户的 Hermes 拿的是默认档。

本文件**不打** `mcp_full_surface`：全程走默认档，就是要拿用户实际的暴露面来判。

三条判据：
1. 合同：shell_hook 模块里每条给模型看的字符串常量，点名的已注册工具都必须在核心集内
   （以后再给注入帧加一句「可调用 xxx」，而 xxx 不在默认档，这条会红）。
2. 端到端：真实 `build_context` 产出卸载注入 → 取出它点名的工具与全文路径 → 默认档
   `handle()` 真调 offload_read（不传 user_id，和 Hermes 实际一致）拿回完整原文。
3. 归属不松：默认档读别人的卸载全文仍被拒。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from lantai.cli import mcp
from tests.support.harness_state import unset_env

REPO_ROOT = Path(__file__).parent.parent
HOOK_PATH = REPO_ROOT / "scripts" / "shell_hook.py"
PROFILE_KEYS = ("LANTAI_MCP_PROFILE", "MCP_TOOL_PROFILE")


def _strip_profiles() -> None:
    for key in PROFILE_KEYS:
        unset_env(key)


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tools_named_in(text: str) -> set[str]:
    """文本里以独立词出现的已注册 MCP 工具名。"""
    return {
        name
        for name in mcp.TOOLS
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
    }


def _model_facing_constants(mod) -> dict[str, str]:
    """shell_hook 里给模型看的模块级字符串常量（含「调用」或「工具」的那些）。"""
    return {
        k: v
        for k, v in vars(mod).items()
        if k.startswith("_") and isinstance(v, str) and ("调用" in v or "工具" in v)
    }


def _seed_engine(rows):
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        for row in rows:
            s.add(MemoryItem(memory_type="semantic", **row))
        s.commit()
    return engine


def test_hook_model_facing_text_only_names_default_profile_tools():
    """判据 1：注入帧文案点名的工具 ⊆ 默认档。"""
    _strip_profiles()
    mod = _load_hook("shell_hook_named_tools")
    consts = _model_facing_constants(mod)
    named = {k: _tools_named_in(v) for k, v in consts.items()}
    all_named = set().union(*named.values()) if named else set()
    # 非空转：至少要扫到卸载提示里的 offload_read，否则说明扫描本身失效
    assert "offload_read" in all_named, f"扫描没命中卸载提示，判据空转：{named}"
    active = set(mcp._get_active_tools())
    missing = {k: sorted(v - active) for k, v in named.items() if v - active}
    assert not missing, f"注入帧叫模型调用的工具不在 MCP 默认档：{missing}；默认档={sorted(active)}"


def test_offload_injection_round_trip_under_default_profile(monkeypatch, tmp_path):
    """判据 2：真实卸载注入 → 默认档 offload_read 拿回全文（不传 user_id）。"""
    _strip_profiles()
    long_content = "用户长期记录：每周三晚上复盘兵役体检准备进度。" + "详细事实" * 100
    engine = _seed_engine([{"id": "mem_off_default", "key": "k", "content": long_content}])

    from sqlmodel import Session

    import lantai.storage.db as db_module

    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": "mem_off_default", "distance": 0.05}]

    mod = _load_hook("shell_hook_offload_default")
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", lambda texts, timeout=None: [[0.1] * 8])
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_OFFLOAD_CHARS", 50)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_CHARS_PER_MEMORY", 60)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_TOTAL_CHARS", 1500)
    monkeypatch.setattr(mod.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))

    context = mod.build_context("我的体检准备现在进行到哪一步了").get("context", "")
    assert "全文:" in context, f"前提：应走卸载注入，实得：{context!r}"

    named = _tools_named_in(context)
    assert "offload_read" in named, f"前提：卸载注入应点名 offload_read，实得 {sorted(named)}"

    advertised = {
        t["name"]
        for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    }
    assert named <= advertised, (
        f"注入帧点名了默认档 tools/list 里没有的工具：{sorted(named - advertised)}"
    )

    # 按注入帧里给的全文路径反推 memory_id（文件名 = {memory_id}.md），和模型能看到的一致
    m = re.search(r"全文:\s*(\S+?\.md)", context)
    assert m, f"注入帧里找不到全文路径：{context!r}"
    memory_id = Path(m.group(1)).stem
    assert memory_id == "mem_off_default"

    resp = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "offload_read", "arguments": {"memory_id": memory_id}},
        }
    )
    assert "error" not in resp, f"默认档调不到注入帧点名的 offload_read：{resp}"
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["content"] == long_content


def test_offload_read_default_profile_still_refuses_other_users_memory(monkeypatch, tmp_path):
    """判据 3：放进默认档不等于放松归属——不传身份读 user-B 的全文仍被拒。"""
    _strip_profiles()
    from lantai.services import offload_service

    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    offload_service.write_offload_file("mem_off_b", "B 的卸载全文")
    engine = _seed_engine(
        [{"id": "mem_off_b", "key": "k-b", "content": "B 的卸载全文", "user_id": "user-B"}]
    )

    from sqlmodel import Session

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(db_module, "engine", engine)

    assert "offload_read" in mcp._get_active_tools()
    resp = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "offload_read", "arguments": {"memory_id": "mem_off_b"}},
        }
    )
    text = json.dumps(resp, ensure_ascii=False)
    assert "B 的卸载全文" not in text, f"默认档读到了别人的卸载全文：{resp}"
    assert "error" in resp
