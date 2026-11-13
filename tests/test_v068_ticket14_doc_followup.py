"""v0.68 增量文档随行回归锁（v0.68-docs-audit 票 14 余量：D-3a/D-4/D-5）。

v0.68 票 02（更正闸下沉 + MCP search 套闸）与 PR #36（mark_abandoned 超时轮
收尾）落地后，三处随行记载缺位，本文件按内容模式锁住（不按行号）：

- manual-call 手册 search 面未提更正闸语义（丢/标被取代旧值、时效视图跳过、
  开关名）与 force 边界（只绕拾遗 relevance gate，不绕更正闸）；
- ADR-0084 未记载超时轮孤儿事件的 `mark_abandoned` 收尾路径（pending→missed
  就地抹文，与 §2.6 的 300s 扫描殊途同归）；
- host-hook-protocol §3「超时与降级」只写超时返回什么，不写迟到事件如何收尾。

行为面（mark_abandoned 真库转移）由 `tests/test_hook_timeout_orphan_event.py`
承载，本文件不重复；settings 开关真实导入核对，不 mock。
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── D-3a/D-4：manual-call 的 search 更正闸与 force 边界 ────────────────────


def test_manual_call_documents_supersede_guard_semantics():
    doc = _read("docs/lantai-manual-call.md")
    assert "更正闸" in doc, (
        "MCP search 非时效查询应用更正闸（v0.68 票 02，mcp.py 套闸），使用面零记载"
    )
    assert "取代" in doc, "应写明被取代旧值的处置（新值同场即丢、否则加已更正标记）"
    assert "时效" in doc, "as_of 等时效视图属历史查询面，跳过更正闸，应写明"
    assert "SHELL_HOOK_SUPERSEDED_GUARD" in doc, "开关沿用注入面（settings.py），应写明"


def test_manual_call_force_does_not_bypass_correction_gate():
    doc = _read("docs/lantai-manual-call.md")
    # force 只绕拾遗 relevance gate（mcp.py handle_search 的 gate 分支），
    # 更正闸在 force 旁路之外照常应用（mcp.py 时效视图判断后的套闸段）
    assert "不绕更正闸" in doc, (
        "force=true 的检索结果仍会被更正闸丢/标旧值，手册只写「绕过闸门」会误导"
    )


def test_settings_switch_name_is_real():
    from lantai.core.settings import settings

    assert isinstance(settings.SHELL_HOOK_SUPERSEDED_GUARD, bool), (
        "手册记载的开关名必须与 settings 现值对得上"
    )


# ── D-5：mark_abandoned 立法与协议面记载 ──────────────────────────────────


def test_adr0084_documents_mark_abandoned_closure():
    doc = _read("docs/adr/0084-vetoed-receipt-terminal-state.md")
    assert "mark_abandoned" in doc, (
        "超时轮孤儿事件收尾（retrieval_log.mark_abandoned：pending→missed 就地抹文）"
        "是本 ADR 生命周期的一部分，零记载即立法真空"
    )


def test_protocol_timeout_section_documents_orphan_closure():
    doc = _read("docs/host-hook-protocol.md")
    assert "mark_abandoned" in doc, (
        "§3 超时与降级只写超时返回什么，不写超时轮迟到事件如何收尾"
        "（宿主拿不到 event_id，回执/否决帧都发不出，原话将滞留 pending）"
    )
