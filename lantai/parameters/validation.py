"""
参数校验层——纯函数，不依赖 DB / 网络，可独立冒烟测试。

三条防线（生成、批准、启动加载）共用同一套校验：
1. validate_snapshot           —— 完整快照（范围/步长/分组约束/NaN）
2. validate_param_advice       —— LLM 输出（判别联合 + 引用真实性 + 变化合法性）
3. apply_validated_changes     —— 从当前快照应用一组已验证的变更
"""
from decimal import Decimal, InvalidOperation

from lantai.parameters.registry import (
    GROUP_CONSTRAINTS,
    ParamSpec,
    canonical_snapshot_hash,
    get_param_registry,
    normalize_text,
)
from lantai.parameters.schemas import (
    AbstainPayload,
    EvidenceItem,
    ParamChange,
    SuggestPayload,
)


class ParamValidationError(ValueError):
    """参数校验失败——任何校验路径失败即丢弃整条结果，不自动修正。"""


def _d(v) -> Decimal:
    if isinstance(v, bool):
        raise ParamValidationError(f"bool 不是合法数值: {v}")
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ParamValidationError(f"非法数值: {v!r}")


def _finite(v: Decimal) -> bool:
    return v.is_finite()


# ---------------------------------------------------------------- 快照校验

def validate_snapshot(snapshot: dict, registry: dict[str, ParamSpec] | None = None,
                      allow_partial: bool = False) -> dict[str, float]:
    """
    校验一个参数快照。
    - allow_partial=False：必须是完整白名单快照（生成/批准前用）
    - allow_partial=True：允许只包含部分白名单参数（runtime 刷新时用，只 setattr 出现的）
    返回规范化的 {name: float}。
    """
    registry = registry or get_param_registry()
    out: dict[str, float] = {}

    if not allow_partial:
        missing = set(registry) - set(snapshot)
        if missing:
            raise ParamValidationError(f"快照缺少白名单参数: {sorted(missing)}")

    for name, raw in snapshot.items():
        spec = registry.get(name)
        if spec is None:
            raise ParamValidationError(f"未知参数（非白名单）: {name}")
        if not spec.adjustable:
            raise ParamValidationError(f"参数不可调（default deny）: {name}")
        val = _d(raw)
        if not _finite(val):
            raise ParamValidationError(f"NaN/Infinity 非法: {name}={raw!r}")
        if spec.minimum is not None and val < spec.minimum:
            raise ParamValidationError(
                f"{name}={val} 低于最小值 {spec.minimum}")
        if spec.maximum is not None and val > spec.maximum:
            raise ParamValidationError(
                f"{name}={val} 高于最大值 {spec.maximum}")
        if spec.step is not None:
            base = spec.minimum if spec.minimum is not None else Decimal(0)
            span = (val - base) / spec.step
            if abs(span - span.to_integral_value()) > Decimal("1e-9"):
                raise ParamValidationError(
                    f"{name}={val} 不满足步长 {spec.step}")
        out[name] = float(val)

    _validate_group_constraints(out, registry)
    return out


def _validate_group_constraints(snapshot: dict[str, float],
                                registry: dict[str, ParamSpec]) -> None:
    groups: dict[str, list[str]] = {}
    for name, spec in registry.items():
        if spec.adjustable and name in snapshot:
            groups.setdefault(spec.group, []).append(name)

    for group, names in groups.items():
        c = GROUP_CONSTRAINTS.get(group)
        if not c:
            continue
        kind = c["kind"]
        if kind == "sum_equals":
            total = sum(_d(snapshot[n]) for n in names)
            target = _d(c["target"])
            epsilon = _d(c["epsilon"])
            if abs(total - target) > epsilon:
                raise ParamValidationError(
                    f"分组 {group} 之和={total}，须等于 {target}"
                    f"（容差 {epsilon}）")
        elif kind == "ordered_gap":
            higher, lower = c["higher"], c["lower"]
            if higher in snapshot and lower in snapshot:
                gap = _d(snapshot[higher]) - _d(snapshot[lower])
                min_gap = _d(c["min_gap"])
                if gap < min_gap:
                    raise ParamValidationError(
                        f"{higher}-{lower}={gap}，须 ≥ {min_gap}")


# ---------------------------------------------------------------- 变更应用

def apply_validated_changes(current: dict, changes: list[ParamChange],
                            registry: dict[str, ParamSpec] | None = None,
                            max_changes: int | None = None) -> dict[str, float]:
    """
    从 current 快照应用一组已验证变更，返回完整 after 快照。
    先合成再整体校验——保证单项合法但组合非法的场景被拦截。
    """
    registry = registry or get_param_registry()

    if max_changes is not None and len(changes) > max_changes:
        raise ParamValidationError(
            f"变更数 {len(changes)} 超过上限 {max_changes}")

    seen: set[str] = set()
    merged = dict(current)
    for ch in changes:
        spec = registry.get(ch.name)
        if spec is None or not spec.adjustable:
            raise ParamValidationError(f"不可调参数: {ch.name}")
        if ch.name in seen:
            raise ParamValidationError(f"参数重复变更: {ch.name}")
        seen.add(ch.name)
        if ch.before != float(current[ch.name]):
            raise ParamValidationError(
                f"{ch.name} before={ch.before} 与当前快照 {current[ch.name]} 不符")
        after = _d(ch.after)
        before = _d(ch.before)
        if after == before:
            raise ParamValidationError(f"{ch.name} 变更前后相同")
        if spec.max_delta_per_apply is not None \
                and abs(after - before) > spec.max_delta_per_apply:
            raise ParamValidationError(
                f"{ch.name} 单次变化 |{after - before}| 超过上限 "
                f"{spec.max_delta_per_apply}")
        merged[ch.name] = float(after)

    return validate_snapshot(merged, registry)


