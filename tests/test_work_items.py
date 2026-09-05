"""案牍投影与控制台 API：纯函数 + 真实 SQLite 冒烟。"""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from lantai.models.tables import (
    ConflictEvent,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    ParamSuggestion,
    RawDocument,
    SchedulerRun,
    SkillCrystal,
)
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash


def test_candidate_group_ids_exact_rules():
    from lantai.services.work_item_service import candidate_group_ids
    rows = [
        {"id": "a", "document_id": "doc-1", "summary": "第一条"},
        {"id": "b", "document_id": "doc-1", "summary": "不同内容"},
        {"id": "c", "document_id": "doc-2", "summary": " 完 全 相 同 "},
        {"id": "d", "document_id": "doc-3", "summary": "完全相同"},
        {"id": "e", "document_id": "doc-4", "summary": "只是语义相近"},
    ]
    groups = candidate_group_ids(rows)
    assert groups["a"] == groups["b"]
    assert groups["c"] == groups["d"]
    assert "e" not in groups


def test_project_work_items_priority_is_deterministic():
    from lantai.services.work_item_service import project_work_items
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    snapshot = {
        "candidates": [{
            "id": "cand", "document_id": "doc", "summary": "即将到期",
            "status": "pending_review", "review_due_at": now + timedelta(hours=3),
            "created_at": now - timedelta(days=2), "topic": [], "lane": "fact",
            "extractor_confidence": .7,
        }],
        "proposals": [], "conflicts": [], "parameters": [], "crystals": [],
        "memories": [], "scheduler_runs": [], "current_param_hash": "x",
        "worker_schedules": {},
    }
    first = project_work_items(snapshot, now=now)
    second = project_work_items(snapshot, now=now)
    assert first == second
    assert first[0].section == "immediate_action"
    assert first[0].priority == "high"


def _seed_all_sources(session_factory):
    now = datetime.now(UTC)
    current_hash = snapshot_hash(default_snapshot())
    with session_factory() as s:
        s.add(RawDocument(id="doc-1", source_type="manual", source_id="src-1",
                          url="", title="来源", content_hash="hash-work-item",
                          content="这是用于案牍测试的来源正文。"))
        s.add(MemoryCandidate(
            id="cand-1", document_id="doc-1", summary="待审候选", status="pending_review",
            review_due_at=now + timedelta(days=2), extractor_confidence=.6))
        s.add(MemoryProposal(
            id="prop-1", proposal_type="add", status="pending", reason="新增知识",
            proposed_patch={"content": "新内容"}, confidence=.8))
        s.add(MemoryItem(
            id="mem-1", memory_type="semantic", key="未分类", content="未分类记忆内容",
            lane="fact", status="active", tree_path=None))
        s.add(ConflictEvent(
            id="conf-1", memory_id="mem-1", incoming_ref="相反内容",
            rule_name="mutex", status="open"))
        s.add(ParamSuggestion(
            id="psg-1", run_id="run-1", status="pending", confidence=.8,
            title="参数建议", summary="调整检索权重", changes=[], before_snapshot={},
            after_snapshot={}, base_snapshot_hash=current_hash,
            fingerprint="sha256:work-item", source_document_ids=["doc-1"]))
        s.add(SkillCrystal(
            id="crystal-1", skill_name="整理发布步骤", trigger_rule="发布时触发",
            procedure="- 检查\n- 发布", status="candidate"))
        from lantai.services.work_item_service import worker_schedule_specs
        for name in worker_schedule_specs():
            s.add(SchedulerRun(name=name, last_run_utc=now.isoformat()))
        s.commit()


def test_real_db_aggregates_all_domain_kinds(param_env):
    session_factory, _ = param_env
    _seed_all_sources(session_factory)
    from lantai.services.work_item_service import get_work_item_detail, list_work_items
    result = list_work_items(limit=100)
    kinds = {item.kind for item in result.items}
    assert {"candidate", "proposal", "conflict", "parameter", "crystal", "memory"} <= kinds
    detail = get_work_item_detail("candidate", "cand-1")
    assert detail.related["document"]["title"] == "来源"


def test_work_item_routes_real_db(param_env):
    session_factory, _ = param_env
    _seed_all_sources(session_factory)
    from api_server import app
    with TestClient(app) as client:
        response = client.get("/work-items?limit=100")
        assert response.status_code == 200
        assert response.json()["total"] >= 6
        detail = client.get("/work-items/detail/candidate/cand-1")
        assert detail.status_code == 200
        assert detail.json()["item"]["kind"] == "candidate"


def test_batch_reject_is_partial_and_real_db(param_env):
    session_factory, _ = param_env
    _seed_all_sources(session_factory)
    from lantai.models.work_items import BatchItemRef, BatchRejectRequest
    from lantai.services.work_item_action_service import batch_reject
    result = batch_reject(BatchRejectRequest(
        reason="测试拒绝",
        items=[BatchItemRef(kind="candidate", source_id="cand-1"),
               BatchItemRef(kind="proposal", source_id="missing")],
    ))
    assert result.ok is False
    assert len(result.succeeded) == 1
    assert len(result.failed) == 1
    with session_factory() as s:
        assert s.get(MemoryCandidate, "cand-1").status == "rejected"
        assert s.exec(select(MemoryProposal).where(
            MemoryProposal.id == "prop-1")).first().status == "pending"

