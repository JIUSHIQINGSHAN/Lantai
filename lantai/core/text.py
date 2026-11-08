"""共享文本工具：码点安全截断 + 总字符预算 + 来源链字段规范化（单一真源）。"""

import re
import unicodedata

SESSION_ID_MAX_CHARS = 128


# 票 28：LIKE 模式串字面化的单一转义真源（hybrid 降级臂 / shiyi 拾遗预筛共用）。
# 用户文本里的 % _ \ 是 SQL LIKE 的通配与转义元字符，不转义即过匹配：
# 裸 `_` 查询等于 `%_%`（全库命中）、`foo_bar` 会把 `fooXbar` 当通配捞进。
# 必须与 `.like(..., escape=LIKE_ESCAPE_CHAR)` 成对使用（escape 字符本身
# 也要转义，故反斜杠最先替换）。
LIKE_ESCAPE_CHAR = "\\"


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


def like_escape(token: str) -> str:
    """把用户 token 转成 LIKE 模式串里的字面文本：\ % _ 三字符前缀反斜杠。

    配套用法：col.like(f"%{like_escape(t)}%", escape=LIKE_ESCAPE_CHAR)。
    剥离通配符会把「查询 foo_bar 应命中字面 foo_bar」的验收废掉，故票 28
    拍板转义而非剥离。
    """
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


# 票 45：剥除集——Cf 类零宽（ZWSP/ZWNJ/ZWJ/词连接符/BOM/LRM/RLM）与 bidi
# 控制符（LRE/RLE/PDF/LRO/RLO/LRI/RLI/FSI/PDI）。这些字符在正文里不可见，
# 却把 trigram 邻接撕开（「机器\u200b学习」的索引 gram ≠ 查询「机器学习」），
# RTL override 还能把视觉顺序伪装透传宿主终端。NFKC 不剥它们（宽度折叠
# 与兼容分解对 Cf 无操作），必须在缝里显式剥除。
_INVISIBLE_CHARS_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def normalize_for_index(text: str | None) -> str:
    """索引/查询/去重键共用的归一缝（票 45，单一真源；ADR-0085）。

    NFKC（全角↔半角折宽度）→ 剥零宽/bidi 控制符 → NFC（NFD 形态收敛）。
    三个消费点同口径：FTS 索引副本与查询关键字抽取、案牍去重键与列表匹配、
    反思聚类签名/检索指纹（登记项）。

    **只许用于派生数据**（索引副本、哈希键、匹配串），不许改写
    MemoryItem.content 等用户资产——归一索引副本 ≠ 内容改写（獬豸
    「不改写用户字」同一边界，ADR-0085 留痕）。
    """
    if not text:
        return ""
    stripped = _INVISIBLE_CHARS_RE.sub("", text)
    return unicodedata.normalize("NFC", unicodedata.normalize("NFKC", stripped))
