"""更漏（ADR-0048/票 09）检索时效视图：as-of 与时间窗的纯过滤函数。

读面哲学（spec §3.2/§3.3）：**写侧宁 miss 不脏写，读侧宁多见不错删**——
- I4：event_time IS NULL（含 temporal_unknown）在任何模式下不因时间条件硬排除；
- TEMPORAL_ASOF_STRICT 作用域仅 as-of 视图；时间窗模式无「严格剔除」；
- second 精度是闭点区间特例（spec §2.3）：t ∈ 窗口即命中，不得按左闭右开读成空集。

全部纯函数：真实 MemoryItem 直调可测，不 mock 任何内部逻辑。
"""

from datetime import UTC, datetime, timedelta, timezone

PRECISION_DELTAS = {
    # 精度 → 锚点向后展开的不确定区间宽度（左闭右开；second 特判为点）
    "year": timedelta(days=366),
    "month": timedelta(days=31),
    "day": timedelta(days=1),
    "hour": timedelta(hours=1),
    "minute": timedelta(minutes=1),
    "second": timedelta(0),  # 点区间 [t, t]
    "fuzzy": timedelta(days=1),  # 定宽 ±1 天
}


def _ensure_utc(dt: datetime) -> datetime:
    """I5：全部 UTC 语义；naive 按 UTC 解释（沿 _chronos_filter/ConflictEngine 现行做法）。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def expand_interval(event_time: datetime, precision: str):
    """锚点 + 精度 → 不确定性区间 E（半开 [start, end)；second 为闭点 [t, t]）。

    未知精度按 fuzzy 最宽解释（spec §2.2：绝不因脏值硬排除）。
    """
    t = _ensure_utc(event_time)
    width = PRECISION_DELTAS.get(precision or "fuzzy", PRECISION_DELTAS["fuzzy"])
    if precision == "second":
        return t, t  # 闭点
    return t, t + width


def interval_overlaps(start: datetime, end: datetime, window: tuple) -> bool:
    """区间与窗口重叠判定；second 点区间特判：t ∈ [w0, w1) 即命中。"""
    w0, w1 = (_ensure_utc(window[0]), _ensure_utc(window[1]))
    if start == end:  # 点区间特判（spec §2.3 必须实现）
        return w0 <= start < w1
    return start < w1 and end > w0  # 半开区间交叠


def _validity_hit(item, as_of: datetime) -> bool:
    """有效期命中：(valid_from IS NULL OR valid_from <= as_of) AND (valid_to IS NULL OR valid_to > as_of)。"""
    vf = _ensure_utc(item.valid_from) if item.valid_from else None
    vt = _ensure_utc(item.valid_to) if item.valid_to else None
    # 两个「排除」条件合并为一个否定：起点晚于 as_of 或终点不晚于 as_of 即不命中。
    # 等价于原「逐条 return False，全过 return True」（None 值在两式下都为假，不排除）。
    return not (vf and vf > as_of or vt and vt <= as_of)


def _event_interval_hit(item, as_of: datetime, delta_days: float) -> bool:
    """事件区间命中：E(event_time, precision) 与 [as_of−Δ, as_of+Δ] 交叠。"""
    if item.event_time is None:
        return False
    start, end = expand_interval(item.event_time, item.event_time_precision)
    window = (as_of - timedelta(days=delta_days), as_of + timedelta(days=delta_days))
    return interval_overlaps(start, end, window)


def asof_matches(item, as_of: datetime, *, delta_days: float = 1.0, strict: bool = False) -> tuple:
    """as-of 视图判定（spec §3.2）：返回 (命中, matched_by)。

    判定顺序按信息量（matched_by 取最具体者）：
    1. 有效期命中（vf/vt 至少一端非空，NULL 端为开）→ (True, "validity")；
    2. 事件区间命中 → (True, "event_interval")；
    3. **I4：event_time IS NULL → (True, "unknown_soft")**——任何模式都不因
       时间条件硬排除（spec §2.5 I4；注意 valid_from 列在 SQLModel 0.0.42
       default_factory 读回语义下不存在稳定 NULL——I4 的承载面是 event_time）；
    4. strict=True → (False, "") 剔除；strict=False（默认）→ (True, "missed_soft") 仅降权。
    """
    as_of = _ensure_utc(as_of)
    vf = _ensure_utc(item.valid_from) if item.valid_from else None
    vt = _ensure_utc(item.valid_to) if item.valid_to else None
    if vf or vt:
        if (vf is None or vf <= as_of) and (vt is None or vt > as_of):
            return True, "validity"
    if item.event_time is not None and _event_interval_hit(item, as_of, delta_days):
        return True, "event_interval"
    if item.event_time is None:
        return True, "unknown_soft"  # I4：event_time IS NULL 永不硬排除
    if strict:
        return False, ""
    return True, "missed_soft"


def window_matches(item, time_from: datetime, time_to: datetime) -> tuple:
    """时间窗判定（spec §3.3）：E(event_time) 与窗口重叠 ∨ 有效期区间与窗口重叠。

    matched_by 取最具体者：event_interval > validity > unknown_soft（I4：
    temporal_unknown 软放行；窗口模式无严格剔除——显式给了窗口，未命中窗口
    本就不命中；fuzzy/unknown 仅由调用方按乘子降权）。返回 (命中, matched_by)。
    """
    w0, w1 = _ensure_utc(time_from), _ensure_utc(time_to)
    if item.event_time is not None:
        start, end = expand_interval(item.event_time, item.event_time_precision)
        if interval_overlaps(start, end, (w0, w1)):
            return True, "event_interval"
    vf = _ensure_utc(item.valid_from) if item.valid_from else None
    vt = _ensure_utc(item.valid_to) if item.valid_to else None
    if vf or vt:
        lo = vf if vf else datetime.min.replace(tzinfo=UTC)
        hi = vt if vt else datetime.max.replace(tzinfo=UTC)
        if lo < w1 and hi > w0:
            return True, "validity"
    if item.event_time is None:
        return True, "unknown_soft"  # I4：event_time IS NULL 永不硬排除
    return False, ""


def temporal_view_filter(
    items: list,
    *,
    as_of=None,
    time_from=None,
    time_to=None,
    as_of_recorded=None,
    strict: bool = False,
    fuzzy_penalty: float = 0.8,
    delta_days: float = 1.0,
):
    """召回结果上的时效视图过滤（hybrid 挂点；无时间参数时零改动直通）。

    返回 (kept_items, explain_map)。explain_map: {id: temporal 分项 dict}。
    - 当前态视图（全部参数缺省）：原样返回，零回归铁律；
    - as_of：asof_matches 判定，未命中且 strict → 剔除；unknown_soft 乘 fuzzy_penalty；
    - time_from/time_to：window_matches 判定，未命中剔除（显式窗口语义）；
      unknown_soft 同样乘子降权（I4 不剔除）；
    - as_of_recorded：事务轴近似（created_at <= as_of_recorded），v2 完整版边界。
    """
    if not (as_of or time_from or time_to or as_of_recorded):
        return items, {}

    explain_map: dict = {}
    kept = []
    as_of_dt = _ensure_utc(as_of) if as_of else None
    for m in items:
        matched_by = ""
        keep = True
        if as_of_dt is not None:
            ok, matched_by = asof_matches(m, as_of_dt, delta_days=delta_days, strict=strict)
            if not ok:
                keep = False
        elif time_from is not None and time_to is not None:
            ok, matched_by = window_matches(m, time_from, time_to)
            if not ok:
                keep = False
        if keep and as_of_recorded is not None:
            created = _ensure_utc(m.created_at) if m.created_at else None
            if created and created > _ensure_utc(as_of_recorded):
                keep = False  # 事务轴：兰台当时还不知道（近似语义，spec §3.2 v2 边界）
        if not keep:
            continue
        if matched_by in ("unknown_soft",) or (
            m.event_time is not None and m.event_time_precision == "fuzzy"
        ):
            m.decay_score *= fuzzy_penalty  # 软降权（I4：降权不剔除）
        if matched_by:
            explain_map[m.id] = {
                "view": "as_of" if as_of_dt else "window",
                "event_time": m.event_time.isoformat() if m.event_time else None,
                "event_time_precision": m.event_time_precision or None,
                "valid_from": m.valid_from.isoformat() if m.valid_from else None,
                "valid_to": m.valid_to.isoformat() if m.valid_to else None,
                "matched_by": matched_by,
            }
        kept.append(m)
    return kept, explain_map
