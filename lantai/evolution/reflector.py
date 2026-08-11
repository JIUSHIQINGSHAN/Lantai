from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, MemoryUsageFeedback
from lantai.storage import db
from lantai.core.ids import new_id


def record_feedback(memory_id: str, query: str,
                    helped: bool, user_accepted: bool,
                    hallucination_risk: float) -> dict:
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False}
        delta = (0.1 if helped else -0.05) + (0.1 if user_accepted else 0) \
                - 0.2 * hallucination_risk
        mem.use_count += 1
        mem.helpful_count += int(helped)
        mem.importance = max(0.0, min(1.0, mem.importance + delta))
        mem.last_used_at = utcnow()
        s.add(mem)
        s.add(MemoryUsageFeedback(
            id=new_id("fb"), memory_id=memory_id, query=query,
            helped=helped, user_accepted=user_accepted,
            hallucination_risk=hallucination_risk, score_delta=delta,
        ))
        s.commit()
        return {"ok": True, "importance": mem.importance}


# ── 反思/蒸馏（spec: docs/plans/reflection-module-spec.md）─────────────
from datetime import timedelta, timezone

from sqlmodel import select

from lantai.core.scheduler import record_run
from lantai.core.settings import settings
from lantai.llm.client import chat_json
from lantai.llm.prompts import REFLECT_CURATOR_SYS, REFLECT_REJECTER_SYS
from lantai.models.enums import ProposalStatus
from lantai.models.tables import (ConflictEvent, MemoryEdge, MemoryItem,
                                  MemoryProposal)

_VALID_TYPES = {"add", "update", "merge", "deprecate"}


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cand(mem: MemoryItem, signal: str, extra: dict | None = None) -> dict:
    c = {"memory_id": mem.id, "key": mem.key, "content": mem.content,
         "lane": mem.lane, "importance": mem.importance, "signal": signal}
    if extra:
        c.update(extra)
    return c


def health_scan(session) -> dict:
    """健康扫描：问题驱动反思输入（零 LLM，纯 SQL）。

    规则 R1-R3 默认开（superseded 残留 / 过期时间窗 / open 冲突账本），
    R4/R5 受 REFLECT_STALE_SCAN_ENABLED 控制（低帮助率 / 低价值陈旧）。
    返回 {"snapshot": {...}, "candidates": [...]}（候选截断到 REFLECT_MAX_BATCH）。
    """
    now = utcnow()
    superseded_by: dict[str, str] = {}
    for e in session.exec(select(MemoryEdge)
                          .where(MemoryEdge.relation == "supersedes")).all():
        superseded_by.setdefault(e.target_memory_id, e.source_memory_id)

    candidates: list[dict] = []
    for m in session.exec(select(MemoryItem)
                          .where(MemoryItem.status == "active")).all():
        if m.id in superseded_by:
            candidates.append(_cand(m, "superseded",
                                    {"superseded_by": superseded_by[m.id]}))
            continue
        vt = _as_utc(m.valid_to)
        if vt is not None and vt < now and m.decay_class != "procedural":
            candidates.append(_cand(m, "expired"))
            continue
        if settings.REFLECT_STALE_SCAN_ENABLED:
            if (m.use_count >= settings.REFLECT_MIN_USE_COUNT
                    and m.helpful_count / m.use_count
                    <= settings.REFLECT_LOW_HELPFUL_RATIO):
                candidates.append(_cand(m, "low_helpful"))
                continue
            last = _as_utc(m.last_used_at or m.created_at)
            age_days = max(0.0, (now - last).total_seconds() / 86400.0)
            if (age_days >= settings.REFLECT_STALE_AGE_DAYS
                    and m.use_count == 0
                    and m.importance < settings.REFLECT_STALE_IMPORTANCE
                    and m.decay_class != "procedural"):
                candidates.append(_cand(m, "stale_low_value"))

    for ev in session.exec(select(ConflictEvent)
                           .where(ConflictEvent.status == "open")).all():
        m = session.get(MemoryItem, ev.memory_id)
        if m:
            candidates.append(_cand(m, "open_conflict",
                                    {"conflict_event_id": ev.id,
                                     "detail": ev.detail}))

    seen: set[str] = set()
    batch: list[dict] = []
    for cand in candidates:
        if cand["memory_id"] in seen:
            continue
        seen.add(cand["memory_id"])
        batch.append(cand)
        if len(batch) >= settings.REFLECT_MAX_BATCH:
            break

    def _count(signal: str) -> int:
        return sum(1 for c in candidates if c["signal"] == signal)

    snapshot = {
        "superseded_active": _count("superseded"),
        "expired_active": _count("expired"),
        "open_conflicts": _count("open_conflict"),
        "low_helpful": _count("low_helpful"),
        "stale_low_value": _count("stale_low_value"),
        "batch_total": len(batch),
    }
    return {"snapshot": snapshot, "candidates": batch}


