"""票 02（v0.68 性能基线顺带炸出）：fastpath 直写路径缺 `content_hash` 在场检查。

同一内容经 `add_memory` 白名单直写第二次 →
`UNIQUE constraint failed: rawdocument.content_hash` → API 500。

根因：提取路径 `_create_candidate_with_extraction` 建 RawDocument 前先按
`content_hash` 查在场（在场复用文档、照建候选）；直写路径
`_create_candidate_direct` 没有这一步，直接 INSERT。前置的 `_apply_dedup`
只搜 **MemoryItem**（向量库），搜不到还停在候选/文档阶段的相同内容——
去重链挡不住这个重复。生产可达性：同一句自我声明在两次 coalesce 冲刷里
各出现一次，且第一次尚未演化成 MemoryItem，第二次冲刷即触发。

纪律：不 mock 被测函数内部计算——`add_memory` 端到端直调，向量库用真实
临时 ChromaDB（外部存储面），embed 用 conftest 统一确定性替身（外部网络面）。
"""

import hashlib

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from lantai.models.schemas import AddMemoryReq
from lantai.models.tables import MemoryCandidate, RawDocument
from lantai.services.memory_service import add_memory
from lantai.storage import db

FASTPATH_SENTENCE = "我叫老周00042，是后端工程师，负责检索链路"
EXTRACT_SENTENCE = "基线摄取样本00042：alpha项目检索的季度复盘纪要与后续动作清单"


@pytest.fixture()
def fp_env(tmp_path, monkeypatch):
    """内存 SQLite（真实建表）+ 真实临时 ChromaDB；coalesce 显式关（验收口径）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db, "engine", engine)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("lantai.core.settings.settings.COALESCE_ENABLED", False)

    chroma_dir = str(tmp_path / "chroma_dup")
    monkeypatch.setattr("lantai.core.settings.settings.CHROMADB_PATH", chroma_dir)
    import lantai.storage.vector_store as vs_mod

    monkeypatch.setattr(vs_mod, "_store", None)
    from lantai.storage.vector_store import get_vector_store

    fresh_vs = get_vector_store()
    monkeypatch.setattr("lantai.services.memory_service.get_vector_store", lambda: fresh_vs)

    return engine


def _add(content: str, title: str, lane: str = "profile") -> dict:
    return add_memory(
        AddMemoryReq(title=title, content=content, lane=lane),
        user_id="default",
    )


def _doc_count(engine, content: str) -> int:
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with Session(engine) as s:
        return len(list(s.exec(select(RawDocument).where(RawDocument.content_hash == h)).all()))


def _candidates_of(engine, document_id: str) -> list[MemoryCandidate]:
    with Session(engine) as s:
        return list(
            s.exec(select(MemoryCandidate).where(MemoryCandidate.document_id == document_id)).all()
        )


class TestFastpathDuplicateContent:
    def test_same_content_twice_no_integrity_error(self, fp_env):
        """复现口径（票 02 验收 1）：同内容连续两次 add_memory（fastpath 命中、
        coalesce 关）不抛 IntegrityError。

        向量库为空 → `_apply_dedup` 必然 insert（搜不到候选/文档阶段的重复），
        第二次此前会直插撞唯一约束。同时断言两次都真走了 fastpath——
        若句型意外脱靶落进提取路径，本测试会因提取路径本来就有在场检查而
        假绿，`fastpath=True` 把这个空转堵死。
        """
        from lantai.parsing.fastpath import fastpath_check

        assert fastpath_check(FASTPATH_SENTENCE), "测试句子脱靶——先修句子再谈回归"

        out1 = _add(FASTPATH_SENTENCE, "自我声明 00042")
        out2 = _add(FASTPATH_SENTENCE, "自我声明 00042")
        assert out1.get("fastpath") is True and out2.get("fastpath") is True, (
            f"两次调用未都走 fastpath 直写：{out1} / {out2}"
        )

        # 与提取路径同口径：文档复用一条，候选照建
        assert out2["document_id"] == out1["document_id"], (
            f"在场文档未复用：{out1['document_id']} → {out2['document_id']}"
        )
        assert out2["candidate_id"] != out1["candidate_id"], "第二次未建候选（口径漂移）"
        assert _doc_count(fp_env, FASTPATH_SENTENCE) == 1, "同 content_hash 落了多条 RawDocument"
        cands = _candidates_of(fp_env, out1["document_id"])
        assert len(cands) == 2, f"应有两条候选（每写一次一条）：{len(cands)}"

    def test_extraction_path_parity_reference(self, fp_env):
        """提取路径参照组（票 02 验收 1 的「行为与提取路径一致」）：

        非 fastpath 内容同写两次——它本来就该绿（在场检查是提取路径既有
        语义），本测试钉住这个参照行为，防止将来把直写路径「修」得和
        参照组不一致还自以为对齐了。
        """
        from lantai.parsing.fastpath import fastpath_check

        assert not fastpath_check(EXTRACT_SENTENCE), "对照句意外命中 fastpath——对照组失效"

        out1 = _add(EXTRACT_SENTENCE, "基线摄取样本 00042", lane="fact")
        out2 = _add(EXTRACT_SENTENCE, "基线摄取样本 00042", lane="fact")
        assert "fastpath" not in out1 and "fastpath" not in out2, (
            f"对照组误入直写路径：{out1} / {out2}"
        )

        assert out2["document_id"] == out1["document_id"]
        assert out2["candidate_id"] != out1["candidate_id"]
        assert _doc_count(fp_env, EXTRACT_SENTENCE) == 1
        assert len(_candidates_of(fp_env, out1["document_id"])) == 2
