"""反思/蒸馏模块冒烟测试（spec: docs/plans/reflection-module-spec.md 第 4 节）

测试纪律（AGENTS.md）：核心函数不 mock 内部计算逻辑——真实构造最小输入直调函数。
仅允许 mock：LLM（chat_json / embed）与向量存储副作用（get_vector_store）。
FTS5 真实建表（init_fts），DB 用内存 SQLite 真实建表。
"""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select
from unittest.mock import Mock, patch

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import (ConflictEvent, MemoryCheckpoint, MemoryEdge,
                                  MemoryItem, MemoryProposal, ReflectRun)
from lantai.storage.fts import init_fts, search_fts, sync_fts


@contextmanager
def _patch_session(session_factory):
    original = db_module.get_session
    db_module.get_session = session_factory
    try:
        yield
    finally:
        db_module.get_session = original


@pytest.fixture()
def reflect_env():
    """内存 SQLite 真实建表 + FTS5 + patch db.get_session。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with _patch_session(session_factory):
        yield session_factory, engine


def _mem(**kw):
    defaults = dict(
        id="", memory_type="semantic", key="", content="", lane="general",
        status="active", importance=0.5, use_count=0, helpful_count=0,
        decay_score=1.0, decay_class="episodic", confidence=0.5,
    )
    defaults.update(kw)
    if not defaults["id"]:
        defaults["id"] = new_id("mem")
    if not defaults["key"]:
        defaults["key"] = defaults["id"]
    return MemoryItem(**defaults)


def _seed(session_factory, rows):
    with session_factory() as s:
        for r in rows:
            s.add(r)
        s.commit()


def _vector_mocks():
    return patch("lantai.retrieval.hybrid.get_vector_store",
                 return_value=Mock(add=Mock(), delete=Mock()))


def _embed_mock():
    return patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8])


# ── health_scan ──────────────────────────────────────────────

class TestHealthScan:

    def test_superseded_residual(self, reflect_env):
        """R1：supersedes 边的目标仍 active → 命中。"""
        session_factory, _ = reflect_env
        old = _mem(id="mem_old", content="旧域名 example.com")
        new = _mem(id="mem_new", content="新域名 example.org")
        _seed(session_factory, [old, new,
                                MemoryEdge(id="edge_1", source_memory_id="mem_new",
                                           target_memory_id="mem_old",
                                           relation="supersedes", confidence=0.9)])

        from lantai.evolution.reflector import health_scan
        with session_factory() as s:
            scan = health_scan(s)

        assert scan["snapshot"]["superseded_active"] == 1
        assert any(c["memory_id"] == "mem_old" and c["signal"] == "superseded"
                   for c in scan["candidates"])

    def test_expired_window(self, reflect_env):
        """R2：valid_to 已过且 active → 命中。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [_mem(id="mem_exp",
                                     content="一次性会议安排",
                                     valid_to=utcnow() - timedelta(days=1))])

        from lantai.evolution.reflector import health_scan
        with session_factory() as s:
            scan = health_scan(s)

        assert scan["snapshot"]["expired_active"] == 1

    def test_open_conflicts(self, reflect_env):
        """R3：open 冲突账本 → 命中。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_cf", content="端口配置 8080"),
            ConflictEvent(id="cf_1", memory_id="mem_cf", incoming_ref="端口 9090",
                          rule_name="port-mutex", kind="mutex", status="open"),
        ])

        from lantai.evolution.reflector import health_scan
        with session_factory() as s:
            scan = health_scan(s)

        assert scan["snapshot"]["open_conflicts"] == 1
        assert any(c["signal"] == "open_conflict" and c["conflict_event_id"] == "cf_1"
                   for c in scan["candidates"])

    def test_batch_cap(self, reflect_env):
        """候选超上限截断到 REFLECT_MAX_BATCH。"""
        session_factory, _ = reflect_env
        rows = []
        for i in range(25):
            old_id = f"mem_old_{i}"
            new_id_ = f"mem_new_{i}"
            rows.append(_mem(id=old_id, content=f"旧内容 {i}"))
            rows.append(_mem(id=new_id_, content=f"新内容 {i}"))
            rows.append(MemoryEdge(id=f"edge_{i}", source_memory_id=new_id_,
                                   target_memory_id=old_id,
                                   relation="supersedes", confidence=0.9))
        _seed(session_factory, rows)

        from lantai.core.settings import settings
        from lantai.evolution.reflector import health_scan
        with session_factory() as s:
            scan = health_scan(s)

        assert scan["snapshot"]["superseded_active"] == 25
        assert scan["snapshot"]["batch_total"] == settings.REFLECT_MAX_BATCH


# ── propose_from_reflection ──────────────────────────────────

class TestProposeFromReflection:

    def test_valid_add(self, reflect_env):
        """合法 add 提案落库（证据可选，类型/证据正确）。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [_mem(id="mem_src", content="来源记忆")])
        candidates = [{"memory_id": "mem_src", "key": "mem_src",
                       "content": "来源记忆", "lane": "general",
                       "importance": 0.5, "signal": "new_theme"}]
        curated = {"proposals": [
            {"proposal_type": "add", "target_memory_id": "",
             "new_content": "蒸馏出的新规则", "memory_type": "semantic",
             "reason": "模式提炼", "confidence": 0.85,
             "evidence_ids": ["mem_src"]},
        ]}

        from lantai.evolution.reflector import propose_from_reflection
        with session_factory() as s:
            props = propose_from_reflection(s, candidates, curated)

        assert len(props) == 1
        assert props[0].proposal_type == "add"
        assert props[0].evidence_ids == ["mem_src"]
        assert props[0].status == "pending"
        assert props[0].candidate_id is None

    def test_bad_evidence_skipped(self, reflect_env):
        """update 提案证据不存在 → 作废不落库（宁 miss）。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [_mem(id="mem_t", content="旧内容")])
        candidates = [{"memory_id": "mem_t", "key": "mem_t", "content": "旧内容",
                       "lane": "general", "importance": 0.5, "signal": "superseded"}]
        curated = {"proposals": [
            {"proposal_type": "update", "target_memory_id": "mem_t",
             "new_content": "新内容", "memory_type": "semantic",
             "reason": "x", "confidence": 0.9, "evidence_ids": ["nope"]},
        ]}

        from lantai.evolution.reflector import propose_from_reflection
        with session_factory() as s:
            props = propose_from_reflection(s, candidates, curated)

        assert props == []

    def test_low_confidence_skipped(self, reflect_env):
        """置信低于 REFLECT_MIN_CONFIDENCE → 不落库。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [_mem(id="mem_s", content="来源")])
        candidates = [{"memory_id": "mem_s", "key": "mem_s", "content": "来源",
                       "lane": "general", "importance": 0.5, "signal": "new_theme"}]
        curated = {"proposals": [
            {"proposal_type": "add", "target_memory_id": "",
             "new_content": "x", "memory_type": "semantic",
             "reason": "x", "confidence": 0.3, "evidence_ids": ["mem_s"]},
        ]}

        from lantai.evolution.reflector import propose_from_reflection
        with session_factory() as s:
            props = propose_from_reflection(s, candidates, curated)

        assert props == []


