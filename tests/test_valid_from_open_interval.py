"""valid_from「开端开放」语义根治（票 .scratch/v0.64-next-window-2026-11/08）。

病灶：SQLModel 0.0.42 下 `Field(default_factory=utcnow)` 列的显式
`valid_from=None` 在 flush 时被改写为落库时刻——「vf=NULL=开端开放」在
产品写入路径上无法表达，「NULL=未知」与「utcnow=落库时刻」数据层不可区分
（票 06 三层根因的最底层，e2/jigu/readpath 三处测试被迫用 SQL 补 NULL 续命）。

修法（方向 1）：列默认改 `Field(default=None)`——显式 None 穿透、缺省落
None；「主张现在开始生效」语义由 12 处产品写入点显式 `valid_from=utcnow()`
声明（迁移 v21 已确立 vf 必填、以 created_at 近似的口径）。

不 mock 冒烟：真实 MemoryItem + 真实 SQLite 落库回读，直调无内部替身。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


def _roundtrip(valid_from) -> MemoryItem:
    """真实落库再读回（expire 后从 DB 取，非内存对象）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="mem_vf_probe",
                content="用户计划下月办理护照签证",
                status="active",
                valid_from=valid_from,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        s.commit()
        s.expire_all()
        return s.get(MemoryItem, "mem_vf_probe")


class TestValidFromExplicitNoneSurvivesFlush:
    def test_explicit_none_survives_flush(self):
        """显式 valid_from=None 落库读回仍是 None（票 08 验收 1；修复前红——被改写为 utcnow）。"""
        row = _roundtrip(None)
        assert row.valid_from is None

    def test_explicit_value_unchanged(self):
        """显式 valid_from 落库保真（既有语义，防改默认时误伤）。"""
        from lantai.retrieval.temporal import _ensure_utc

        vf = datetime(2026, 5, 1, tzinfo=UTC)
        row = _roundtrip(vf)
        # 列 TypeDecorator 读回 naive（既有存储行为），归一 UTC 再比
        assert _ensure_utc(row.valid_from) == vf

    def test_omitted_falls_to_column_default_none(self):
        """不传 valid_from → 列默认 None（新契约：缺省不再 utcnow，
        「现在生效」由服务层写入点显式声明——模型层不再替调用方做时间假设）。"""
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id="mem_vf_omit",
                    content="裸构造",
                    status="active",
                    created_at=utcnow(),
                )
            )
            s.commit()
            s.expire_all()
            row = s.get(MemoryItem, "mem_vf_omit")
            assert row.valid_from is None

    def test_open_interval_visible_in_historical_as_of(self):
        """端到端语义：vf=None 的记忆在 as_of 历史视图中按开区间可见
        （修复前 vf 被污染为落库时刻 → 历史切片全数误杀，票 06 实证）。"""
        from lantai.retrieval.temporal import asof_matches

        row = _roundtrip(None)
        as_of = utcnow() - timedelta(days=30)
        ok, by = asof_matches(row, as_of, strict=True)
        assert ok is True and by == "unknown_soft"  # vf/vt/event_time 全 NULL → I4 豁免


class TestServiceWriteSitesDeclareValidFrom:
    """验收 2：服务层写入点显式声明「现在生效」（不 mock 冒烟，真实构造直调）。"""

    def test_build_verbatim_item_sets_valid_from(self):
        """verbatim 写入构造点（memory_service.build_verbatim_item）：vf 落 utcnow 非 NULL。"""
        from lantai.services.memory_service import build_verbatim_item

        item = build_verbatim_item("兰台票08冒烟：服务层 vf 回填", lane="general")
        assert item.valid_from is not None
        # 与 created_at 同源（代码里 valid_from=created），语义「主张自落库起生效」
        assert item.valid_from == item.created_at

    def test_forgetting_eval_seeds_keep_open_interval(self):
        """评测种子面（forgetting_quality._seed_case）：种子不带 valid_from_days
        → 显式 vf=None（开区间语义）落库保真——修复前会被 default_factory
        改写为 utcnow，遗忘评测的「未生效」与「开区间」不可区分。真实直调
        不 mock（真实 MemoryItem + 真实 SQLite）。"""
        from lantai.eval.forgetting_quality import _seed_case
        from lantai.storage.fts import init_fts

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            init_fts(conn.connection.driver_connection)  # _seed_case 内部 sync_fts 需要
        now = utcnow()
        case = {"seeds": [{"content": "兰台票08冒烟：开区间种子", "memory_type": "semantic"}]}
        with Session(engine) as s:
            mapping = _seed_case(s, case, now)
            s.commit()
            s.expire_all()
            row = s.get(MemoryItem, mapping["0"])
            assert row.valid_from is None
