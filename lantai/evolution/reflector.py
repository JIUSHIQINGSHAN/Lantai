from fastapi import HTTPException

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, MemoryUsageFeedback
from lantai.storage import db


def record_feedback(
    memory_id: str,
    query: str,
    helped: bool,
    user_accepted: bool,
    hallucination_risk: float,
    principal=None,
) -> dict:
    """登记检索反馈并回写记忆权重。

    归属（票 .scratch/readside-gaps/13）：此前只按 id 取行、一个身份都不取——
    任何持 key 者都能刷别人的 `use_count` / `helpful_count` / `importance`，
    而这三个字段正是考功与遗忘的**输入**（改它们等于间接操控别人的演化结果）。
    校验复用 `acl.ensure_can_delete` 单一真源（同票 04/06/11 写侧范式）。

    `principal=None`（worker/CLI/scheduler）保持全表，与已修各票逐字一致。
    """
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False}
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            try:
                ensure_can_delete(
                    principal,
                    resource_user_id=mem.user_id,
                    resource_tenant_id=mem.tenant_id,
                    lane=mem.lane,
                )
            except HTTPException as exc:
                # service 契约是 dict（被 worker/eval/MCP 多处消费，形状不能动），
                # 403 语义在路由边界由 _ok_or_raise 翻译；这里只报「不允许」。
                return {"ok": False, "reason": f"forbidden: {exc.detail}"}
        delta = (
            (0.1 if helped else -0.05) + (0.1 if user_accepted else 0) - 0.2 * hallucination_risk
        )
        mem.use_count += 1
        mem.helpful_count += int(helped)
        mem.importance = max(0.0, min(1.0, mem.importance + delta))
        mem.last_used_at = utcnow()
        s.add(mem)
        fb = MemoryUsageFeedback(
            id=new_id("fb"),
            memory_id=memory_id,
            query=query,
            helped=helped,
            user_accepted=user_accepted,
            hallucination_risk=hallucination_risk,
            score_delta=delta,
        )
        s.add(fb)

        # 闭环：当用户明确拒绝、未帮助或存在高幻觉风险时，生成 ActionOutcome 与 FailureRecord
        if not helped or not user_accepted or hallucination_risk >= 0.5:
            from lantai.models.tables import ActionOutcome, FailureRecord

            act_id = new_id("out")
            action_out = ActionOutcome(
                id=act_id,
                task_id=query[:100] or "retrieval_feedback",
                action_type="retrieval_response",
                action_summary=f"Query: {query[:120]} -> Memory: {mem.content[:120]}",
                success=False,
                score=max(0.0, 0.5 + delta),
                side_effects=["retrieval_rejected_or_hallucinated"],
                feedback={
                    "query": query,
                    "helped": helped,
                    "user_accepted": user_accepted,
                    "hallucination_risk": hallucination_risk,
                },
                created_at=utcnow(),
            )
            s.add(action_out)

            fail_rec = FailureRecord(
                id=new_id("fail"),
                task=query[:100] or "retrieval_task",
                action="recall_memory",
                expected="helpful and truthful recall",
                actual=f"Rejected by user or hallucination risk {hallucination_risk:.2f}",
                cause=f"Memory {mem.id} provided insufficient or conflicting facts",
                lesson=f"Review and verify memory [{mem.key or mem.id}]: {mem.content[:80]}",
                severity=max(0.4, hallucination_risk),
                recurrence_count=1,
                source_ids=[mem.id, fb.id, act_id],
                created_at=utcnow(),
            )
            s.add(fail_rec)

        s.commit()
        return {"ok": True, "importance": mem.importance}


# ── 反思/蒸馏（spec: docs/plans/reflection-module-spec.md）─────────────
from datetime import UTC, timedelta

from sqlmodel import select

from lantai.core.logger import logger
from lantai.core.scheduler import record_run
from lantai.core.settings import settings
from lantai.llm.client import chat_json
from lantai.llm.prompts import REFLECT_CURATOR_SYS, REFLECT_REJECTER_SYS
from lantai.models.enums import ProposalStatus
from lantai.models.tables import ConflictEvent, MemoryEdge, MemoryProposal
from lantai.services.prompt_service import get_prompt

_VALID_TYPES = {"add", "update", "merge", "deprecate"}


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _cand(mem: MemoryItem, signal: str, extra: dict | None = None) -> dict:
    c = {
        "memory_id": mem.id,
        "key": mem.key,
        "content": mem.content,
        "lane": mem.lane,
        "importance": mem.importance,
        "signal": signal,
    }
    if extra:
        c.update(extra)
    return c


