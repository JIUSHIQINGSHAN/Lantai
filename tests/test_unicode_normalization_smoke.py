"""票 45：归一是「检测闸私有品」——索引/查询/去重三面全程零归一。

`.scratch/v0.68-next-window-2026-11/issues/45-unicode-normalization-seam-absent.md`

**先说影响**：全仓 `unicodedata` 只有检测闸两处瞬态 NFKC（獬豸「只检测
不改写」），落库、FTS trigram 索引、查询、案牍去重键、反思聚类签名、检索
指纹六面裸奔——全角↔半角与 NFC↔NFD 是**双向确定性零命中**（不是概率性
降级）；正文里一个 ZWSP（网页粘贴常态）把「机器学习」的 trigram 撕开，
查询 gram 零命中；RTL override 控制符透传宿主终端可致视觉伪装。

**mock 边界声明**（AGENTS.md 纪律）：本文件**零 mock**——真 SQLite 内存库
+ 真 FTS5 trigram（init_fts 核验过），真 sync_fts / search_fts /
search_fts_bm25 / candidate_group_ids / list_work_items / detect_patterns /
_norm_hash 直调；案牍列表臂把 `db.get_session` 指向内存库属接线替身
（test_fts_integration 先例），且用**测试体内** `MonkeyPatch.context`
（conftest 模块卫生绊线：还原先于一切 fixture teardown）。
源码里的不可见/全角字符一律用 \\uXXXX 转义写死——隐形字面量不可审，
也易在格式化中走形。
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem

# 票 45 主危害样例：ZWSP 撕裂 + 全角字母（网页剪藏 / IME 全角态常态输入）
_ZWSP = "\u200b"
_FULLWIDTH_CONTENT = f"机器{_ZWSP}学习是ＡＩ的核心"


def _seam():
    """被测归一缝本体（红态缺失即 AttributeError —— 病灶本身）。"""
    from lantai.core.text import normalize_for_index

    return normalize_for_index


class TestNormalizeForIndex:
    """归一缝本体：NFKC 折宽度 + 剥零宽/bidi 控制符 + NFC。"""

    def test_folds_width_strips_invisible_composes(self):
        normalize = _seam()
        # 零宽剥除 + 全角折半角（一次归一双病同治）
        assert normalize(_FULLWIDTH_CONTENT) == "机器学习是AI的核心"
        # 全角整词折半角
        assert normalize("ｄｅｃｉｓｉｏｎ") == "decision"
        # NFC 组合：NFD 形态（e + U+0301）收敛到组合形 U+00E9
        assert normalize("cafe\u0301") == "café"
        # bidi override / ZWSP / BOM 剥除
        assert normalize("a\u202eb\u200bc\ufeffd") == "abcd"
        # 常规文本逐字透传（归一不是改写）
        assert normalize("hello 世界 123") == "hello 世界 123"

    def test_idempotent_and_total(self):
        normalize = _seam()
        once = normalize(_FULLWIDTH_CONTENT)
        assert normalize(once) == once, "归一必须幂等（二过缝不变）"
        assert normalize(None) == ""
        assert normalize("") == ""


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    from lantai.storage.fts import init_fts

    init_fts(e.raw_connection())
    return e


class TestFtsNormalizationRecall:
    """病灶 1+2：真 FTS5 trigram 库——全角/ZWSP/NFD 内容与查询的确定性 miss。"""

    def _add_mem_with_fts(self, engine, content: str) -> str:
        from lantai.storage.fts import sync_fts

        mid = new_id("mem")
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="general",
                    key=mid,
                    content=content,
                    lane="general",
                    status="active",
                    importance=0.5,
                    use_count=0,
                    decay_score=1.0,
                    last_used_at=utcnow(),
                    created_at=utcnow(),
                )
            )
            # 生产形状：sync_fts 与记忆写入同事务（tanyi.py:225 同款）
            sync_fts(s, mid, content)
            s.commit()
        return mid

    def test_fullwidth_zwsp_content_hit_by_normalized_query(self, engine):
        """TDD 1：全角ＡＩ + ZWSP 撕裂正文，查「机器学习 AI」必须命中。"""
        from lantai.storage.fts import search_fts, search_fts_bm25

        mid = self._add_mem_with_fts(engine, _FULLWIDTH_CONTENT)
        with engine.connect() as conn:
            c = conn.connection.driver_connection
            rows = search_fts(c, "机器学习 AI", top_k=5)
            assert mid in rows, f"归一缝缺失：ZWSP/全角正文对归一查询零召回（实得 {rows}）"
            rows_bm25 = search_fts_bm25(c, "机器学习", top_k=5)
            assert mid in [r[0] for r in rows_bm25], "BM25 OR 通道同样必须命中"

    def test_fullwidth_query_hits_halfwidth_content(self, engine):
        """TDD 2 反向臂：半角正文、查全角「ｄｅｃｉｓｉｏｎ」必须命中。"""
        from lantai.storage.fts import search_fts

        mid = self._add_mem_with_fts(engine, "decision support system for deployment")
        with engine.connect() as conn:
            c = conn.connection.driver_connection
            rows = search_fts(c, "ｄｅｃｉｓｉｏｎ", top_k=5)
            assert mid in rows, f"归一缝缺失：全角查询对半角正文零召回（实得 {rows}）"

    def test_nfd_content_hit_by_nfc_query(self, engine):
        """NFC↔NFD 臂（macOS 文件名/某些 IME 形态）：双向确定性 miss。"""
        from lantai.storage.fts import search_fts

        mid = self._add_mem_with_fts(engine, "cafe\u0301 ordering workflow")
        with engine.connect() as conn:
            c = conn.connection.driver_connection
            rows = search_fts(c, "café", top_k=5)
            assert mid in rows, f"归一缝缺失：NFC 查询对 NFD 正文零召回（实得 {rows}）"

    def test_index_copy_normalized_content_untouched(self, engine):
        """TDD 3 哨兵（两态皆绿的行为锁）：只归一索引副本，正文逐字节不动。"""
        from sqlmodel import select

        mid = self._add_mem_with_fts(engine, _FULLWIDTH_CONTENT)
        with Session(engine) as s:
            row = s.exec(select(MemoryItem).where(MemoryItem.id == mid)).one()
            assert row.content == _FULLWIDTH_CONTENT, "用户资产不得被改写（獬豸同一原则）"
            assert _ZWSP in row.content, "ZWSP 必须仍在原文里（改写即越权）"

    def test_legacy_raw_index_rebuild_normalizes(self, engine):
        """存量落点：未归一旧索引全量重建后可被归一查询命中（派生数据重放）。"""
        from lantai.storage.fts import rebuild_fts, search_fts

        mid = new_id("mem")
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="general",
                    key=mid,
                    content=_FULLWIDTH_CONTENT,
                    lane="general",
                    status="active",
                    importance=0.5,
                    use_count=0,
                    decay_score=1.0,
                    last_used_at=utcnow(),
                    created_at=utcnow(),
                )
            )
            s.commit()
        # 模拟归一缝上线前的存量索引：原文直灌（未过缝）
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            raw.execute(
                "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
                (mid, _FULLWIDTH_CONTENT),
            )
            raw.commit()

        with Session(engine) as s:
            n = rebuild_fts(s)
            s.commit()  # rebuild_fts 不 commit（事务归调用方，同 sync_fts 口径）
        assert n == 1
        with engine.connect() as conn:
            c = conn.connection.driver_connection
            assert search_fts(c, "机器学习") == [mid], "重建后存量索引必须可被归一查询命中"


class TestWorkItemNormalization:
    """病灶 3：案牍去重键 casefold 不含 NFKC——全角标题变体不归组。"""

    def test_fullwidth_halfwidth_summaries_group_together(self):
        """TDD 4：全角/半角同文摘要两条候选必须归并同组（真函数直调）。"""
        from lantai.services.work_item_service import candidate_group_ids

        rows = [
            {"id": "cand_w", "summary": "ＡＰＩ密钥决策记录", "document_id": ""},
            {"id": "cand_a", "summary": "API密钥决策记录", "document_id": ""},
        ]
        groups = candidate_group_ids(rows)
        assert groups.get("cand_w") and groups["cand_w"] == groups.get("cand_a"), (
            f"去重键未折宽度：全角变体不归组（实得 {groups}）——待审队列双份工作项"
        )

    def test_identical_summaries_still_group(self):
        """回归护栏（两态皆绿）：归一不得破坏既有精确重复归组。"""
        from lantai.services.work_item_service import candidate_group_ids

        rows = [
            {"id": "cand_1", "summary": "部署前必须执行 dry-run", "document_id": "doc1"},
            {"id": "cand_2", "summary": "部署前必须执行 dry-run", "document_id": "doc2"},
        ]
        groups = candidate_group_ids(rows)
        assert groups.get("cand_1") == groups.get("cand_2") and groups.get("cand_1")

    def test_list_query_folds_width(self, engine):
        """案牍列表查询匹配同理（:627）：半角查询必须命中全角标题工作项。"""
        import lantai.storage.db as db_module
        from lantai.services.work_item_service import list_work_items

        with Session(engine) as s:
            s.add(
                MemoryCandidate(
                    id="cand_list_w",
                    document_id="doc_list_w",
                    user_id="default",
                    summary="ＡＰＩ密钥决策记录",
                    status="pending_review",
                )
            )
            s.commit()

        with pytest.MonkeyPatch.context() as mp2:
            mp2.setattr(db_module, "get_session", lambda: Session(engine))
            resp = list_work_items(query="api密钥")
        assert resp.total == 1, (
            f"列表查询未折宽度：半角查询漏掉全角标题工作项（total={resp.total}）"
        )


class TestRegistryItemsNormalization:
    """病灶 4 登记项：反思聚类签名与检索指纹只 lower 不折宽度。"""

    def test_norm_hash_folds_width(self):
        """检索指纹（观测口径不参与召回）：全角/半角同文必须同指纹。"""
        from lantai.observability.retrieval_log import _norm_hash

        assert _norm_hash("ｄｅｃｉｓｉｏｎ ＡＩ") == _norm_hash("decision ai"), (
            "查询指纹未折宽度：同文双指纹稀释 dry-run 指标"
        )

    def test_detect_patterns_folds_width_bucketing(self):
        """反思聚类签名：全角/半角同文经验必须同桶（1 pattern × 4 次现）。"""
        from lantai.cognition.evolution import EvolutionEngine
        from lantai.models.tables import CognitiveRole

        def _obs(idx: int, content: str) -> MemoryItem:
            return MemoryItem(
                id=f"obs_{idx}",
                content=content,
                role=CognitiveRole.OBSERVATION,
                source_ids=[f"src_{idx}"],
                status="active",
            )

        experiences = [
            _obs(0, "ＡＰＩ密钥决策记录"),
            _obs(1, "ＡＰＩ密钥决策记录"),
            _obs(2, "API密钥决策记录"),
            _obs(3, "API密钥决策记录"),
        ]
        patterns = EvolutionEngine().detect_patterns(experiences)
        assert len(patterns) == 1, (
            f"聚类签名未折宽度：全角/半角同文分成 {len(patterns)} 桶 "
            f"（实得 {[p.occurrence_count for p in patterns]}）"
        )
        assert patterns[0].occurrence_count == 4
