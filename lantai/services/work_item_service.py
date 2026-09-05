"""案牍聚合：把各领域事实记录投影成统一控制台待办。"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    ConflictEvent,
    IngestJob,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    ParamAdviceRun,
    ParamOverride,
    ParamSuggestion,
    RawDocument,
    ReflectRun,
    SchedulerRun,
    SkillCrystal,
)
from lantai.models.work_items import WorkItem, WorkItemDetailResponse, WorkItemListResponse
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash
from lantai.storage import db

_PROCESS_STARTED_AT = utcnow()
_TYPE_ORDER = {
    "candidate": 0,
    "conflict": 1,
    "proposal": 2,
    "parameter": 3,
    "crystal": 4,
    "memory": 5,
    "worker": 6,
}
_SECTION_ORDER = {
    "immediate_action": 0,
    "pending_decisions": 1,
    "organization_needed": 2,
    "runtime_status": 3,
}
_PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _normalize_duplicate_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().casefold())


def candidate_group_ids(rows: list[dict[str, Any]]) -> dict[str, str]:
    """同原文或规范化摘要完全相同的候选归组；仅返回真实重复组。"""
    parent = {str(row["id"]): str(row["id"]) for row in rows}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    first_by_key: dict[str, str] = {}
    for row in rows:
        row_id = str(row["id"])
        keys = []
        document_id = str(row.get("document_id") or "").strip()
        normalized = _normalize_duplicate_text(str(row.get("summary") or ""))
        if document_id:
            keys.append(f"doc:{document_id}")
        if normalized:
            keys.append(f"text:{normalized}")
        for key in keys:
            if key in first_by_key:
                union(row_id, first_by_key[key])
            else:
                first_by_key[key] = row_id

    groups: dict[str, list[str]] = {}
    for row_id in parent:
        groups.setdefault(find(row_id), []).append(row_id)
    result: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        digest = hashlib.sha256("|".join(sorted(members)).encode("utf-8")).hexdigest()[:12]
        for member in members:
            result[member] = f"candidate:{digest}"
    return result


def worker_schedule_specs() -> dict[str, dict[str, Any]]:
    """受控制台观测的 worker 周期；内部刷新/coalesce 不暴露。"""
    return {
        "ingest": {"seconds": settings.INGEST_CRON_MINUTES * 60, "enabled": True},
        "evolve": {"seconds": settings.EVOLVE_CRON_MINUTES * 60, "enabled": True},
        "forgetting": {"seconds": settings.FORGET_CRON_HOURS * 3600, "enabled": True},
        "candidate_ttl": {
            "seconds": settings.CANDIDATE_TTL_CRON_HOURS * 3600,
            "enabled": True,
        },
        "digest": {"seconds": 86400, "enabled": settings.DIGEST_ENABLED},
        "param_advice": {
            "seconds": settings.PARAM_ADVICE_CRON_MINUTES * 60,
            "enabled": settings.PARAM_ADVICE_ENABLED,
        },
        "reflect": {"seconds": 86400, "enabled": settings.REFLECT_ENABLED},
        "autodream": {
            "seconds": settings.AUTODREAM_CRON_DAYS * 86400,
            "enabled": settings.AUTODREAM_ENABLED,
        },
    }


def _work_item_sort_key(item: WorkItem) -> tuple:
    due = _aware(item.due_at) or datetime.max.replace(tzinfo=UTC)
    created = _aware(item.created_at) or datetime.max.replace(tzinfo=UTC)
    return (
        _SECTION_ORDER[item.section],
        _PRIORITY_ORDER[item.priority],
        due,
        created,
        _TYPE_ORDER[item.kind],
        item.id,
    )


def project_work_items(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    process_started_at: datetime | None = None,
) -> list[WorkItem]:
    """纯函数：领域快照 -> 稳定、互斥分区的案牍列表。"""
    now = _aware(now) or utcnow()
    process_started_at = _aware(process_started_at) or _PROCESS_STARTED_AT
    items: list[WorkItem] = []

    candidates = list(snapshot.get("candidates", []))
    group_ids = candidate_group_ids(candidates)
    for row in candidates:
        created = _aware(row.get("created_at"))
        due = _aware(row.get("review_due_at"))
        urgent = due is not None and due <= now + timedelta(hours=24)
        overdue = due is not None and due <= now
        items.append(WorkItem(
            id=f"candidate:{row['id']}", kind="candidate", source_id=row["id"],
            title=(row.get("summary") or "待审候选")[:160],
            summary=" / ".join(str(v) for v in (row.get("topic") or [])[:4]),
            section="immediate_action" if urgent else "pending_decisions",
            priority="critical" if overdue else ("high" if urgent else "normal"),
            reason="候选已逾期" if overdue else (
                "候选将在 24 小时内到期" if urgent else "等待人工判断是否进入提案链"),
            risk="high" if overdue else "medium", status=row.get("status", "pending_review"),
            created_at=created, updated_at=row.get("deferred_at") or created, due_at=due,
            allowed_actions=["defer", "reject", "approve"],
            badges=[row.get("lane", "general"),
                    f"置信 {float(row.get('extractor_confidence') or 0):.2f}"],
            related_refs=[{"kind": "document", "id": row.get("document_id", "")}],
            group_id=group_ids.get(str(row["id"])),
        ))

    stale_after = now - timedelta(days=7)
    for row in snapshot.get("proposals", []):
        created = _aware(row.get("created_at"))
        stale = bool(created and created <= stale_after)
        proposal_type = row.get("proposal_type", "change")
        items.append(WorkItem(
            id=f"proposal:{row['id']}", kind="proposal", source_id=row["id"],
            title=f"{proposal_type} 提案",
            summary=(row.get("reason") or row.get("proposed_patch", {}).get("content") or "")[:240],
            section="immediate_action" if stale else "pending_decisions",
            priority="high" if stale else "normal",
            reason="提案等待超过 7 天" if stale else "等待最终写入裁决",
            risk="high" if proposal_type in {"merge", "deprecate"} else "medium",
            status=row.get("status", "pending"), created_at=created, updated_at=created,
            allowed_actions=["reject", "approve"],
            badges=[proposal_type, f"置信 {float(row.get('confidence') or 0):.2f}"],
            related_refs=[
                {"kind": "candidate", "id": row["candidate_id"]}
                for _ in [0] if row.get("candidate_id")
            ] + [
                {"kind": "memory", "id": row["target_memory_id"]}
                for _ in [0] if row.get("target_memory_id")
            ],
        ))

    for row in snapshot.get("conflicts", []):
        created = _aware(row.get("created_at"))
        items.append(WorkItem(
            id=f"conflict:{row['id']}", kind="conflict", source_id=row["id"],
            title=f"{row.get('rule_name') or '规则'} 冲突",
            summary=(row.get("incoming_ref") or str(row.get("detail") or ""))[:240],
            section="immediate_action", priority="high", reason="存在未处理冲突",
            risk="high", status=row.get("status", "open"), created_at=created,
            updated_at=created, allowed_actions=["resolve", "dismiss"],
            badges=[row.get("kind", "mutex")],
            related_refs=[{"kind": "memory", "id": row.get("memory_id", "")}],
        ))

    current_param_hash = snapshot.get("current_param_hash", "")
    for row in snapshot.get("parameters", []):
        created = _aware(row.get("created_at"))
        stale_base = bool(current_param_hash and row.get("base_snapshot_hash") != current_param_hash)
        stale_age = bool(created and created <= stale_after)
        urgent = stale_base or stale_age
        actions = ["reject", "regenerate"] if stale_base else ["reject", "approve"]
        items.append(WorkItem(
            id=f"parameter:{row['id']}", kind="parameter", source_id=row["id"],
            title=(row.get("title") or "参数建议")[:160], summary=(row.get("summary") or "")[:240],
            section="immediate_action" if urgent else "pending_decisions",
            priority="high" if urgent else "normal",
            reason="参数基线已变化" if stale_base else (
                "参数建议等待超过 7 天" if stale_age else "等待参数变更裁决"),
            risk="high" if stale_base else "medium", status=row.get("status", "pending"),
            created_at=created, updated_at=created, allowed_actions=actions,
            badges=[f"置信 {float(row.get('confidence') or 0):.2f}",
                    f"{len(row.get('changes') or [])} 项变更"],
            related_refs=[
                {"kind": "document", "id": source_id}
                for source_id in (row.get("source_document_ids") or [])[:5]
            ],
        ))

    for row in snapshot.get("crystals", []):
        created = _aware(row.get("created_at"))
        updated = _aware(row.get("updated_at")) or created
        stale = bool(created and created <= stale_after)
        items.append(WorkItem(
            id=f"crystal:{row['id']}", kind="crystal", source_id=row["id"],
            title=(row.get("skill_name") or "技能结晶")[:160],
            summary=(row.get("trigger_rule") or row.get("procedure") or "")[:240],
            section="immediate_action" if stale else "pending_decisions",
            priority="high" if stale else "normal",
            reason="技能结晶等待超过 7 天" if stale else "等待补全步骤并裁决",
            risk="medium", status=row.get("status", "candidate"), created_at=created,
            updated_at=updated, allowed_actions=["reject", "approve"],
            badges=[f"{int(row.get('candidate_count') or 0)} 条样本",
                    f"命中 {int(row.get('hit_count') or 0)} 次"],
            related_refs=[
                {"kind": "memory_key", "id": key}
                for key in (row.get("sample_keys") or [])[:5]
            ],
        ))

    for row in snapshot.get("memories", []):
        created = _aware(row.get("created_at"))
        updated = _aware(row.get("updated_at")) or created
        items.append(WorkItem(
            id=f"memory:{row['id']}", kind="memory", source_id=row["id"],
            title=(row.get("key") or row.get("content") or "未分类记忆")[:160],
            summary=(row.get("content") or "")[:240], section="organization_needed",
            priority="low", reason="活跃记忆尚未挂载分类树", risk="low",
            status=row.get("status", "active"), created_at=created, updated_at=updated,
            allowed_actions=["organize"],
            badges=[row.get("lane", "general"), row.get("memory_type", "memory")],
        ))

    explicit_workers: set[str] = set()
    latest_ingest = snapshot.get("latest_ingest_job")
    if latest_ingest and latest_ingest.get("status") == "failed":
        explicit_workers.add("ingest")
        created = _aware(latest_ingest.get("started_at") or latest_ingest.get("finished_at"))
        items.append(WorkItem(
            id=f"worker:ingest:{latest_ingest['id']}", kind="worker",
            source_id="ingest", title="摄取任务失败",
            summary=(latest_ingest.get("error") or "摄取任务未完成")[:240],
            section="immediate_action", priority="critical", reason="worker 明确失败",
            risk="critical", status="failed", created_at=created,
            updated_at=_aware(latest_ingest.get("finished_at")) or created,
            allowed_actions=["run", "refresh"], badges=["ingest"],
        ))

    latest_param = snapshot.get("latest_param_run")
    if latest_param and latest_param.get("status") == "failed":
        explicit_workers.add("param_advice")
        created = _aware(latest_param.get("created_at"))
        items.append(WorkItem(
            id=f"worker:param_advice:{latest_param['id']}", kind="worker",
            source_id="param_advice", title="参数建议任务失败",
            summary=(latest_param.get("error_code") or "参数建议任务未完成")[:240],
            section="immediate_action", priority="critical", reason="worker 明确失败",
            risk="critical", status="failed", created_at=created,
            updated_at=_aware(latest_param.get("finished_at")) or created,
            allowed_actions=["run", "refresh"], badges=["param_advice"],
        ))

    latest_reflect = snapshot.get("latest_reflect_run")
    if latest_reflect:
        created = _aware(latest_reflect.get("run_at"))
        if latest_reflect.get("error"):
            explicit_workers.add("reflect")
            items.append(WorkItem(
                id=f"worker:reflect:{latest_reflect['id']}", kind="worker",
                source_id="reflect", title="反思任务失败",
                summary=str(latest_reflect.get("error"))[:240], section="immediate_action",
                priority="critical", reason="worker 明确失败", risk="critical",
                status="failed", created_at=created, updated_at=created,
                allowed_actions=["run", "refresh"], badges=["reflect"],
            ))
        elif latest_reflect.get("curate_failed") or latest_reflect.get("rejecter_failed"):
            explicit_workers.add("reflect")
            parts = []
            if latest_reflect.get("curate_failed"):
                parts.append("curator LLM 失败")
            if latest_reflect.get("rejecter_failed"):
                parts.append(f"rejecter LLM 失败 {latest_reflect['rejecter_failed']} 次")
            items.append(WorkItem(
                id=f"worker:reflect:{latest_reflect['id']}", kind="worker",
                source_id="reflect", title="反思任务降级完成", summary="；".join(parts),
                section="runtime_status", priority="high", reason="外部模型调用出现失败",
                risk="medium", status="warning", created_at=created, updated_at=created,
                allowed_actions=["run", "refresh"], badges=["reflect", "LLM warning"],
            ))

    last_runs = {row["name"]: _parse_iso(row.get("last_run_utc"))
                 for row in snapshot.get("scheduler_runs", [])}
    for name, spec in snapshot.get("worker_schedules", {}).items():
        if not spec.get("enabled") or name in explicit_workers:
            continue
        period = timedelta(seconds=max(1, int(spec["seconds"])))
        grace = max(period * 0.25, timedelta(minutes=15))
        last_run = last_runs.get(name)
        baseline = last_run or process_started_at
        due = baseline + period + grace
        if now <= due:
            continue
        elapsed = now - baseline
        critical = elapsed > period * 2
        items.append(WorkItem(
            id=f"worker:{name}:overdue", kind="worker", source_id=name,
            title=f"{name} 未按计划运行",
            summary=f"上次完成：{last_run.isoformat() if last_run else '本次启动后尚无记录'}",
            section="immediate_action", priority="critical" if critical else "high",
            reason="超过两个完整周期" if critical else "超过运行周期与宽限期",
            risk="critical" if critical else "high", status="overdue",
            created_at=baseline, updated_at=last_run, due_at=due,
            allowed_actions=["run", "refresh"], badges=[name, "逾期"],
        ))

    return sorted(items, key=_work_item_sort_key)


def _current_param_hash(session) -> str:
    head = session.exec(select(ParamOverride).order_by(ParamOverride.revision.desc())).first()
    return head.after_snapshot_hash if head else snapshot_hash(default_snapshot())


def load_work_item_snapshot() -> dict[str, Any]:
    """从真实领域表读取案牍所需的最小快照。"""
    with db.get_session() as s:
        candidates = s.exec(select(MemoryCandidate).where(
            MemoryCandidate.status == "pending_review")).all()
        proposals = s.exec(select(MemoryProposal).where(
            MemoryProposal.status == "pending")).all()
        conflicts = s.exec(select(ConflictEvent).where(
            ConflictEvent.status == "open")).all()
        parameters = s.exec(select(ParamSuggestion).where(
            ParamSuggestion.status == "pending")).all()
        crystals = s.exec(select(SkillCrystal).where(
            SkillCrystal.status == "candidate")).all()
        memories = s.exec(select(MemoryItem).where(
            MemoryItem.status == "active",
            (MemoryItem.tree_path.is_(None)) | (MemoryItem.tree_path == ""),
        )).all()
        scheduler_runs = s.exec(select(SchedulerRun)).all()
        latest_ingest = s.exec(select(IngestJob).order_by(
            IngestJob.started_at.desc(), IngestJob.finished_at.desc())).first()
        latest_param = s.exec(select(ParamAdviceRun).order_by(
            ParamAdviceRun.created_at.desc())).first()
        latest_reflect = s.exec(select(ReflectRun).order_by(
            ReflectRun.run_at.desc())).first()
        return {
            "candidates": [row.model_dump() for row in candidates],
            "proposals": [row.model_dump() for row in proposals],
            "conflicts": [row.model_dump() for row in conflicts],
            "parameters": [row.model_dump() for row in parameters],
            "crystals": [row.model_dump() for row in crystals],
            "memories": [row.model_dump() for row in memories],
            "scheduler_runs": [row.model_dump() for row in scheduler_runs],
            "latest_ingest_job": latest_ingest.model_dump() if latest_ingest else None,
            "latest_param_run": latest_param.model_dump() if latest_param else None,
            "latest_reflect_run": latest_reflect.model_dump() if latest_reflect else None,
            "current_param_hash": _current_param_hash(s),
            "worker_schedules": worker_schedule_specs(),
        }


def list_work_items(
    *, section: str = "", kind: str = "", risk: str = "", query: str = "",
    limit: int = 50, offset: int = 0,
) -> WorkItemListResponse:
    items = project_work_items(load_work_item_snapshot())
    query_norm = (query or "").strip().casefold()
    filtered = [item for item in items if (
        (not kind or item.kind == kind)
        and (not risk or item.risk == risk)
        and (not query_norm or query_norm in (
            f"{item.title} {item.summary} {item.reason} {' '.join(item.badges)}".casefold()))
    )]
    counts = Counter(item.section for item in filtered)
    if section:
        filtered = [item for item in filtered if item.section == section]
    total = len(filtered)
    return WorkItemListResponse(
        items=filtered[offset:offset + limit], total=total,
        counts={name: counts.get(name, 0) for name in _SECTION_ORDER},
        limit=limit, offset=offset,
    )


def get_work_item_detail(kind: str, source_id: str) -> WorkItemDetailResponse:
    items = project_work_items(load_work_item_snapshot())
    item = next((value for value in items
                 if value.kind == kind and value.source_id == source_id), None)
    if item is None:
        raise ValueError("work item not found or no longer pending")

    with db.get_session() as s:
        source: dict[str, Any]
        related: dict[str, Any] = {}
        if kind == "candidate":
            row = s.get(MemoryCandidate, source_id)
            source = row.model_dump(mode="json")
            document = s.get(RawDocument, row.document_id)
            related["document"] = document.model_dump(mode="json") if document else None
            peers = s.exec(select(MemoryCandidate).where(
                MemoryCandidate.status == "pending_review")).all()
            norm = _normalize_duplicate_text(row.summary)
            related["duplicates"] = [peer.model_dump(mode="json") for peer in peers
                if peer.id != row.id and (
                    peer.document_id == row.document_id
                    or (norm and _normalize_duplicate_text(peer.summary) == norm))]
            props = s.exec(select(MemoryProposal).where(
                MemoryProposal.candidate_id == source_id)).all()
            related["proposals"] = [prop.model_dump(mode="json") for prop in props]
        elif kind == "proposal":
            row = s.get(MemoryProposal, source_id)
            source = row.model_dump(mode="json")
            related["candidate"] = (
                s.get(MemoryCandidate, row.candidate_id).model_dump(mode="json")
                if row.candidate_id and s.get(MemoryCandidate, row.candidate_id) else None)
            related["target_memory"] = (
                s.get(MemoryItem, row.target_memory_id).model_dump(mode="json")
                if row.target_memory_id and s.get(MemoryItem, row.target_memory_id) else None)
            related["conflicts"] = [
                value.model_dump(mode="json") for conflict_id in row.conflict_ids
                if (value := s.get(ConflictEvent, conflict_id)) is not None]
        elif kind == "conflict":
            row = s.get(ConflictEvent, source_id)
            source = row.model_dump(mode="json")
            memory = s.get(MemoryItem, row.memory_id)
            related["memory"] = memory.model_dump(mode="json") if memory else None
        elif kind == "parameter":
            row = s.get(ParamSuggestion, source_id)
            source = row.model_dump(mode="json")
            current_hash = _current_param_hash(s)
            related["current_param_hash"] = current_hash
            related["base_stale"] = row.base_snapshot_hash != current_hash
            docs = s.exec(select(RawDocument).where(
                RawDocument.id.in_(row.source_document_ids))).all()
            related["documents"] = [doc.model_dump(mode="json") for doc in docs]
        elif kind == "crystal":
            row = s.get(SkillCrystal, source_id)
            source = row.model_dump(mode="json")
            memories = s.exec(select(MemoryItem).where(
                MemoryItem.key.in_(row.sample_keys))).all() if row.sample_keys else []
            related["memories"] = [memory.model_dump(mode="json") for memory in memories]
        elif kind == "memory":
            row = s.get(MemoryItem, source_id)
            source = row.model_dump(mode="json")
            from lantai.services.tree_service import get_subtree
            related["tree"] = get_subtree(s, "/")
        else:
            source = {"worker": source_id, "status": item.status,
                      "reason": item.reason, "summary": item.summary}
            related["schedule"] = worker_schedule_specs().get(source_id, {})
        return WorkItemDetailResponse(item=item, source=source, related=related)