def _reflect_scope(principal):
    """反思扫描的归属条件（票 .scratch/readside-gaps/14）。

    admin / `principal=None` → None（不过滤）；否则
    `user_id == viewer OR IS NULL`，口径与票 12 的
    `_consolidation_scope` / `_kaogong_scope` 逐字一致。

    NULL 口径同票 03/04/06/09/10：真实库 615 行 `user_id IS NULL` 的
    memoryitem，判「不可见」会让单人部署下的反思整体空转。NULL 是
    「未记录」不是「属于所有人」。

    `principal=None`（scheduler 定时任务 / worker）保持全表：反思是系统
    行为，收窄成空转会让 open 冲突账本与陈旧记忆永远没人处理。**这个
    口径是刻意的**，别让后来人以为漏了（同票据口径 4）。
    """
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))


def health_scan(session, principal=None) -> dict:
    """健康扫描：问题驱动反思输入（零 LLM，纯 SQL）。

    归属（票 .scratch/readside-gaps/14）：此前三处扫描全表、一个身份都
    不取——A 触发一次反思，B 的记忆正文就被拼进 `_curate` 的 user prompt
    发往外部 LLM。`wrap_as_data` 的围栏只防注入不防归属，所以这里必须
    收窄候选集本身。

    规则 R1-R3 默认开（superseded 残留 / 过期时间窗 / open 冲突账本），
    R4/R5 受 REFLECT_STALE_SCAN_ENABLED 控制（低帮助率 / 低价值陈旧）。
    返回 {"snapshot": {...}, "candidates": [...]}（候选截断到 REFLECT_MAX_BATCH）。
    """
    now = utcnow()
    superseded_by: dict[str, str] = {}
    for e in session.exec(select(MemoryEdge).where(MemoryEdge.relation == "supersedes")).all():
        superseded_by.setdefault(e.target_memory_id, e.source_memory_id)

    scope = _reflect_scope(principal)
    from lantai.services.work_item_service import _viewer_of

    viewer = None if scope is None else _viewer_of(principal)
    q = select(MemoryItem).where(MemoryItem.status == "active")
    if scope is not None:
        q = q.where(scope)
    candidates: list[dict] = []
    for m in session.exec(q).all():
        if m.id in superseded_by:
            candidates.append(_cand(m, "superseded", {"superseded_by": superseded_by[m.id]}))
            continue
        vt = _as_utc(m.valid_to)
        if vt is not None and vt < now and m.decay_class != "procedural":
            candidates.append(_cand(m, "expired"))
            continue
        if settings.REFLECT_STALE_SCAN_ENABLED:
            if (
                m.use_count >= settings.REFLECT_MIN_USE_COUNT
                and m.helpful_count / m.use_count <= settings.REFLECT_LOW_HELPFUL_RATIO
            ):
                candidates.append(_cand(m, "low_helpful"))
                continue
            last = _as_utc(m.last_used_at or m.created_at)
            age_days = max(0.0, (now - last).total_seconds() / 86400.0)
            if (
                age_days >= settings.REFLECT_STALE_AGE_DAYS
                and m.use_count == 0
                and m.importance < settings.REFLECT_STALE_IMPORTANCE
                and m.decay_class != "procedural"
            ):
                candidates.append(_cand(m, "stale_low_value"))

    for ev in session.exec(select(ConflictEvent).where(ConflictEvent.status == "open")).all():
        m = session.get(MemoryItem, ev.memory_id)
        # `session.get` 走主键直读，绕开上面的 scope——open 冲突账本会
        # 指向任何人的记忆，这里必须补一次归属判定（宁 miss 不脏写）。
        # scope 非 None 时 viewer 一定已算出（`_reflect_scope` 的两条
        # 早退路径都返回 None），故这里不必重复判 admin。
        if (
            m
            and m.status == "active"
            and (scope is None or m.user_id == viewer or m.user_id is None)
        ):
            candidates.append(
                _cand(m, "open_conflict", {"conflict_event_id": ev.id, "detail": ev.detail})
            )

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


def _importance_waterline(session, principal=None) -> float:
    """近窗口新增记忆 importance 累加（水位触发；无持久化时间戳，近似实现）。"""
    now = utcnow()
    start = now - timedelta(days=settings.REFLECT_IMPORTANCE_WINDOW_DAYS)
    total = 0.0
    q = select(MemoryItem)
    scope = _reflect_scope(principal)
    if scope is not None:
        q = q.where(scope)
    for m in session.exec(q).all():
        created = _as_utc(m.created_at)
        if created is not None and created >= start:
            total += m.importance
    return total


