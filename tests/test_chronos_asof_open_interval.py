"""as-of 历史视图查询不被 Chronos 当前态过滤器误杀（票 06 / v0.64）。

病灶（diagnosing-bugs 五步收敛）：`hybrid_search(as_of=历史时刻)` 的链路里，
`_chronos_filter`（当前态「未到 valid_from 剔除」语义）先于
`temporal_view_filter`（as-of 时效视图，ADR-0048 spec §3.2）执行——
而 SQLModel 0.0.42 下 `Field(default_factory=utcnow)` 列的显式 `valid_from=None`
在 flush 时被改写为落库时刻（temporal.py:71 既有已知限制，I4 承载面因此
定为 event_time 并已绕开）。于是任何 vf/vt 开区间（None → 落库 utcnow）
的种子记忆在 as-of 查询中被 `_chronos_filter` 以「vf(今天) > as_of(历史)」
剔除，temporal_view_filter 根本见不到它——E2 双视图评测 6 例零召回、
accuracy 0.8378 < 0.9 的根因。

单一职责修法：as_of 显式查询时，valid_from/valid_to 的时效判定**全权归
temporal_view_filter**（asof_matches 的 validity 分支 + I4 + strict 语义）；
`_chronos_filter` 的 vf>now 剔除只在当前态查询（as_of 缺省）执行。
vt 衰减（0.3×）在两种模式下都保留（as_of 模式下按 as_of 时刻算过期衰减，
与 temporal_view_filter 的软降权乘子语义正交）。

不 mock 冒烟：真实 MemoryItem + 真实 SQLite（显式 valid_from=None 落库复现
SQLModel 改写）+ 直调 `_chronos_filter`。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


def _seed_and_readback(valid_from, valid_to, event_time=None, precision=""):
    """真实落库再读回：复现 SQLModel 0.0.42 对显式 None 的 default_factory 改写。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="mem_vf_none",
                content="用户计划下月办理护照签证",
                status="active",
                valid_from=valid_from,
                valid_to=valid_to,
                event_time=event_time,
                event_time_precision=precision,
                created_at=utcnow(),
            )
        )
        s.commit()
        s.expire_all()
        return s.get(MemoryItem, "mem_vf_none")


class TestChronosAsOfDoesNotEvictOpenInterval:
    """as_of 模式下 _chronos_filter 不做 vf>now 剔除（时效归 temporal_view_filter）。"""

    def test_as_of_keeps_item_with_polluted_valid_from(self):
        """vf 开区间（None）+ as_of 指向历史 → 不剔除。

        **契约更新（票 `.scratch/v0.64-next-window-2026-11/08`）**：原版本
        依赖「显式 None 落库被改写为 utcnow」的污染前提复现票 06 病灶；票 08
        根治后 None 保真，本测试职责转为固化「开区间种子在 as-of 视图不被
        chronos 误杀」这一语义本身（不再依赖污染复现——污染已不存在）。
        """
        from lantai.retrieval.hybrid import _chronos_filter

        row = _seed_and_readback(None, None)
        # 票 08 根治后：显式 None 落库保真（vf IS NULL = 开端开放，不再被改写）
        assert row.valid_from is None
        as_of = utcnow() - timedelta(days=30)  # 历史时刻
        kept = _chronos_filter([row], as_of=as_of)
        assert [m.id for m in kept] == ["mem_vf_none"]

    def test_current_view_still_evicts_future_valid_from(self):
        """当前态（as_of 缺省）：vf 在未来仍被剔除（既有语义零回归）。"""
        from lantai.retrieval.hybrid import _chronos_filter

        m = MemoryItem(
            id="mem_future",
            content="尚未生效的预约",
            status="active",
            valid_from=utcnow() + timedelta(days=7),
            created_at=utcnow(),
        )
        kept = _chronos_filter([m])
        assert kept == []

    def test_as_of_still_decays_expired_valid_to(self):
        """as_of 模式下 vt < as_of 的过期衰减 0.3× 保留（chronos 另一半职责不丢）。"""
        from lantai.retrieval.hybrid import _chronos_filter

        as_of = utcnow()
        m = MemoryItem(
            id="mem_expired",
            content="已过期的承诺",
            status="active",
            valid_from=as_of - timedelta(days=30),
            valid_to=as_of - timedelta(days=1),
            created_at=as_of,
        )
        kept = _chronos_filter([m], as_of=as_of)
        assert len(kept) == 1
        assert kept[0].decay_score == 0.3

    def test_event_time_carrier_unaffected(self):
        """I4 承载面（event_time）不受影响：event_time 非空条目照常走后续判定。"""
        from lantai.retrieval.hybrid import _chronos_filter

        row = _seed_and_readback(
            None, None, event_time=datetime(2026, 8, 20, 12, 0, tzinfo=UTC), precision="second"
        )
        as_of = utcnow() - timedelta(days=30)
        kept = _chronos_filter([row], as_of=as_of)
        assert [m.id for m in kept] == ["mem_vf_none"]
