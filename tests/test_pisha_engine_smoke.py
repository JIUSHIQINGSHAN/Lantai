"""测试披沙（PishaEngine）提纯去噪核心引擎（Ticket 01 冒烟测试）。

遵循 AGENTS.md 测试纪律：真实构造最小输入直调被测核心函数，在真实 SQLite 上写库验证，不 mock 核心计算。
覆盖 4 个独立接缝：
- 接缝 1：纯函数指代提纯、启发式去噪与确定性规则降级（引擎 refine_text 纯函数）
- 接缝 2：真实 DB 单条候选提纯落库与元数据变迁（service 层——票 58 死代码处置后
  DB 写面单一真源在 services.refine_service，引擎版副本已删）
- 接缝 3：批量模糊带扫描提纯（service 层）
- 接缝 4：行级 ACL 权限隔离与樊篱（wrap_as_data）防注入包裹
  （ACL 判据单一真源 acl.ensure_can_delete——引擎版独立 _check_access 已删）
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.pisha import PishaEngine, PishaResult
from lantai.core.auth import Principal
from lantai.models.tables import MemoryCandidate
from lantai.services.refine_service import batch_refine_candidates, refine_candidate_record


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
        """接缝 2：真实 DB 单条候选提纯落库与元数据变迁（service 层真源）。"""
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

        out = refine_candidate_record("cand_pisha_01", session=test_db)

        assert out["id"] == "cand_pisha_01"

        updated_cand = test_db.get(MemoryCandidate, "cand_pisha_01")
        assert updated_cand is not None
        assert updated_cand.provenance.get("pisha_refined") is True
        assert "其实" not in updated_cand.summary
        assert "colloquial_pruned" in updated_cand.provenance.get("denoise_tags", [])
        # 票 58：改写前原件备份（可回滚位）
        assert updated_cand.provenance.get("pisha_original") == {
            "summary": "其实关于这个事以后要注意！",
            "claims": ["其实关于这个事以后要注意！"],
        }

    def test_seam3_batch_refine_fuzzy_band(self, test_db: Session):
        """接缝 3：批量模糊带扫描提纯（service 层真源）。"""
        cands = [
            MemoryCandidate(
                id="cand_band_01",
                document_id="doc_pisha_band_01",
                summary="好的，总的来说这事挺靠谱的",
                extractor_confidence=0.25,
                status="pending_review",
                tenant_id="tenant_01",
                # user_id 必填（同 test_refine 口径）：service 批量按 _owner_scope
                # 收窄，principal=None 时 NULL 属主种子一条都扫不到
                user_id="default",
            ),
            MemoryCandidate(
                id="cand_band_02",
                document_id="doc_pisha_band_02",
                summary="嗯，他说那个模块还要再测测呢",
                extractor_confidence=0.45,
                status="pending_review",
                tenant_id="tenant_01",
                user_id="default",
            ),
            MemoryCandidate(
                id="cand_band_high",
                document_id="doc_pisha_band_03",
                summary="置信度很高确凿事实",
                extractor_confidence=0.9,
                status="pending_review",
                tenant_id="tenant_01",
                user_id="default",
            ),
        ]
        test_db.add_all(cands)
        test_db.commit()

        batch_out = batch_refine_candidates(min_conf=0.2, max_conf=0.6, session=test_db)

        # 仅模糊带 [0.2, 0.6] 的两条应被处理（service 返回汇总 dict 而非逐条列表）
        assert batch_out["total_scanned"] == 2
        assert batch_out["refined"] == 2
        assert batch_out["rejected"] == 0
        high = test_db.get(MemoryCandidate, "cand_band_high")
        assert high.provenance.get("pisha_refined") is None, "带外候选不得入批"

    def test_seam4_tenant_acl_and_fencing_isolation(self, test_db: Session):
        """接缝 4：行级 ACL 权限隔离（ensure_can_delete 单一真源）与樊篱包裹。"""
        # 文本带口语噪声（嗯/呢）：LLM 不可用时确定性降级仍有 denoise_tags，
        # 不触发「保持原样待重试」早退，bob 臂才能真正走进落库写面
        raw = "嗯，其实机密业务线的部署要点要记住呢"
        cand = MemoryCandidate(
            id="cand_secret_01",
            document_id="doc_pisha_secret_01",
            summary=raw,
            extractor_confidence=0.4,
            status="pending_review",
            user_id="bob",
            tenant_id="tenant_bob",
        )
        test_db.add(cand)
        test_db.commit()

        # Alice 尝试跨租户提纯 Bob 的候选 → 403（HTTPException，ensure_can_delete 口径）
        alice_principal = Principal(user_id="alice", tenant_id="tenant_alice", role="user")

        with pytest.raises(HTTPException) as exc_info:
            refine_candidate_record("cand_secret_01", session=test_db, principal=alice_principal)
        assert exc_info.value.status_code == 403
        assert "another tenant" in exc_info.value.detail

        # Bob 本人提纯，正常通过 ACL 并落库（LLM 不可用 → 确定性规则保底：
        # conf 保持 0.4，口语噪声剪除，改写前原件备份在 provenance.pisha_original）
        bob_principal = Principal(user_id="bob", tenant_id="tenant_bob", role="user")
        out = refine_candidate_record("cand_secret_01", session=test_db, principal=bob_principal)
        assert out["id"] == "cand_secret_01"
        assert out["provenance"].get("pisha_refined") is True
        assert "colloquial_pruned" in out["provenance"].get("denoise_tags", [])
        assert out["provenance"]["pisha_original"]["summary"] == raw
        assert out["summary"] != raw, "降噪后不得与原句全等"