def _curate(candidates: list[dict], related_texts: str) -> dict:
    """阶段 1：curator 蒸馏提案（strict JSON；异常降级为空，宁 miss）。"""
    if not candidates:
        return {"proposals": []}
    from lantai.llm.fence import wrap_as_data

    batch_text = "\n".join(
        f"- [{c['memory_id']}] lane={c['lane']} importance={c['importance']:.2f} "
        f"signal={c['signal']} {c['key']}: "
        f"{wrap_as_data(str(c['content'])[:200], item_id=c['memory_id'])}"
        for c in candidates
    )
    user = (
        f"FLAGGED MEMORIES:\n{batch_text}\n\n"
        f"RELATED EXISTING MEMORIES:\n"
        f"{wrap_as_data(related_texts) if related_texts else '(none)'}"
    )
    try:
        return chat_json(get_prompt("REFLECT_CURATOR_SYS", REFLECT_CURATOR_SYS), user)
    except Exception:
        return {"proposals": [], "curate_failed": True}


def _reject(prop: MemoryProposal, evidence_texts: str) -> dict:
    """阶段 2：rejecter 复核（防幻觉蒸馏；异常按不通过处理，宁 miss）。"""
    if not evidence_texts.strip():
        return {"accept": False, "risk": "high", "reason": "no evidence text"}
    from lantai.llm.fence import wrap_as_data

    patch = prop.proposed_patch or {}
    user = (
        f"PROPOSAL:\ntype={prop.proposal_type} "
        f"target={prop.target_memory_id}\n"
        f"content={wrap_as_data(str(patch.get('content', '')))}\n"
        f"reason={prop.reason}\n\nEVIDENCE:\n{wrap_as_data(evidence_texts)}"
    )
    try:
        return chat_json(get_prompt("REFLECT_REJECTER_SYS", REFLECT_REJECTER_SYS), user)
    except Exception:
        return {
            "accept": False,
            "risk": "high",
            "reason": "rejecter unavailable",
            "unavailable": True,
        }


def propose_from_reflection(session, candidates: list[dict], curated: dict) -> list[MemoryProposal]:
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
        evidence = [
            e for e in (p.get("evidence_ids") or []) if session.get(MemoryItem, e) is not None
        ]
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
                "lane": p.get("lane")
                or (candidates[0]["lane"] if candidates else settings.DEFAULT_LANE),
            },
            confidence=conf,
            conflict_ids=[],
            status=ProposalStatus.PENDING,
            # 唯一来源标识（ADR/校准口径）：digest 反思统计按 decided_by='reflect'
            # 过滤；evolve 自动路径为 'auto'、autodream 为 'autodream'（勿混）
            decided_by="reflect",
        )
        session.add(prop)
        props.append(prop)
    session.commit()
    for p in props:
        session.refresh(p)
    return props


_REFLECT_RUN_SOURCES = frozenset({"scheduled", "manual", "unknown"})


def _record_reflect_run(run_at=None, **fields) -> None:
    """反思运行结论落库（reflect_run 表；失败不阻断运行，宁 miss 不静默）。"""
    try:
        from lantai.models.tables import ReflectRun

        with db.get_session() as s:
            s.add(ReflectRun(id=new_id("run"), run_at=run_at or utcnow(), **fields))
            s.commit()
    except Exception as exc:
        logger.warning("reflect_run 落库失败（审计留痕不静默）: %s", exc, exc_info=True)


def _safe_waterline(principal=None) -> float:
    """异常路径尽力补水位（读取失败不阻断留痕，宁 miss 不静默）。"""
    try:
        with db.get_session() as s:
            return round(_importance_waterline(s, principal=principal), 2)
    except Exception:
        return 0.0


def run_reflect_once(source: str = "unknown", principal=None) -> dict:
    """反思主入口（异常留痕后原样抛出，供调度器日志/下轮重试）。

    归属（票 .scratch/readside-gaps/14）：`principal` 是**可选**形参——
    scheduler / worker 不传（保持全表，见 `_reflect_scope` 口径注释），
    REST / MCP 入口传（按 `user_id` 收窄）。默认 None 保证所有既有内部
    调用方行为逐字不变。
    """
    if source not in _REFLECT_RUN_SOURCES:
        raise ValueError(f"invalid reflect run source: {source}")
    try:
        return _run_reflect_once(source=source, principal=principal)
    except Exception as exc:
        _record_reflect_run(
            source=source,
            waterline=_safe_waterline(principal=principal),
            error=str(exc),
        )
        raise