def _importance_waterline(session) -> float:
    """近窗口新增记忆 importance 累加（水位触发；无持久化时间戳，近似实现）。"""
    now = utcnow()
    start = now - timedelta(days=settings.REFLECT_IMPORTANCE_WINDOW_DAYS)
    total = 0.0
    for m in session.exec(select(MemoryItem)).all():
        created = _as_utc(m.created_at)
        if created is not None and created >= start:
            total += m.importance
    return total


def _curate(candidates: list[dict], related_texts: str) -> dict:
    """阶段 1：curator 蒸馏提案（strict JSON；异常降级为空，宁 miss）。"""
    if not candidates:
        return {"proposals": []}
    batch_text = "\n".join(
        f"- [{c['memory_id']}] lane={c['lane']} importance={c['importance']:.2f} "
        f"signal={c['signal']} {c['key']}: {c['content'][:200]}"
        for c in candidates)
    user = (f"FLAGGED MEMORIES:\n{batch_text}\n\n"
            f"RELATED EXISTING MEMORIES:\n{related_texts or '(none)'}")
    try:
        return chat_json(REFLECT_CURATOR_SYS, user)
    except Exception:
        return {"proposals": []}


def _reject(prop: MemoryProposal, evidence_texts: str) -> dict:
    """阶段 2：rejecter 复核（防幻觉蒸馏；异常按不通过处理，宁 miss）。"""
    if not evidence_texts.strip():
        return {"accept": False, "risk": "high", "reason": "no evidence text"}
    patch = prop.proposed_patch or {}
    user = (f"PROPOSAL:\ntype={prop.proposal_type} "
            f"target={prop.target_memory_id}\ncontent={patch.get('content', '')}\n"
            f"reason={prop.reason}\n\nEVIDENCE:\n{evidence_texts}")
    try:
        return chat_json(REFLECT_REJECTER_SYS, user)
    except Exception:
        return {"accept": False, "risk": "high", "reason": "rejecter unavailable"}


def propose_from_reflection(session, candidates: list[dict],
                            curated: dict) -> list[MemoryProposal]:
    """curator 输出 → MemoryProposal（证据存在性校验 + 置信过滤）。

    证据校验：evidence_ids 必须指向库中真实存在的 MemoryItem（防编造 id）；
    update/merge/deprecate 必须携带证据（宁 miss）；add 允许无证据。
    返回已 refresh 的提案列表（脱离 session 后可安全读取）。
    """
    props: list[MemoryProposal] = []
    for p in curated.get("proposals", []):
        ptype = p.get("proposal_type", "")
        if ptype not in _VALID_TYPES:
            continue
        conf = float(p.get("confidence", 0.0))
        if conf < settings.REFLECT_MIN_CONFIDENCE:
            continue
        evidence = [e for e in (p.get("evidence_ids") or [])
                    if session.get(MemoryItem, e) is not None]
        if ptype != "add" and not evidence:
            continue
        target = p.get("target_memory_id") or ""
        target_mem = None
        if ptype in ("update", "merge", "deprecate"):
            if not target:
                continue
            target_mem = session.get(MemoryItem, target)
            if not target_mem or target_mem.status != "active":
                continue
            target = target_mem.id
        content = p.get("new_content", "")
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type=ptype,
            target_memory_id=target or None,
            candidate_id=None,
            evidence_ids=evidence,
            reason=p.get("reason", ""),
            proposed_patch={
                "memory_type": p.get("memory_type", "semantic"),
                "key": (target_mem.key if target_mem else content[:60]),
                "content": content,
                "lane": p.get("lane") or (candidates[0]["lane"] if candidates
                                          else settings.DEFAULT_LANE),
            },
            confidence=conf,
            conflict_ids=[],
            status=ProposalStatus.PENDING,
            decided_by="auto",
        )
        session.add(prop)
        props.append(prop)
    session.commit()
    for p in props:
        session.refresh(p)
    return props


