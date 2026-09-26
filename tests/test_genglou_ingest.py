"""更漏写入侧（票 08）冒烟测试：显式时间提取、链路透传、I1 校验、U3 不猜。

不 mock：proposer→promoter→MemoryItem 走真实内存库（embed 用确定性替身，
外部网络替身面）；提取纯函数直调；correct 走真实 record_ops。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.evolution.promoter as promoter_mod
import lantai.storage.db as db_module
import lantai.storage.vector_store as vs_module
from lantai.storage.fts import init_fts


def _hash_embed(texts):
    out = []
    for text in texts:
        v = [0.0] * 512
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))] or [text]
        for g in grams:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % 512] += 1.0
        n = sum(v) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture()
def ingest_env(tmp_path, monkeypatch):
    import lantai.eval.models  # noqa: F401
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(promoter_mod, "embed", _hash_embed)

    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma-ingest"))
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(vs_module, "_store", None, raising=False)

    yield engine

    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    engine.dispose()


class TestExplicitTimeExtraction:
    def test_iso_and_cn_formats(self):
        from datetime import datetime

        from lantai.core.time_precision import extract_explicit_event_time

        dt, prec = extract_explicit_event_time("系统 2026-09-15 完成迁移")
        assert prec == "day" and dt == datetime(2026, 9, 15)
        dt, prec = extract_explicit_event_time("2026年9月15日迁的库")
        assert prec == "day" and dt == datetime(2026, 9, 15)
        dt, prec = extract_explicit_event_time("2024 年起用 Postgres".replace(" ", ""))
        assert prec == "year" and dt == datetime(2024, 1, 1)

    def test_no_guess_on_relative_or_empty(self):
        """U3 不猜面：相对时间/无时间 → (None, "")，绝不回填。"""
        from lantai.core.time_precision import extract_explicit_event_time

        for text in ("昨天迁移了数据库", "下周一上线", "用户喜欢 VSCode", ""):
            assert extract_explicit_event_time(text) == (None, "")

    def test_i1_validator(self):
        from lantai.core.time_precision import validate_event_time_pair

        assert validate_event_time_pair(None, "") is True
        assert validate_event_time_pair("2026-01-01", "day") is True
        assert validate_event_time_pair(None, "day") is False  # 违例：空时间带精度
        assert validate_event_time_pair("2026-01-01", "") is False  # 违例：时间无精度
        assert validate_event_time_pair("2026-01-01", "bogus") is False


class TestEventTimeChain:
    def test_candidate_to_memory_passthrough(self, ingest_env):
        """链路透传：候选 provenance.event_time → proposal patch → MemoryItem 落列。"""
        from datetime import datetime

        from lantai.evolution.promoter import apply_proposal
        from lantai.evolution.proposer import propose_from_candidate
        from lantai.models.tables import MemoryCandidate, MemoryItem

        with db_module.get_session() as s:
            cand = MemoryCandidate(
                id="evs-et-cand",
                document_id="evs-et-doc",
                summary="系统 2026-09-15 完成迁移",
                claims=["系统 2026-09-15 完成迁移"],
                status="pending_review",
                extractor_confidence=0.9,
                provenance={
                    "prompt": "staged-eval-stub",
                    "event_time": "2026-09-15T00:00:00",
                    "event_time_precision": "day",
                },
            )
            s.add(cand)
            s.commit()

        gate_res = {"decision": "working_only", "novelty": 0.9, "conflicts": []}
        prop = propose_from_candidate("evs-et-cand", gate_res)
        assert prop.proposed_patch.get("event_time") == "2026-09-15T00:00:00"
        applied = apply_proposal(prop.id)
        assert applied["ok"] is True

        with db_module.get_session() as s:
            mem = s.get(
                MemoryItem,
                s.exec(
                    __import__("sqlmodel")
                    .select(MemoryItem)
                    .where(
                        MemoryItem.key == "系统 2026-09-15 完成迁移", MemoryItem.status == "active"
                    )
                )
                .first()
                .id,
            )
            assert mem.event_time is not None
            assert mem.event_time.year == 2026 and mem.event_time.month == 9
            assert mem.event_time_precision == "day"

    def test_i1_violation_rejected_not_silently_fixed(self, ingest_env):
        """I1 违例（空时间带精度）→ apply 拒写留痕，不静默修正。"""
        from lantai.evolution.promoter import apply_proposal
        from lantai.evolution.proposer import propose_from_candidate
        from lantai.models.tables import MemoryCandidate

        with db_module.get_session() as s:
            cand = MemoryCandidate(
                id="evs-bad-cand",
                document_id="evs-bad-doc",
                summary="无时间的普通事实条目内容",
                status="pending_review",
                extractor_confidence=0.9,
                provenance={"prompt": "staged-eval-stub"},
            )
            s.add(cand)
            s.commit()

        gate_res = {"decision": "working_only", "novelty": 0.9, "conflicts": []}
        prop = propose_from_candidate("evs-bad-cand", gate_res)
        # 人为注入 I1 违例 patch（模拟上游脏数据）——在会话内改（detached 实例不落库）
        from lantai.models.tables import MemoryProposal

        with db_module.get_session() as s:
            prop_db = s.get(MemoryProposal, prop.id)
            prop_db.proposed_patch = {
                **prop_db.proposed_patch,
                "event_time": None,
                "event_time_precision": "day",
            }
            s.commit()

        applied = apply_proposal(prop.id)
        assert applied["ok"] is False
        assert "I1" in applied.get("reason", "")

    def test_dialogue_provenance_u3_annotation(self, monkeypatch):
        """U3：对话摄取显式时间 → provenance 落值；无时间 → failed 标注且不猜。"""
        from unittest.mock import patch as _patch

        import lantai.ingestion.dialogue as dlg
        from lantai.models.tables import MemoryCandidate

        # extract_candidate（外部 LLM 提取器替身面）：固定高置信提取结果
        def fake_extract(q, t):
            return {
                "summary": t[:400],
                "claims": [t],
                "extractor_confidence": 0.9,
            }

        with _patch("lantai.ingestion.dialogue.extract_candidate", fake_extract):
            res = dlg.ingest_dialogue("系统 2026-09-15 完成迁移", session_id="sess-u3")
        assert res.get("candidate_id"), res

        with db_module.get_session() as s:
            cand = s.get(MemoryCandidate, res["candidate_id"])
            prov = cand.provenance or {}
        assert prov.get("event_time") == "2026-09-15T00:00:00"
        assert prov.get("event_time_precision") == "day"

        # 无时间文本：failed 标注，不猜
        with _patch("lantai.ingestion.dialogue.extract_candidate", fake_extract):
            res2 = dlg.ingest_dialogue("用户喜欢 VSCode 编辑器", session_id="sess-u3")
        with db_module.get_session() as s:
            cand2 = s.get(MemoryCandidate, res2["candidate_id"])
            prov2 = cand2.provenance or {}
        assert prov2.get("event_time_extract_failed") is True
        assert "event_time" not in prov2


class TestCorrectEventTime:
    def test_correct_updates_event_time_with_provenance(self, ingest_env):
        """纠错可更正 event_time：I1 校验 + 旧值进 corrections 留痕。"""
        from lantai.core.ids import new_id
        from lantai.models.tables import MemoryItem
        from lantai.services.record_ops_service import correct_memory

        with db_module.get_session() as s:
            mem = MemoryItem(
                id=new_id("evs-mem"),
                content="迁移完成于 2026-09-15",
                key="迁移完成于 2026-09-15",
                status="active",
                event_time=None,
                event_time_precision="",
            )
            s.add(mem)
            s.commit()
            mid = mem.id

        res = correct_memory(
            mid,
            new_content="迁移完成于 2026-09-16",
            reason="日期笔误",
            actor="tester",
            event_time="2026-09-16",
            event_time_precision="day",
        )
        assert res["ok"] is True

        with db_module.get_session() as s:
            mem = s.get(MemoryItem, mid)
            assert mem.event_time is not None and mem.event_time.day == 16
            assert mem.event_time_precision == "day"
            corr = (mem.provenance or {}).get("corrections", [])
            assert corr and corr[-1]["old_event_time"] is None  # 旧值留痕

    def test_correct_i1_violation_rejected(self, ingest_env):
        from lantai.core.ids import new_id
        from lantai.models.tables import MemoryItem
        from lantai.services.record_ops_service import correct_memory

        with db_module.get_session() as s:
            mem = MemoryItem(id=new_id("evs-mem"), content="内容甲", key="内容甲", status="active")
            s.add(mem)
            s.commit()
            mid = mem.id

        res = correct_memory(mid, new_content="内容乙", event_time=None, event_time_precision="day")
        assert res["ok"] is False and "I1" in res["error"]