# ── run_reflect_once ─────────────────────────────────────────

class TestRunReflectOnce:

    def _chat_side_effect(self, curate_json, reject_verdicts):
        calls = [curate_json] + reject_verdicts
        return calls

    def test_auto_apply_deprecate(self, reflect_env):
        """高置信 + risk=low → deprecate 自动应用：archived + valid_to + checkpoint。"""
        session_factory, _ = reflect_env
        old = _mem(id="mem_old", content="旧域名 example.com")
        new = _mem(id="mem_new", content="新域名 example.org")
        _seed(session_factory, [old, new,
                                MemoryEdge(id="edge_1", source_memory_id="mem_new",
                                           target_memory_id="mem_old",
                                           relation="supersedes", confidence=0.9)])

        curate = {"proposals": [
            {"proposal_type": "deprecate", "target_memory_id": "mem_old",
             "new_content": "", "memory_type": "semantic",
             "reason": "superseded by mem_new", "confidence": 0.9,
             "evidence_ids": ["mem_new"]},
        ]}
        reject = {"accept": True, "risk": "low", "reason": "supported"}

        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=[curate, reject]), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()

        assert result["ok"] is True
        assert result["auto_applied"] == 1
        assert result["pending"] == 0
        assert result["health_before"]["superseded_active"] == 1
        assert result["health_after"]["superseded_active"] == 0
        with session_factory() as s:
            old = s.get(MemoryItem, "mem_old")
            assert old.status == "archived"
            assert old.valid_to is not None
            ckpts = s.exec(select(MemoryCheckpoint)
                           .where(MemoryCheckpoint.memory_id == "mem_old")).all()
            assert len(ckpts) >= 1

    def test_pending_low_confidence(self, reflect_env):
        """低置信（≥min 但 <auto）→ pending，进 /proposals 待审。"""
        session_factory, _ = reflect_env
        # superseded 候选（R1 触发蒸馏）
        _seed(session_factory, [
            _mem(id="mem_s", content="旧内容 example.com"),
            _mem(id="mem_new", content="新内容 example.org"),
            MemoryEdge(id="edge_s", source_memory_id="mem_new",
                       target_memory_id="mem_s",
                       relation="supersedes", confidence=0.9),
        ])
        curate = {"proposals": [
            {"proposal_type": "add", "target_memory_id": "",
             "new_content": "低置信蒸馏", "memory_type": "semantic",
             "reason": "x", "confidence": 0.6, "evidence_ids": ["mem_s"]},
        ]}
        reject = {"accept": True, "risk": "low", "reason": "ok"}

        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=[curate, reject]), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()

        assert result["pending"] == 1
        assert result["auto_applied"] == 0
        with session_factory() as s:
            prop = s.exec(select(MemoryProposal)
                          .where(MemoryProposal.proposal_type == "add")).one()
            assert prop.status == "pending"

    def test_pending_medium_risk(self, reflect_env):
        """高置信但 risk=medium → 强制 pending。"""
        session_factory, _ = reflect_env
        # superseded 候选（R1 触发蒸馏）
        _seed(session_factory, [
            _mem(id="mem_s", content="旧内容 example.com"),
            _mem(id="mem_new", content="新内容 example.org"),
            MemoryEdge(id="edge_m", source_memory_id="mem_new",
                       target_memory_id="mem_s",
                       relation="supersedes", confidence=0.9),
        ])
        curate = {"proposals": [
            {"proposal_type": "add", "target_memory_id": "",
             "new_content": "有风险蒸馏", "memory_type": "semantic",
             "reason": "x", "confidence": 0.95, "evidence_ids": ["mem_s"]},
        ]}
        reject = {"accept": True, "risk": "medium", "reason": "需人工确认"}

        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=[curate, reject]), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()

        assert result["pending"] == 1
        assert result["auto_applied"] == 0

    def test_idle_no_llm(self, reflect_env):
        """空闲日（无健康候选 + 水位不足）→ 零 LLM 调用。"""
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json") as mock_chat:
            result = run_reflect_once()

        assert result["skipped"] == "idle"
        mock_chat.assert_not_called()


