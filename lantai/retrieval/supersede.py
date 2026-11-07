"""更正闸（v0.68 票 02 下沉）：已被明确取代的旧值，不再当作当前事实输出。

周报 2026-11-03 P0-2（Obsolete Facts in Agent Memory / CALM）的收口（PR #30）
把闸挂在了 shell_hook 注入面（script 层私有函数）；本票把闸下沉到检索层供
多条读侧入口复用：`scripts/shell_hook.py` 以同名别名消费本模块（既有更正闸
测试经 `mod._apply_supersede_guard` / `mod._RETROSPECTIVE_RE` 访问，是行为
零变化的回归锁），MCP `handle_search`（`lantai/cli/mcp.py`）同半步接入，
时效视图（as_of 等）跳过。

四个符号自 `scripts/shell_hook.py` **逐字搬入**，语义与日志文案一个字不改。
"""

import re

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import ensure_aware, parse_iso_utc, resolve_display_tz

# 更正闸（周报 2026-11-03 P0-2）：问句里有这些回看词时，用户在问「以前怎样」，
# 已被取代的旧值照常注入（带标记）。误判方向只会是「多留一条带标记的旧值」，
# 不会把有用的记忆挡掉——宁可词表宽一点。
_RETROSPECTIVE_RE = re.compile(
    r"以前|之前|从前|过去|曾经|原来|原先|本来|当时|那时|当初|早先|以往|往年"
    r"|去年|前年|上次|历史|变化|变过|改过|更正|过时|旧的|旧值|老地址"
    r"|before|previous|used to|formerly|history",
    re.IGNORECASE,
)


def _superseded_date_str(mem: dict) -> str:
    """旧值失效日：优先 valid_to（现实失效点 = 新值现实起点），回落 superseded_at。

    按注入显示时区（SHELL_HOOK_INJECT_TZ）只到天；取不到或解析失败返回空串。
    """
    for key in ("valid_to", "superseded_at"):
        value = mem.get(key)
        if not value:
            continue
        try:
            if isinstance(value, str):
                value = parse_iso_utc(value)
            return (
                ensure_aware(value)
                .astimezone(resolve_display_tz(settings.SHELL_HOOK_INJECT_TZ))
                .strftime("%Y-%m-%d")
            )
        except (ValueError, AttributeError, OverflowError, OSError, TypeError):
            continue
    return ""


def _superseded_marker(mem: dict) -> str:
    date = _superseded_date_str(mem)
    if date:
        return f"[已更正：{date} 起不再成立，仅作历史] "
    return "[已更正：不再成立，仅作历史] "


def _apply_supersede_guard(results: list, query: str) -> list:
    """注入前的更正闸：已被明确取代的旧值，不再当作当前事实注入。

    来源：记忆研究周报 2026-11-03 P0-2（Obsolete Facts in Agent Memory / CALM）。
    `late_correction.apply_supersede_correction` 更正后旧记忆仍是 status=active
    （有意保留历史），只标 lifecycle_status=superseded + superseded_by；检索层
    `_apply_supersedes_order` 只把它压到新值之下。此前注入帧把新旧两条并列且不加
    任何标记——模型无从分辨哪条是现在。

    规则（第一版只认显式更正链，不按 valid_to 推测）：
    - lifecycle_status == superseded 且 superseded_by 非空的条目才动；
    - 问句不带回看词、且取代它的新记忆也在本次结果里 → 丢掉旧条目；
    - 否则保留，但正文前加「[已更正：YYYY-MM-DD 起不再成立，仅作历史]」。

    SHELL_HOOK_SUPERSEDED_GUARD=false 时原样返回（逐字旧行为）。不改入参、不碰库。
    """
    if not results or not getattr(settings, "SHELL_HOOK_SUPERSEDED_GUARD", True):
        return results
    present = set()
    for r in results:
        mem = r.get("memory") if isinstance(r, dict) else None
        rid = (mem.get("id") if isinstance(mem, dict) else None) or (
            r.get("id") if isinstance(r, dict) else None
        )
        if rid:
            present.add(rid)
    retrospective = bool(_RETROSPECTIVE_RE.search(query or ""))
    out = []
    dropped = 0
    for r in results:
        mem = r.get("memory") if isinstance(r, dict) else None
        if (
            not isinstance(mem, dict)
            or str(mem.get("lifecycle_status") or "") != "superseded"
            or not mem.get("superseded_by")
        ):
            out.append(r)
            continue
        if not retrospective and mem.get("superseded_by") in present:
            dropped += 1
            continue
        marker = _superseded_marker(mem)
        new_mem = dict(mem)
        new_mem["content"] = marker + (mem.get("content") or "")
        new_r = dict(r)
        new_r["memory"] = new_mem
        if isinstance(new_r.get("document"), str) and new_r["document"]:
            new_r["document"] = marker + new_r["document"]
        out.append(new_r)
    if dropped:
        logger.info("更正闸：%d 条已被取代的旧值未注入（新值已在本次结果里）", dropped)
    return out