def _run_reflect_once(source: str, principal=None) -> dict:
    """反思主入口：健康扫描 →（水位触发新记忆蒸馏）→ curator → 提案 → 裁决。

    自动应用（需 REFLECT_AUTO_APPLY=True，默认 False）：confidence >=
    REFLECT_AUTO_APPLY_CONF 且 rejecter risk=low；开关关闭时一律 pending。
    risk=medium 强制 pending；accept=false / risk=high 丢弃（宁 miss）。
    ADR-0050 决策 7a：默认关——后台合成产物不自动生效，人工闸门不被绕过。
    返回统计 + 健康快照前后对比（自证）。

    归属（票 .scratch/readside-gaps/14）：**四处**扫描（候选集 / related /
    theme 触发 / rejecter 的 evidence_ids）必须同批过 scope。只收窄候选集
    是不够的——后三处都是独立路径，漏一处 B 的正文仍会经它们进提示词。
    其中 evidence_ids 来自 LLM 输出、是**攻击者可控字段**，curator 回一个
    别人的记忆 id 就能把那条正文送出去，故改成 `id.in_(...)` + scope 查询，
    不再按主键 `session.get` 直读（那是归属盲区）。
    """
    with db.get_session() as s:
        scan = health_scan(s, principal=principal)
        waterline = _importance_waterline(s, principal=principal)
        q = select(MemoryItem).where(MemoryItem.status == "active")
        scope = _reflect_scope(principal)
        if scope is not None:
            q = q.where(scope)
        related = s.exec(q).all()
        related_texts = "\n".join(f"- ({m.memory_type}) {m.key}: {m.content}" for m in related[:20])

    candidates = scan["candidates"]
    theme_triggered = waterline >= settings.REFLECT_IMPORTANCE_POOL
    if not candidates and not theme_triggered:
        record_run("reflect")
        _record_reflect_run(source=source, waterline=round(waterline, 2), skipped="idle")
        return {
            "ok": True,
            "skipped": "idle",
            "health": scan["snapshot"],
            "waterline": round(waterline, 2),
        }

    if theme_triggered:
        start = utcnow() - timedelta(days=settings.REFLECT_IMPORTANCE_WINDOW_DAYS)
        with db.get_session() as s:
            tq = select(MemoryItem)
            if scope is not None:
                tq = tq.where(scope)
            for m in s.exec(tq).all():
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
    auto_applied = pending = discarded = rejecter_failed = 0
    for prop in props:
        with db.get_session() as s:
            # 归属（票 14，第四条扫描路径）：`prop.evidence_ids` 来自 LLM 输出，
            # 是**攻击者可控字段**——curator 只要回一个别人的记忆 id，这条正文
            # 就被拼进 rejecter 的提示词送出去。候选集收窄管不到这里，
            # 必须同样过 scope（宁 miss 不脏写：越权证据直接不取，
            # rejecter 拿到空文本会按 no evidence text 判 high 风险丢弃）。
            eq = select(MemoryItem).where(MemoryItem.id.in_(prop.evidence_ids))
            if scope is not None:
                eq = eq.where(scope)
            evidence_texts = "\n".join(m.content for m in s.exec(eq).all())
        verdict = _reject(prop, evidence_texts)
        if verdict.get("unavailable"):
            rejecter_failed += 1
        if not verdict.get("accept") or verdict.get("risk") == "high":
            with db.get_session() as s:
                p = s.get(MemoryProposal, prop.id)
                p.status = ProposalStatus.REJECTED
                p.decision_reason = str(verdict.get("reason", ""))
                s.add(p)
                s.commit()
            discarded += 1
            continue
        if (
            settings.REFLECT_AUTO_APPLY
            and prop.confidence >= settings.REFLECT_AUTO_APPLY_CONF
            and verdict.get("risk") == "low"
        ):
            from lantai.evolution.promoter import apply_proposal

            res = apply_proposal(prop.id)
            if res.get("ok"):
                auto_applied += 1
                src = cand_by_id.get(prop.target_memory_id)
                if src and src.get("conflict_event_id"):
                    try:
                        from lantai.services.conflict_service import resolve_conflict_event

                        resolve_conflict_event(
                            src["conflict_event_id"], "resolved", "reflection proposal applied"
                        )
                    except Exception:
                        pass
        else:
            pending += 1  # 保持 pending，进 /proposals 待审

    with db.get_session() as s:
        scan_after = health_scan(s, principal=principal)

    record_run("reflect")
    _record_reflect_run(
        source=source,
        waterline=round(waterline, 2),
        health_before=scan["snapshot"],
        health_after=scan_after["snapshot"],
        proposals_created=len(props),
        auto_applied=auto_applied,
        pending=pending,
        discarded=discarded,
        curate_failed=bool(curated.get("curate_failed")),
        rejecter_failed=rejecter_failed,
    )
    return {
        "ok": True,
        "skipped": False,
        "health_before": scan["snapshot"],
        "health_after": scan_after["snapshot"],
        "proposals_created": len(props),
        "auto_applied": auto_applied,
        "pending": pending,
        "discarded": discarded,
        "rejecter_failed": rejecter_failed,
        "waterline": round(waterline, 2),
    }