# ---------------------------------------------------------------- LLM 输出校验

def _validate_evidence(evidence: list[EvidenceItem],
                       papers: list[dict]) -> None:
    """quote 必须是对应论文内容的真实子串（空白归一化后）；source_id 必须属于本批次。"""
    doc_contents = {p["source_document_id"]: normalize_text(p["content"])
                    for p in papers}
    doc_ids = set(doc_contents)
    for e in evidence:
        if e.source_document_id not in doc_ids:
            raise ParamValidationError(
                f"source_id {e.source_document_id} 不属于本批次")
        norm_quote = normalize_text(e.quote)
        if not norm_quote:
            raise ParamValidationError("quote 为空")
        if norm_quote not in doc_contents[e.source_document_id]:
            raise ParamValidationError(
                f"quote 非原文子串（可能虚构证据）: {e.quote[:40]!r}")


def validate_param_advice(payload: dict, current_snapshot: dict,
                          papers: list[dict],
                          min_confidence: float = 0.85,
                          max_changes: int = 6) -> SuggestPayload | AbstainPayload:
    """
    校验 LLM 原始 dict 输出（extra="forbid" 严格解析）：
    - 非法结构 / 幻觉参数 / 虚构证据 / 低置信度 / 约束违反 → 抛 ParamValidationError
    - abstain 合法 → 返回 AbstainPayload（不创建建议）
    """
    registry = get_param_registry()

    # 判别联合解析：suggest / abstain（extra=forbid）
    decision = payload.get("decision")
    if decision == "abstain":
        return AbstainPayload.model_validate(payload)
    if decision != "suggest":
        raise ParamValidationError(f"非法 decision: {decision!r}")

    sug = SuggestPayload.model_validate(payload)
    if sug.confidence < min_confidence:
        raise ParamValidationError(
            f"置信度 {sug.confidence} 低于阈值 {min_confidence}")

    _validate_evidence(sug.evidence, papers)

    # 应用变更并整体校验（范围/步长/最大变化/分组约束/before 一致）
    apply_validated_changes(current_snapshot,
                            sug.changes, registry, max_changes=max_changes)

    return sug


def snapshot_hash(snapshot: dict) -> str:
    """快捷：canonical hash。"""
    return canonical_snapshot_hash(snapshot)


# ---------------------------------------------------------------- V2 信号校验（方向一/四）

# 受保护信号键：LLM 输出中出现即判越界（试图影响 gating）
PROTECTED_SIGNAL_KEYS: tuple[str, ...] = (
    "venue_class", "evidence_tier", "peer_reviewed", "journal_ref",
    "doi", "published_at", "citation_count", "primary_evidence_eligible",
    "signal_source", "tier_reason", "gating",
)


