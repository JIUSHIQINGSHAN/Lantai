"""
LLM 建议生成器——只提出候选，不直接写 settings / .env / override。

- 无 fallback：LLM 失败 / 非法输出 / 低置信度 → 不创建建议（宁 miss 不脏写）。
- 证据引用必须是对应论文正文的真实子串（归一化后），防虚构。
"""
from remembrance.core.logger import logger
from remembrance.core.settings import settings
from remembrance.llm.client import chat_json
from remembrance.llm.prompts import PARAM_ADVICE_SYS
from remembrance.parameters.registry import (
    GROUP_CONSTRAINTS,
    PHYSICALLY_EXCLUDED,
    canonical_json,
    get_param_registry,
)
from remembrance.parameters.schemas import (
    AbstainPayload,
    ParamAdviceResult,
    SuggestPayload,
)
from remembrance.parameters.validation import (
    ParamValidationError,
    validate_param_advice,
)


def _registry_for_llm() -> dict:
    """只给 LLM 暴露 adjustable 参数的规格（不含任何密钥/安全参数值）。"""
    out = {}
    for name, spec in get_param_registry().items():
        if spec.adjustable:
            out[name] = {
                "value_type": spec.value_type,
                "description": spec.description,
                "minimum": str(spec.minimum) if spec.minimum is not None else None,
                "maximum": str(spec.maximum) if spec.maximum is not None else None,
                "step": str(spec.step) if spec.step is not None else None,
                "max_delta_per_apply": (
                    str(spec.max_delta_per_apply)
                    if spec.max_delta_per_apply is not None else None),
                "group": spec.group,
                "risk_level": spec.risk_level,
            }
    return out


def _group_constraints_for_llm() -> dict:
    return {g: {k: v for k, v in c.items()}
            for g, c in GROUP_CONSTRAINTS.items()}


def build_param_advice_user_prompt(papers: list[dict],
                                   current_snapshot: dict) -> str:
    """拼接 user context（canonical JSON，键序稳定）。"""
    context = {
        "SYSTEM_CONTEXT": {
            "system": "Remembrance-System",
            "retrieval": "weighted vector + BM25 + FTS5 + decay fusion",
            "evidence_scope": "abstract or supplied excerpt only",
        },
        "POLICY": {
            "minimum_confidence": settings.PARAM_ADVICE_MIN_CONFIDENCE,
            "maximum_changes": settings.PARAM_ADVICE_MAX_CHANGES,
        },
        "CURRENT_SNAPSHOT": current_snapshot,
        "PARAMETER_REGISTRY": _registry_for_llm(),
        "GROUP_CONSTRAINTS": _group_constraints_for_llm(),
        "NON_ADJUSTABLE_NAMES": list(PHYSICALLY_EXCLUDED),
        "PAPERS": [
            {"source_document_id": p["source_document_id"],
             "title": p["title"], "source_url": p["source_url"],
             "content": p["content"]}
            for p in papers
        ],
    }
    return ("Return strict JSON matching the schema in the system prompt.\n"
            "Only output JSON.\n\n"
            + canonical_json(context))


def generate_param_advice(papers: list[dict],
                          current_snapshot: dict,
                          min_confidence: float | None = None,
                          max_changes: int | None = None) -> dict:
    """
    调用 LLM 并严格校验。
    返回：
      {"ok": True,  "payload": SuggestPayload | AbstainPayload}
      {"ok": False, "error_code": "llm_error" | "validation_error" | "disabled"}
    绝不抛异常（worker 依赖此契约）。
    """
    if not settings.PARAM_ADVICE_ENABLED:
        return {"ok": False, "error_code": "disabled"}

    min_confidence = min_confidence if min_confidence is not None \
        else settings.PARAM_ADVICE_MIN_CONFIDENCE
    max_changes = max_changes if max_changes is not None \
        else settings.PARAM_ADVICE_MAX_CHANGES

    user = build_param_advice_user_prompt(papers, current_snapshot)
    try:
        raw = chat_json(PARAM_ADVICE_SYS, user)
    except Exception as e:  # chat_json 内部已重试 3 次
        logger.warning("param advice LLM call failed: %s", e)
        return {"ok": False, "error_code": "llm_error"}

    try:
        payload = validate_param_advice(raw, current_snapshot, papers,
                                        min_confidence, max_changes)
    except (ParamValidationError, Exception) as e:
        if isinstance(e, ParamValidationError):
            logger.info("param advice rejected: %s", e)
        else:  # pydantic ValidationError 等
            logger.info("param advice schema invalid: %s", str(e)[:200])
        return {"ok": False, "error_code": "validation_error"}

    return {"ok": True, "payload": payload}
