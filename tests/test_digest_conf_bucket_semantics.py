"""票 86:digest 置信桶「终桶含 1.0」判定不挂 label 字面量——数值语义单点。

病灶（digest_worker.py:139）：
    if lo <= conf < hi or (label == "0.9-1.0" and conf == 1.0):
终桶右端闭区间的判定挂在标签字符串字面量上，与 settings.DIGEST_CONF_BUCKETS
真源耦合漂移——任何人按 ADR-0002 反硬编码精神调整桶定义（改名/拆桶/阈值微调）
后，conf == 1.0 的提案**静默落「其他」兜底桶**：不 crash、不告警，日报/校准
报告顶多「其他」多一行，语义错无人知。

修复语义：hi >= 1.0 的桶按数值语义收纳 conf == 1.0（右端闭区间），与标签名
无关；「其他」兜底标签收编为模块常量（settings 桶定义之外才出现的置信值归此）。

不 mock 口径：真内存库（SQLModel.metadata.create_all 建全表）直调
`_aggregate_reflection`，SQL 查询与分桶循环全部真实执行；monkeypatch 只替换
**桶定义输入**（digest_worker._CONF_BUCKETS——模块导入期对 settings 的快照，
单 patch settings 无效，快照即被测消费面；票面验收 1「测试内 monkeypatch 桶
定义」即此意），不触碰被测函数内部逻辑。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
import lantai.workers.digest_worker as digest_worker
from lantai.models.tables import MemoryProposal

# 桶定义漂移臂的替身：与默认五桶阈值逐项相同，仅终桶标签改名
# （票面验证口径「终桶标签改成别名、阈值不动」——唯一变量是标签）。
_RENAMED_TERMINAL_BUCKETS = (
    ("0.5-0.6", 0.5, 0.6),
    ("0.6-0.7", 0.6, 0.7),
    ("0.7-0.8", 0.7, 0.8),
    ("0.8-0.9", 0.8, 0.9),
    ("0.9+", 0.9, 1.0),
)


@pytest.fixture()
def bucket_env(monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session——直调与 collect 全真路径。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory


def _seed_conf_one(session_factory) -> None:
    """灌一条 decided_by="reflect"、confidence=1.0 的真提案（漂移受害形状）。"""
    now = datetime.now(UTC)
    with session_factory() as s:
        s.add(
            MemoryProposal(
                id="p-conf-one",
                proposal_type="add",
                candidate_id=None,
                decided_by="reflect",
                evidence_ids=["e1"],
                proposed_patch={"key": "a"},
                confidence=1.0,
                status="applied",
                created_at=now,
                applied_at=now,
            )
        )
        s.commit()


class TestConfBucketTerminalSemantics:
    """终桶收纳 conf==1.0 凭数值语义（hi >= 1.0 右端闭区间），不凭标签。"""

    def test_conf_one_lands_in_terminal_bucket_regardless_of_label(self, bucket_env, monkeypatch):
        """漂移臂（票 86 验收 1）：终桶改名后 conf=1.0 仍落数值正确的桶。

        修复前：分桶判据 `label == "0.9-1.0"` 撞不上改名后的 "0.9+"，
        conf=1.0 静默落「其他」——本臂红。
        修复后：hi >= 1.0 的桶按数值语义收纳，标签名无关——绿。
        渲染断言顺带锁定日报面经同一 `_CONF_BUCKETS` 源自动跟随。
        """
        monkeypatch.setattr(digest_worker, "_CONF_BUCKETS", _RENAMED_TERMINAL_BUCKETS)
        _seed_conf_one(bucket_env)

        now = datetime.now(UTC)
        with bucket_env() as s:
            rf = digest_worker._aggregate_reflection(
                s, now - timedelta(hours=1), now + timedelta(hours=1)
            )

        assert rf["conf_buckets"].get("0.9+") == 1, (
            f"conf=1.0 该按数值语义落进 hi>=1.0 的终桶（0.9+），实际分桶 {rf['conf_buckets']}"
        )
        assert "其他" not in rf["conf_buckets"], (
            f"conf=1.0 不得静默落「其他」兜底桶（标签耦合漂移病灶），实际 {rf['conf_buckets']}"
        )

        # 端到端臂：真 collect_digest_stats（patch 过的 get_session → 同一内存库）
        # + 真渲染，日报面经同一 _CONF_BUCKETS 源自动跟随改名后的标签
        e2e = digest_worker.collect_digest_stats()["reflection"]
        assert e2e["conf_buckets"].get("0.9+") == 1, (
            f"collect 端到端分桶该与直调一致，实际 {e2e['conf_buckets']}"
        )
        md = digest_worker.render_digest_markdown(digest_worker.collect_digest_stats())
        assert "[0.9+]×1" in md, f"日报渲染该跟随改名后的终桶标签，实际：{md!r}"
        assert "[其他]" not in md, f"conf=1.0 不得在日报渲染面落「其他」，实际：{md!r}"

    def test_conf_one_default_buckets_status_quo(self, bucket_env):
        """现状锁（票 86 验收 2）：默认桶定义下 conf=1.0 落 "0.9-1.0" 不变。

        修复前即绿（字面量恰好一致）；修复后必须保持绿——数值语义
        （hi >= 1.0）在默认定义下与字面量判定行为等价，不得改坏现状。
        """
        _seed_conf_one(bucket_env)

        now = datetime.now(UTC)
        with bucket_env() as s:
            rf = digest_worker._aggregate_reflection(
                s, now - timedelta(hours=1), now + timedelta(hours=1)
            )

        assert rf["conf_buckets"]["0.9-1.0"] == 1, (
            f"默认桶下 conf=1.0 该落 0.9-1.0 终桶，实际 {rf['conf_buckets']}"
        )
        assert "其他" not in rf["conf_buckets"], (
            f"默认桶下 1.0 不该进兜底桶，实际 {rf['conf_buckets']}"
        )

    def test_outlier_fallback_label_independent(self, bucket_env, monkeypatch):
        """兜底臂：「其他」兜底桶对桶外值（<0.5）语义不随标签漂移。

        改名终桶后，桶外置信 0.3 仍落「其他」且渲染面显示——兜底桶
        与分桶真源的解耦是双向的（终桶判定与兜底收纳都不看标签字面量）。
        """
        monkeypatch.setattr(digest_worker, "_CONF_BUCKETS", _RENAMED_TERMINAL_BUCKETS)
        now = datetime.now(UTC)
        with bucket_env() as s:
            s.add(
                MemoryProposal(
                    id="p-outlier",
                    proposal_type="add",
                    candidate_id=None,
                    decided_by="reflect",
                    evidence_ids=["e1"],
                    proposed_patch={"key": "b"},
                    confidence=0.3,
                    status="rejected",
                    created_at=now,
                )
            )
            s.commit()
            rf = digest_worker._aggregate_reflection(
                s, now - timedelta(hours=1), now + timedelta(hours=1)
            )

        assert rf["conf_buckets"].get("其他") == 1, (
            f"桶外置信 0.3 该落「其他」兜底桶（不静默丢失），实际 {rf['conf_buckets']}"
        )
        md = digest_worker.render_digest_markdown(digest_worker.collect_digest_stats())
        assert "[其他]×1" in md, f"改名后的桶定义下渲染面仍该显示兜底桶收纳，实际：{md!r}"
