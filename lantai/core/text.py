"""共享文本工具：码点安全截断 + 总字符预算 + 来源链字段规范化（单一真源）。"""

SESSION_ID_MAX_CHARS = 128


def normalize_session_id(value, *, default=None):
    """来源链 session_id 规范化（shell_hook / MCP / 插件协议共用，单一真源）。

    非字符串或超长 → 返回 default（调用方决定用 "" 还是 None，宁 miss 不脏写）。
    """
    if not isinstance(value, str):
        return default
    value = value.strip()
    if not value or len(value) > SESSION_ID_MAX_CHARS:
        return default
    return value


def truncate_codepoints(text: str, max_chars: int, suffix: str) -> str:
    """按码点截断文本：不会切开多字节字符/emoji 代理对；超长附后缀提示。"""
    cps = list(text)
    if len(cps) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return "".join(cps[:max_chars])
    return "".join(cps[: max_chars - len(suffix)]).rstrip() + suffix


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