def run_reflect_once() -> dict:
    """反思主入口：健康扫描 →（水位触发新记忆蒸馏）→ curator → 提案 → 裁决。

    自动应用：confidence >= REFLECT_AUTO_APPLY_CONF 且 rejecter risk=low；
    risk=medium 强制 pending；accept=false / risk=high 丢弃（宁 miss）。
    返回统计 + 健康快照前后对比（自证）。
    """
    with db.get_session() as s:
        scan = health_scan(s)
        waterline = _importance_waterline(s)
        related = s.exec(select(MemoryItem)
                         .where(MemoryItem.status == "active")).all()
        related_texts = "\n".join(
            f"- ({m.memory_type}) {m.key}: {m.content}" for m in related[:20])

    candidates = scan["candidates"]
    theme_triggered = waterline >= settings.REFLECT_IMPORTANCE_POOL
    if not candidates and not theme_triggered:
        record_run("reflect")
        return {"ok": True, "skipped": "idle",
                "health": scan["snapshot"], "waterline": round(waterline, 2)}

    if theme_triggered:
        start = utcnow() - timedelta(days=settings.REFLECT_IMPORTANCE_WINDOW_DAYS)
        with db.get_session() as s:
            for m in s.exec(select(MemoryItem)).all():
                created = _as_utc(m.created_at)
                if created is None or created < start:
                    continue
                if any(c["memory_id"] == m.id for c in candidates):
                    continue
                candidates.append(_cand(m, "new_theme"))
                if len(candidates) >= settings.REFLECT_MAX_BATCH:
                    break

    curated = _curate(candidates, related_texts)
    with db.get_session() as s:
        props = propose_from_reflection(s, candidates, curated)

    cand_by_id = {c["memory_id"]: c for c in candidates}
    auto_applied = pending = discarded = 0
    for prop in props:
        with db.get_session() as s:
            evidence_texts = "\n".join(
                m.content for eid in prop.evidence_ids
                if (m := s.get(MemoryItem, eid)) is not None)
        verdict = _reject(prop, evidence_texts)
        if not verdict.get("accept") or verdict.get("risk") == "high":
            with db.get_session() as s:
                p = s.get(MemoryProposal, prop.id)
                p.status = ProposalStatus.REJECTED
                p.decision_reason = str(verdict.get("reason", ""))
                s.add(p)
                s.commit()
            discarded += 1
            continue
        if (prop.confidence >= settings.REFLECT_AUTO_APPLY_CONF
                and verdict.get("risk") == "low"):
            from lantai.evolution.promoter import apply_proposal
            res = apply_proposal(prop.id)
            if res.get("ok"):
                auto_applied += 1
                src = cand_by_id.get(prop.target_memory_id)
                if src and src.get("conflict_event_id"):
                    try:
                        from lantai.services.conflict_service import (
                            resolve_conflict_event)
                        resolve_conflict_event(src["conflict_event_id"],
                                               "resolved",
                                               "reflection proposal applied")
                    except Exception:
                        pass
        else:
            pending += 1  # 保持 pending，进 /proposals 待审

    with db.get_session() as s:
        scan_after = health_scan(s)

    record_run("reflect")
    return {
        "ok": True, "skipped": False,
        "health_before": scan["snapshot"], "health_after": scan_after["snapshot"],
        "proposals_created": len(props), "auto_applied": auto_applied,
        "pending": pending, "discarded": discarded,
        "waterline": round(waterline, 2),
    }
