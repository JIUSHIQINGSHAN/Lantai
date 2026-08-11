"""
检索事件日志（方向二弱标注源）——只追加，失败不影响主链路。

无标注评估集的现实替代：记录"哪条记忆被召回 + 当时生效参数 + 延迟"，
后续 dry-run/shadow 用这些事件做相对指标（zero_result / jaccard / 弱命中率）。
"""
import hashlib
import time

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import RetrievalEvent
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash
from lantai.storage import db


def _norm_hash(query: str) -> str:
    digest = hashlib.sha256(" ".join(query.split()).lower().encode("utf-8"))
    return digest.hexdigest()


# 系统注入噪音的确定性前缀（Hermes 技能库维护 / 记忆保存 / Skill 安装模板）。
# 这类查询不是用户的真实记忆回忆，混入评估集会稀释 dry-run 指标。
_SYSTEM_NOISE_PREFIXES = (
    "review the conversation above",
    "consider saving to memory",
    "请帮我安装这个 agent skill",
)
# 长度阈值：实测存量数据 201-500 字符区间为 0 条——真实回忆查询几乎都 ≤200，
# >500 的基本是超长系统注入指令（技能库维护 prompt 达 5-7k 字符）。天然鸿沟，可安全判定。
_SYSTEM_NOISE_MAX_LEN = 500


def is_system_noise(query: str) -> bool:
    """判定一次检索查询是否为系统注入噪音（非用户真实记忆回忆）。

    纯函数、无副作用；宁 miss 不误标——只认确定性前缀 + 长度鸿沟，
    不依赖关键词匹配，避免把用户的真实长查询误判为噪音。
    """
    q = (query or "").strip()
    if not q:
        return False
    lower = q.lower()
    if any(lower.startswith(p) for p in _SYSTEM_NOISE_PREFIXES):
        return True
    return len(q) > _SYSTEM_NOISE_MAX_LEN


def log_retrieval(query: str, results: list[dict], *, latency_ms: int,
                  gate: dict | None = None, trace_id: str | None = None,
                  lanes: list[str] | None = None) -> str | None:
    """
    在检索出口记录一次事件。失败仅记日志，绝不抛给主链路。
    返回事件 id（供后续回填 used_ids）；失败返回 None。
    """
    try:
        event_id = new_id("rev")
        result_ids = [r["memory"]["id"] for r in results
                      if isinstance(r, dict) and "memory" in r]
        result_scores = [r["score"] for r in results
                         if isinstance(r, dict) and "score" in r]
        intent = (gate or {}).get("intent") if isinstance(gate, dict) else None
        from lantai.observability.recall_report import (
            _scenes_from_results, _tokens_from_results, estimate_tokens)
        with db.get_session() as s:
            s.add(RetrievalEvent(
                id=event_id,
                trace_id=trace_id or "",
                query_text=query,
                query_norm_hash=_norm_hash(query),
                lane=",".join(lanes) if lanes else "",
                intent_bucket=intent if isinstance(intent, str) else None,
                param_snapshot_hash=snapshot_hash(default_snapshot()),
                result_ids=result_ids,
                result_scores=result_scores,
                used_ids=[],
                latency_ms=int(latency_ms),
                zero_result=not result_ids,
                is_system_noise=is_system_noise(query),
                scene_ids=_scenes_from_results(results),
                estimated_tokens=(estimate_tokens(query) + _tokens_from_results(results)),
            ))
            s.commit()
        return event_id
    except Exception:
        logger.exception("retrieval event log failed (non-fatal)")
        return None


def backfill_used_ids(event_id: str, used_ids: list[str]) -> None:
    """生成侧回填：哪些被召回的记忆真正被用进回答（弱标注）。"""
    try:
        with db.get_session() as s:
            ev = s.get(RetrievalEvent, event_id)
            if ev:
                ev.used_ids = list(used_ids)
                s.add(ev)
                s.commit()
    except Exception:
        logger.exception("retrieval used_ids backfill failed (non-fatal)")
