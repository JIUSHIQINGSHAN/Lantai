"""共享文本工具：码点安全截断 + 总字符预算（召回预算 / 场景导航共用）。"""


def truncate_codepoints(text: str, max_chars: int, suffix: str) -> str:
    """按码点截断文本：不会切开多字节字符/emoji 代理对；超长附后缀提示。"""
    cps = list(text)
    if len(cps) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return "".join(cps[:max_chars])
    return "".join(cps[:max_chars - len(suffix)]).rstrip() + suffix


def apply_recall_budget(lines: list[str], max_total_chars: int) -> tuple[list[str], int]:
    """总字符预算分配：按序装入各行（含行间换行），超预算丢弃剩余。

    返回 (budgeted_lines, dropped_count)。
    """
    used = 0
    budgeted: list[str] = []
    for line in lines:
        sep = 1 if budgeted else 0  # 行间分隔换行符
        if used + sep + len(line) > max_total_chars:
            break
        budgeted.append(line)
        used += sep + len(line)
    return budgeted, len(lines) - len(budgeted)
