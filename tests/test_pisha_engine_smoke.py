"""测试披沙（PishaEngine）提纯去噪核心引擎（Ticket 01 冒烟测试）。

遵循 AGENTS.md 测试纪律：真实构造最小输入直调被测核心函数，在真实 SQLite 上写库验证，不 mock 核心计算。
覆盖 4 个独立接缝：
- 接缝 1：纯函数指代提纯、启发式去噪与确定性规则降级
- 接缝 2：真实 DB 单条候选提纯落库与元数据变迁
- 接缝 3：批量模糊带扫描提纯
- 接缝 4：行级 ACL 权限隔离与樊篱（wrap_as_data）防注入包裹
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.pisha import PishaEngine, PishaResult
from lantai.core.auth import Principal
from lantai.models.tables import MemoryCandidate


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class TestPishaEngineSmoke:
    def test_seam1_refine_text_heuristic_and_rules(self):
        """接缝 1：纯函数指代提纯、启发式口语去噪与确定性规则降级。"""
        engine = PishaEngine()
        raw_text = "坦白讲，他推荐了那个工具，挺好用的呢！"
        entity_hints = {"他": "李架构师", "那个工具": "Docker"}

        # 不 mock LLM 时，由于无外部网络/配置，代码自动走优雅确定性规则降级
        res: PishaResult = engine.refine_text(
            raw_text,
            context="项目部署选型讨论",
            entity_hints=entity_hints,
            initial_confidence=0.35,
        )

        assert isinstance(res, PishaResult)
        assert res.is_valid is True
        assert "李架构师" in res.refined_text
        assert "Docker" in res.refined_text
        assert "坦白讲" not in res.refined_text
        assert "colloquial_pruned" in res.denoise_tags
        assert res.pronoun_resolutions == {"他": "李架构师", "那个工具": "Docker"}
        assert "<memory_data" in res.fenced_content

    def test_seam2_refine_candidate_db_record(self, test_db: Session):
        """接缝 2：真实 DB 单条候选提纯落库与元数据变迁。"""
        cand = MemoryCandidate(
            id="cand_pisha_01",
            document_id="doc_pisha_01",
            content="其实关于这个事以后要注意！",
            summary="其实关于这个事以后要注意！",
            claims=["其实关于这个事以后要注意！"],
            extractor_confidence=0.3,
            status="pending_review",
            lane="general",
            user_id="alice",
            tenant_id="tenant_01",
        )
        test_db.add(cand)
        test_db.commit()

        engine = PishaEngine(session=test_db)
        out = engine.refine_candidate("cand_pisha_01")

        assert out["candidate_id"] == "cand_pisha_01"
        assert out["is_valid"] is True

        updated_cand = test_db.get(MemoryCandidate, "cand_pisha_01")
        assert updated_cand is not None
        assert updated_cand.provenance.get("pisha_refined") is True
        assert "其实" not in updated_cand.summary
        assert "colloquial_pruned" in updated_cand.provenance.get("denoise_tags", [])

    def test_seam3_batch_refine_fuzzy_band(self, test_db: Session):
        """接缝 3：批量模糊带扫描提纯。"""
        cands = [
            MemoryCandidate(
                id="cand_band_01",
                document_id="doc_pisha_band_01",
                summary="好的，总的来说这事挺靠谱的",
                extractor_confidence=0.25,
                status="pending_review",
                tenant_id="tenant_01",
            ),
            MemoryCandidate(
                id="cand_band_02",
                document_id="doc_pisha_band_02",
                summary="嗯，他说那个模块还要再测测呢",
                extractor_confidence=0.45,
                status="pending_review",
                tenant_id="tenant_01",
            ),
            MemoryCandidate(
                id="cand_band_high",
                document_id="doc_pisha_band_03",
                summary="置信度很高确凿事实",
                extractor_confidence=0.9,
                status="pending_review",
                tenant_id="tenant_01",
            ),
        ]
        test_db.add_all(cands)
        test_db.commit()

        engine = PishaEngine(session=test_db)
        batch_res = engine.batch_refine_candidates(min_conf=0.2, max_conf=0.6, limit=10)

        # 仅模糊带 [0.2, 0.6] 的两条应被处理
        assert len(batch_res) == 2
        cand_ids = [r["candidate_id"] for r in batch_res]
        assert "cand_band_01" in cand_ids
        assert "cand_band_02" in cand_ids
        assert "cand_band_high" not in cand_ids

    def test_seam4_tenant_acl_and_fencing_isolation(self, test_db: Session):
        """接缝 4：行级 ACL 权限隔离与樊篱（wrap_as_data）防注入包裹。"""
        cand = MemoryCandidate(
            id="cand_secret_01",
            document_id="doc_pisha_secret_01",
            summary="机密业务线核心部署要点",
            extractor_confidence=0.4,
            status="pending_review",
            user_id="bob",
            tenant_id="tenant_bob",
        )
        test_db.add(cand)
        test_db.commit()

        # Alice 尝试跨租户提纯 Bob 的候选
        alice_principal = Principal(user_id="alice", tenant_id="tenant_alice", role="user")
        engine_alice = PishaEngine(session=test_db, principal=alice_principal)

        with pytest.raises(PermissionError) as exc_info:
            engine_alice.refine_candidate("cand_secret_01")
        assert "行级租户/属主隔离" in str(exc_info.value)

        # Bob 本人提纯，正常通过且带有樊篱包裹
        bob_principal = Principal(user_id="bob", tenant_id="tenant_bob", role="user")
        engine_bob = PishaEngine(session=test_db, principal=bob_principal)
        out = engine_bob.refine_candidate("cand_secret_01")
        assert out["candidate_id"] == "cand_secret_01"
        assert "<memory_data" in out["pisha_result"]["fenced_content"]
