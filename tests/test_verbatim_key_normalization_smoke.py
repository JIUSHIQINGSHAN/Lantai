"""票 46：verbatim 身份键 = sha256(原文字节)——归一形态一变即分裂双份记忆。

`.scratch/v0.68-next-window-2026-11/issues/46-verbatim-key-unnormalized-sha.md`

**先说影响**：verbatim 是内容寻址（sha256 即 id 语义，docstring 自称「同一段
文本就是同一条记忆」），但键对**原文字节**取 sha——IME 全半角翻转、macOS
NFD、网页粘贴的零宽字符产生不同字节 → 不同 sha → 同一逻辑内容两条 active
verbatim 并存、双双进检索挤占预算，更正链（supersedes）可能只覆盖其一。
`add_raw_memory` 不走 `find_similar` 余弦去重（只有提取路径走），gate/dedup
救不了此路径；obsidian sync 每 sync 一次多存一份的原始病在宽度变体下复发。

**mock 边界声明**（AGENTS.md 纪律）：真 SQLite 内存库 + 真 `add_raw_memory`
/`_store_imported_memory`/`build_verbatim_item` 直调；`embed` 走 conftest
autouse 外部替身（AGENTS.md 允许面：外部网络）；`db.get_session` 指向内存库
属接线替身（test_fts_integration 先例），测试体内 `MonkeyPatch.context`
（conftest 模块卫生绊线：还原先于一切 fixture teardown）。
"""

from __future__ import annotations

import hashlib
import unicodedata

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.schemas import RawMemoryReq
from lantai.models.tables import MemoryItem

_CLEAN = "decision: 使用 PostgreSQL"
_FULLWIDTH = "ｄｅｃｉｓｉｏｎ：使用 PostgreSQL"
_NFD = "café: 使用 PostgreSQL"  # 分解形态（U+0301 组合尖音符），转义立法防字面量走形
_NFC = unicodedata.normalize("NFC", _NFD)  # 组合形态（U+00E9）——由分解形态派生


def _raw_sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    from lantai.storage.fts import init_fts

    init_fts(e.raw_connection())
    return e


def _seed_legacy_verbatim(engine, content: str) -> str:
    """预置旧格式行：key = 原文 sha（归一缝上线前的存量形态）。"""
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="verbatim",
                key=_raw_sha(content),
                content=content,
                lane="general",
                tier="long_term",
                status="active",
                confidence=1.0,
                importance=0.5,
                decay_class="semantic",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()
    return mid


def _add_raw(engine, content: str) -> dict:
    import lantai.storage.db as db_module
    from lantai.services.memory_service import add_raw_memory

    with pytest.MonkeyPatch.context() as mp2:
        mp2.setattr(db_module, "get_session", lambda: Session(engine))
        return add_raw_memory(RawMemoryReq(content=content))


def _active_count(engine) -> int:
    with Session(engine) as s:
        return len(
            s.exec(
                select(MemoryItem).where(
                    MemoryItem.memory_type == "verbatim", MemoryItem.status == "active"
                )
            ).all()
        )


class TestVerbatimIdentityKey:
    """身份键改对归一后文本取 sha（新行），存量行双键并查。"""

    def test_fullwidth_variant_dedups_halfwidth(self, engine):
        """TDD 1：先存半角、再存全角同文 → 修复后判 duplicate（红：双行并存）。"""
        first = _add_raw(engine, _CLEAN)
        second = _add_raw(engine, _FULLWIDTH)
        assert _CLEAN != _FULLWIDTH and _raw_sha(_CLEAN) != _raw_sha(_FULLWIDTH)
        assert second.get("dedup") is True, (
            f"宽度变体分裂双份：first={first} second={second}，active={_active_count(engine)}"
        )
        assert _active_count(engine) == 1
        assert first["memory_id"] == second["memory_id"]

    def test_nfd_variant_dedups(self, engine):
        """TDD 3：NFD 分解形态与 NFC 组合形态同判（macOS 文件名/IME 形态）。"""
        assert _NFD != _NFC, "NFD/NFC 两形态被字面量走形合并（自检失效）"
        _add_raw(engine, _NFC)
        second = _add_raw(engine, _NFD)
        assert second.get("dedup") is True, f"NFD 变体分裂双份：{second}"
        assert _active_count(engine) == 1

    def test_legacy_raw_key_row_still_dedups_variant(self, engine):
        """TDD 2 存量臂：旧格式 key（原文 sha）的行，同文宽度变体仍判 duplicate。"""
        legacy_id = _seed_legacy_verbatim(engine, _CLEAN)
        second = _add_raw(engine, _FULLWIDTH)
        assert second.get("dedup") is True, (
            f"存量行漏判：legacy={legacy_id} second={second}（双键并查未生效）"
        )
        assert _active_count(engine) == 1

    def test_byte_identical_rewrite_still_dedups(self, engine):
        """回归护栏（两态皆绿）：字节级同文去重不因改键破坏。"""
        _add_raw(engine, _CLEAN)
        second = _add_raw(engine, _CLEAN)
        assert second.get("dedup") is True
        assert _active_count(engine) == 1

    def test_new_row_key_is_normalized_sha_and_content_untouched(self, engine):
        """新行 key = sha256(归一后文本)（纯函数直调）；正文逐字节不动（哨兵）。"""
        from lantai.core.text import normalize_for_index
        from lantai.services.memory_service import build_verbatim_item

        item = build_verbatim_item(_FULLWIDTH, "general")
        assert item.key == _raw_sha(normalize_for_index(_FULLWIDTH)), (
            "新行 key 未对归一后文本取 sha"
        )
        assert build_verbatim_item(_CLEAN, "general").key == item.key, "归一同文异键（身份缝未收敛）"
        assert item.content == _FULLWIDTH, "用户正文不得被改写（ADR-0085 同一边界）"


class TestImportPathSameSeam:
    """import_service 同缝（票面写入口 2/4）：_store_imported_memory 双键并查。"""

    def test_imported_fullwidth_variant_dedups(self, engine):
        import lantai.storage.db as db_module
        from lantai.services.import_service import _store_imported_memory

        with pytest.MonkeyPatch.context() as mp2:
            mp2.setattr(db_module, "get_session", lambda: Session(engine))
            with Session(engine) as s:
                first = _store_imported_memory(s, _CLEAN, None, None, "general", [])
                s.commit()
                second = _store_imported_memory(s, _FULLWIDTH, None, None, "general", [])
                s.commit()
        assert first == "imported"
        assert second == "duplicate", f"导入路径宽度变体分裂双份：{first}/{second}"
        assert _active_count(engine) == 1

    def test_imported_legacy_row_still_dedups(self, engine):
        """存量臂（导入侧）：旧格式 key 行 + 宽度变体 → duplicate。"""
        _seed_legacy_verbatim(engine, _CLEAN)
        import lantai.storage.db as db_module
        from lantai.services.import_service import _store_imported_memory

        with pytest.MonkeyPatch.context() as mp2:
            mp2.setattr(db_module, "get_session", lambda: Session(engine))
            with Session(engine) as s:
                result = _store_imported_memory(s, _FULLWIDTH, None, None, "general", [])
                s.commit()
        assert result == "duplicate", f"导入存量臂漏判：{result}"
        assert _active_count(engine) == 1