# ── 运行结果落库（观察期可审计）──────────────────────

class TestRunOutcome:
    """反思每次运行的结论落库：水位/跳过/产出/LLM 失败（不静默）。"""

    def test_idle_run_records_outcome(self, reflect_env):
        session_factory, _ = reflect_env
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json") as mock_chat:
            result = run_reflect_once()
        assert result["skipped"] == "idle"
        with session_factory() as s:
            runs = s.exec(select(ReflectRun)).all()
            assert len(runs) == 1
            r = runs[0]
            assert r.skipped == "idle"
            assert r.proposals_created == 0
            assert r.error == ""
            assert r.waterline == 0.0

    def test_full_run_records_outcome(self, reflect_env):
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_old", content="旧域名 example.com"),
            _mem(id="mem_new", content="新域名 example.org"),
            MemoryEdge(id="edge_1", source_memory_id="mem_new",
                       target_memory_id="mem_old",
                       relation="supersedes", confidence=0.9),
        ])
        curate = {"proposals": [
            {"proposal_type": "deprecate", "target_memory_id": "mem_old",
             "new_content": "", "memory_type": "semantic",
             "reason": "superseded by mem_new", "confidence": 0.9,
             "evidence_ids": ["mem_new"]},
        ]}
        reject = {"accept": True, "risk": "low", "reason": "supported"}
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=[curate, reject]), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()
        assert result["auto_applied"] == 1
        with session_factory() as s:
            runs = s.exec(select(ReflectRun)).all()
            assert len(runs) == 1
            r = runs[0]
            assert r.skipped == ""
            assert r.proposals_created == 1
            assert r.auto_applied == 1
            assert r.curate_failed is False
            assert r.health_before.get("superseded_active") == 1
            assert r.health_after.get("superseded_active") == 0

    def test_curate_failure_records_flag(self, reflect_env):
        """LLM 失败 → 宁 miss 空降级，但运行记录标记 curate_failed（不静默）。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_s", content="旧内容 example.com"),
            _mem(id="mem_new", content="新内容 example.org"),
            MemoryEdge(id="edge_s", source_memory_id="mem_new",
                       target_memory_id="mem_s",
                       relation="supersedes", confidence=0.9),
        ])
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=RuntimeError("llm down")), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()
        assert result["proposals_created"] == 0
        with session_factory() as s:
            r = s.exec(select(ReflectRun)).one()
            assert r.curate_failed is True
            assert r.error == ""

    def test_rejecter_failure_records_flag(self, reflect_env):
        """裁决 LLM 失败 → 提案宁 miss 丢弃，但运行记录标记 rejecter_failed（不静默）。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_old", content="旧域名 example.com"),
            _mem(id="mem_new", content="新域名 example.org"),
            MemoryEdge(id="edge_1", source_memory_id="mem_new",
                       target_memory_id="mem_old",
                       relation="supersedes", confidence=0.9),
        ])
        curate = {"proposals": [
            {"proposal_type": "deprecate", "target_memory_id": "mem_old",
             "new_content": "", "memory_type": "semantic",
             "reason": "superseded by mem_new", "confidence": 0.9,
             "evidence_ids": ["mem_new"]},
        ]}
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.chat_json",
                   side_effect=[curate, RuntimeError("rejecter down")]), \
             _embed_mock(), _vector_mocks():
            result = run_reflect_once()
        assert result["discarded"] == 1
        assert result["rejecter_failed"] == 1
        with session_factory() as s:
            r = s.exec(select(ReflectRun)).one()
            assert r.proposals_created == 1
            assert r.discarded == 1
            assert r.curate_failed is False
            assert r.rejecter_failed == 1

    def test_exception_records_waterline_and_error(self, reflect_env):
        """未捕获异常 → 尽力补水位 + error 留痕后原样抛出（宁 miss 不静默）。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_a", content="重要记忆", importance=0.8,
                 created_at=utcnow()),
        ])
        from lantai.evolution.reflector import run_reflect_once
        with patch("lantai.evolution.reflector.health_scan",
                   side_effect=RuntimeError("scan boom")):
            with pytest.raises(RuntimeError):
                run_reflect_once()
        with session_factory() as s:
            r = s.exec(select(ReflectRun)).one()
            assert r.error != ""
            assert r.waterline == 0.8


# ── apply_proposal 扩展（deprecate / merge / 回归）────────────

class TestApplyProposalExtensions:

    def _apply(self, prop):
        with _embed_mock(), _vector_mocks():
            from lantai.evolution.promoter import apply_proposal
            return apply_proposal(prop.id)

    def test_deprecate_branch(self, reflect_env):
        """deprecate：archived + valid_to + supersedes 边 + checkpoint + FTS 删除。"""
        session_factory, engine = reflect_env
        _seed(session_factory, [
            _mem(id="mem_old", key="域名", content="example.com"),
            _mem(id="mem_new", key="新域名", content="example.org"),
        ])
        with session_factory() as s:
            sync_fts(s, "mem_old", "example.com")
            s.add(MemoryProposal(
                id="prop_dep", proposal_type="deprecate",
                target_memory_id="mem_old", evidence_ids=["mem_new"],
                reason="superseded", confidence=0.9, status="pending",
                proposed_patch={"memory_type": "semantic", "key": "域名",
                                "content": "", "lane": "general"},
            ))
            s.commit()
        from lantai.evolution.promoter import apply_proposal
        with _embed_mock(), _vector_mocks():
            result = apply_proposal("prop_dep")

        assert result["ok"] is True
        with session_factory() as s:
            old = s.get(MemoryItem, "mem_old")
            assert old.status == "archived"
            assert old.valid_to is not None
            edge = s.exec(select(MemoryEdge)
                          .where(MemoryEdge.relation == "supersedes",
                                 MemoryEdge.target_memory_id == "mem_old")).one()
            assert edge.source_memory_id == "mem_new"
            assert len(s.exec(select(MemoryCheckpoint)
                              .where(MemoryCheckpoint.memory_id == "mem_old")).all()) >= 1
        with engine.connect() as conn:
            hits = search_fts(conn.connection.driver_connection, "example.com")
            assert "mem_old" not in hits

    def test_merge_branch(self, reflect_env):
        """merge：主记忆更新 + 被合并记忆 archived + supersedes 边 + 双 checkpoint。"""
        session_factory, _ = reflect_env
        _seed(session_factory, [
            _mem(id="mem_a", key="端口 A", content="服务A 端口 8080"),
            _mem(id="mem_b", key="端口 B", content="服务A 端口 9090"),
        ])
        with session_factory() as s:
            s.add(MemoryProposal(
                id="prop_merge", proposal_type="merge",
                target_memory_id="mem_a", evidence_ids=["mem_a", "mem_b"],
                reason="重复配置", confidence=0.9, status="pending",
                proposed_patch={"memory_type": "semantic", "key": "端口 A",
                                "content": "服务A 端口 8080（备用 9090）",
                                "lane": "general"},
            ))
            s.commit()

        from lantai.evolution.promoter import apply_proposal
        with _embed_mock(), _vector_mocks():
            result = apply_proposal("prop_merge")

        assert result["ok"] is True
        with session_factory() as s:
            a = s.get(MemoryItem, "mem_a")
            b = s.get(MemoryItem, "mem_b")
            assert a.status == "active"
            assert "备用 9090" in a.content
            assert a.version == 2
            assert b.status == "archived"
            assert s.exec(select(MemoryEdge)
                          .where(MemoryEdge.relation == "supersedes",
                                 MemoryEdge.source_memory_id == "mem_a",
                                 MemoryEdge.target_memory_id == "mem_b")).one()
            assert len(s.exec(select(MemoryCheckpoint)
                              .where(MemoryCheckpoint.memory_id == "mem_a")).all()) >= 1
            assert len(s.exec(select(MemoryCheckpoint)
                              .where(MemoryCheckpoint.memory_id == "mem_b")).all()) >= 1

    def test_add_update_regression(self, reflect_env):
        """门面回归：add/update 语义与现状一致。"""
        session_factory, _ = reflect_env
        with session_factory() as s:
            s.add(MemoryProposal(
                id="prop_add", proposal_type="add", evidence_ids=["doc_1"],
                reason="x", confidence=0.9, status="pending",
                proposed_patch={"memory_type": "semantic", "key": "新记忆",
                                "content": "全新内容", "lane": "general"},
            ))
            s.commit()

        from lantai.evolution.promoter import apply_proposal
        with _embed_mock(), _vector_mocks():
            assert apply_proposal("prop_add")["ok"] is True

        with session_factory() as s:
            mem = s.exec(select(MemoryItem).where(MemoryItem.key == "新记忆")).one()
            mem_id = mem.id
            assert mem.content == "全新内容"
            assert mem.status == "active"
            # update：target 命中既有记忆 → 内容更新 + 版本递增
            s.add(MemoryProposal(
                id="prop_upd", proposal_type="update",
                target_memory_id=mem.id, evidence_ids=["doc_2"],
                reason="x", confidence=0.9, status="pending",
                proposed_patch={"memory_type": "semantic", "key": "新记忆",
                                "content": "更新后的内容", "lane": "general"},
            ))
            s.commit()

        from lantai.evolution.promoter import apply_proposal as _ap
        with _embed_mock(), _vector_mocks():
            assert _ap("prop_upd")["ok"] is True

        with session_factory() as s:
            mem = s.get(MemoryItem, mem_id)
            assert mem.content == "更新后的内容"
            assert mem.version == 2



