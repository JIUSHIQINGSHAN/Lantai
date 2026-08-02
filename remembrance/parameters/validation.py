"""
参数校验层——纯函数，不依赖 DB / 网络，可独立冒烟测试。

三条防线（生成、批准、启动加载）共用同一套校验：
1. validate_snapshot           —— 完整快照（范围/步长/分组约束/NaN）
2. validate_param_advice       —— LLM 输出（判别联合 + 引用真实性 + 变化合法性）
3. apply_validated_changes     —— 从当前快照应用一组已验证的变更
"""
from decimal import Decimal, InvalidOperation

from remembrance.parameters.registry import (
    GROUP_CONSTRAINTS,
    ParamSpec,
    canonical_snapshot_hash,
    get_param_registry,
    normalize_text,
)
from remembrance.parameters.schemas import (
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