def detect_signal_contamination(obj, path: str = "") -> list[str]:
    """递归扫描 LLM 输出任意层级，命中受保护键 → 提示词越界。"""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PROTECTED_SIGNAL_KEYS:
                hits.append(f"{path}.{k}" if path else k)
            hits.extend(detect_signal_contamination(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(detect_signal_contamination(v, f"{path}[{i}]"))
    return hits


def enforce_primary_eligibility(evidence: list, views: dict) -> bool:
    """建议的证据中至少一篇论文 primary_evidence_eligible（非 D 档且未过期阻断）。"""
    eligible = {sid for sid, v in views.items() if v.primary_evidence_eligible}
    return any(e.source_document_id in eligible for e in evidence)


def enforce_quorum(evidence: list, views: dict, quorum_required: int) -> bool:
    """同向证据中 eligible 论文去重数 >= quorum（tier C 需 2 篇互证）。"""
    if quorum_required <= 1:
        return True
    eligible_ids = {
        e.source_document_id for e in evidence
        if views.get(e.source_document_id) is not None
        and views[e.source_document_id].primary_evidence_eligible
    }
    return len(eligible_ids) >= quorum_required


def apply_tier_weight(confidence: float, tier_weight: float) -> float:
    """有效置信度 = min(llm_conf, llm_conf * weight)——权重只能压低不能抬高。"""
    return min(confidence, confidence * tier_weight)


def scale_delta_budget(base_max_delta, factor) -> Decimal:
    """按证据强度缩放单次允许变化量（越弱证据步长越小）。"""
    return _d(base_max_delta) * _d(factor)


def _max_evidence_tier(evidence: list, views: dict) -> str:
    """证据论文中最高 tier（A>B>C>D），用于定 gating。"""
    order = {"A": 4, "B": 3, "C": 2, "D": 1}
    best = "D"
    for e in evidence:
        v = views.get(e.source_document_id)
        if v and order.get(v.evidence_tier, 0) > order.get(best, 0):
            best = v.evidence_tier
    return best


def validate_suggestion_with_signals(sug: SuggestPayload, current_snapshot: dict,
                                     papers: list[dict], views: dict,
                                     min_confidence: float,
                                     max_changes: int) -> dict:
    """
    批量结构下的单条建议校验：基础校验 + 信号三道锁（资格/quorum/预算缩放）。
    返回 {suggestion, tier, effective_confidence, delta_budget_factor}；
    任一失败抛 ParamValidationError。
    """
    if sug.confidence < min_confidence:
        raise ParamValidationError(
            f"置信度 {sug.confidence} 低于阈值 {min_confidence}")

    _validate_evidence(sug.evidence, papers)

    # 信号锁 1：主证据资格（至少一篇 eligible）
    if not enforce_primary_eligibility(sug.evidence, views):
        raise ParamValidationError(
            "建议证据全部来自 ineligible 论文（tier D 或已过期阻断）")

    tier = _max_evidence_tier(sug.evidence, views)
    from lantai.parameters.registry import get_param_registry
    from lantai.parameters.signal_service import resolve_gating
    gating = resolve_gating(tier)

    # 信号锁 2：quorum 互证
    if not enforce_quorum(sug.evidence, views, gating.quorum_required):
        raise ParamValidationError(
            f"tier {tier} 需 {gating.quorum_required} 篇互证，当前不足")

    # 信号锁 3：delta 预算按 tier 缩放后仍须满足
    registry = get_param_registry()
    effective_max_delta = {
        name: scale_delta_budget(spec.max_delta_per_apply,
                                 gating.delta_budget_factor)
        for name, spec in registry.items() if spec.adjustable
    }
    for ch in sug.changes:
        limit = effective_max_delta.get(ch.name)
        if limit is not None and abs(_d(ch.after) - _d(ch.before)) > limit:
            raise ParamValidationError(
                f"{ch.name} 变化量超出 tier {tier} 预算 {limit}")

    # 基础变更校验（范围/步长/分组/before 一致）
    apply_validated_changes(current_snapshot, sug.changes, registry,
                            max_changes=max_changes)

    effective_confidence = apply_tier_weight(sug.confidence, gating.tier_weight)
    return {"suggestion": sug, "tier": tier,
            "effective_confidence": effective_confidence,
            "delta_budget_factor": gating.delta_budget_factor}


def validate_batch_advice(payload: dict, current_snapshot: dict,
                          papers: list[dict], views: dict,
                          min_confidence: float = 0.85,
                          max_changes: int = 6) -> dict:
    """
    V2 批量输出校验：
    - 污染检测（受保护键越界 → 整批丢弃）
    - BatchParamAdvice 严格解析（extra=forbid）
    - 矛盾条目 quote 真实性 + source 不同 + param_key 白名单
    - 逐条建议走 validate_suggestion_with_signals
    返回 {"suggestions": [ValidatedSuggestion...], "contradictions": [...]}；
    任何失败抛 ParamValidationError（整批丢弃，不修正）。
    """
    from lantai.parameters.schemas import (
        BatchParamAdvice,
        ContradictionItem,
    )

    contamination = detect_signal_contamination(payload)
    if contamination:
        raise ParamValidationError(
            f"信号污染（提示词越界）: {contamination[:5]}")

    batch = BatchParamAdvice.model_validate(payload)

    # 矛盾条目校验（quote 真实性 / source 不同 / param_key 白名单）
    doc_contents = {p["source_document_id"]: normalize_text(p["content"])
                    for p in papers}
    valid_contradictions: list[ContradictionItem] = []
    for c in batch.contradictions:
        if c.side_a.source_document_id not in doc_contents \
                or c.side_b.source_document_id not in doc_contents:
            raise ParamValidationError("矛盾条目引用了批外 source_id")
        if c.side_a.source_document_id == c.side_b.source_document_id:
            raise ParamValidationError("矛盾两侧必须是不同论文")
        if normalize_text(c.side_a.quote) not in \
                doc_contents[c.side_a.source_document_id] \
                or normalize_text(c.side_b.quote) not in \
                doc_contents[c.side_b.source_document_id]:
            raise ParamValidationError("矛盾条目引用了虚构 quote（整批丢弃）")
        valid_contradictions.append(c)

    # 命中矛盾的 param_key 分区：对应建议不产出（规则 20 的后端强制）
    conflicted_keys = {c.param_key for c in valid_contradictions}

    validated = []
    for sug in batch.suggestions:
        if any(ch.name in conflicted_keys for ch in sug.changes):
            continue  # 矛盾参数不产建议，转矛盾报告
        result = validate_suggestion_with_signals(
            sug, current_snapshot, papers, views,
            min_confidence, max_changes)
        validated.append(result)

    return {"suggestions": validated, "contradictions": valid_contradictions}
