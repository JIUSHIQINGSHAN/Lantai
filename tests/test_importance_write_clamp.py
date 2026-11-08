"""票 35：importance 写入面无界——一条毒行让遗忘引擎确定性崩溃。

病灶（`.scratch/v0.68-next-window-2026-11/issues/35-importance-unbounded-forgetting-stall.md`，
立案轮 6 探针 `_v068_probe_r6_forgetting_poison.py` 实锤）：`_lane_strength` 对
importance 无下界假设（chat 泳道 base_s=3、boost=5：importance=-0.6 → strength=0.0
→ `exp(-days/0)` ZeroDivisionError；-0.61 → strength<0 → `exp(+800)`
OverflowError），而三条写入路径全部不钳制——`apply_forgetting` keyset 分页主循环
零容错，毒行让全库衰减与自动归档永久停摆，同页对照行已算好的衰减随异常一起回滚。

修复四件（票面修复目标 1/2/3）：
1. REST 更新面 pydantic `ge/le` 拒绝（422，宁 miss 不脏写；NaN/inf 一并拒——
   pydantic 实证带 ge/le 约束时三者全 ValidationError），惯例先例
   routes_cognitive.py:37-38 / routes_jiaochou.py:47；
2. 沉潜生成面 / promoter apply 面钳入 [0,1]（mokan.py:121
   `max(0.0, min(1.0, float(x)))` 既有习语）；非有限值 NaN/inf：生成面跳过该簇
   （宁 miss 不脏写），apply 面落 REJECTED 终态（拒绝必须终态，防
   evolve_worker 对 APPROVED 提案每轮重试＝livelock，`_reject_proposal` 口径）；
3. `_lane_strength` 强度下限兜底 `MIN_LANE_STRENGTH_DAYS`：历史毒行按地板强度
   衰减 → decay 立即跌破归档阈值 → 被正常归档，引擎自愈。存量排查（2026-11-08，
   真实库 remembrance.db 只读）：4 行、0 行负值、min 0.1 / max 0.5——无存量订正。
4. `apply_forgetting` 逐行容错：脏数据行（TEXT importance 经无闸 import 面落库，
   票 47 收口前的现实入口——本文件探针实证 TEXT 落 REAL 列活着过 ORM fetch、
   在循环内 `boost * 'oops'` TypeError）不再炸穿 keyset 主循环：跳过 + 留痕 +
   健康行照常衰减（宁 miss 不脏写，修复前同页对照行衰减随异常一起回滚）。

不 mock 冒烟（AGENTS.md）：真实 SQLite（StaticPool 内存库）+ 真 FTS + 真
`apply_forgetting` / `consolidate_cluster` / `apply_proposal` / TestClient 全链；
替身只在外部 LLM/embed/向量库边界（chat_json / embed / 向量记账），被测链路零
mock。时间用真实 utcnow + 播种过去时刻构造天数差（同 test_forgetting_exempt_archive
口径）。现状即绿臂如实申报：PUT 边界值 0.0/1.0 接受（修复前即如此）。
"""

import logging
import math
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import get_current_user
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal
from lantai.memory.forgetting import apply_forgetting
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryItem, MemoryProposal
from lantai.services.consolidation_service import (
    consolidate_cluster,
    find_consolidation_clusters,
)

# chat 泳道 profile（settings.LANE_DECAY_PROFILES["chat"]）：base_s=3, boost=5
_CHAT_BASE_S = 3
_CHAT_BOOST = 5


def _ago(days: float):
    return utcnow() - timedelta(days=days)


def _seed_memory(s: Session, mid: str, *, importance: float, lane: str = "chat", created=None):
    s.add(
        MemoryItem(
            id=mid,
            content=f"票35 {mid}",
            memory_type="text",
            lane=lane,
            tier="working",
            importance=importance,
            decay_class="episodic",
            use_count=0,
            helpful_count=0,
            status="active",
            decay_score=1.0,
            created_at=created,
            last_used_at=created,
        )
    )
    s.commit()


# ── 公式地板（修复目标 2）：历史毒行不再炸穿引擎，且被正常归档自愈 ──────────


class TestFormulaFloor:
    def test_zero_strength_poison_row_archived_not_crash(self, param_env):
        """importance=-0.6（chat，40 天）→ strength=0.0：修复前 ZeroDivisionError，
        修复后按地板强度衰减 → decay 跌破阈值 → 正常归档。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_memory(s, "mem_ctl", importance=0.5, created=_ago(40))
            _seed_memory(s, "mem_poison", importance=-0.6, created=_ago(40))
        apply_forgetting()
        with session_factory() as s:
            poison = s.get(MemoryItem, "mem_poison")
            assert poison.status == "archived"
            assert poison.decay_score == pytest.approx(0.0, abs=1e-9)

    def test_negative_strength_overflow_row_archived_not_crash(self, param_env):
        """importance=-0.61（chat，40 天）→ strength<0：修复前 exp(+800)
        OverflowError，修复后同上自愈归档。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_memory(s, "mem_ctl", importance=0.5, created=_ago(40))
            _seed_memory(s, "mem_poison", importance=-0.61, created=_ago(40))
        apply_forgetting()
        with session_factory() as s:
            poison = s.get(MemoryItem, "mem_poison")
            assert poison.status == "archived"
            assert poison.decay_score == pytest.approx(0.0, abs=1e-9)

    def test_control_row_decay_progresses_alongside_poison(self, param_env):
        """探针 A 对照行镜像：毒行 id 更大——修复前页内异常把对照行已算好的衰减
        一起回滚（decay 停在 1.0）；修复后对照行按公式正常推进。"""
        session_factory, _ = param_env
        with session_factory() as s:
            _seed_memory(s, "mem_ctl", importance=0.5, created=_ago(40))
            _seed_memory(s, "mem_poison", importance=-0.6, created=_ago(40))
        apply_forgetting()
        with session_factory() as s:
            ctl = s.get(MemoryItem, "mem_ctl")
            expected = math.exp(-40 / (_CHAT_BASE_S + _CHAT_BOOST * 0.5))
            assert ctl.decay_score == pytest.approx(expected, rel=1e-3)


# ── 写面锁·REST（修复目标 1，方向＝拒绝 422）───────────────────────────────


@pytest.fixture()
def rest_env(monkeypatch):
    """同 test_terminal_write_guards 形态：真 DB + 真 FTS + 真 HTTP，替身仅限
    向量库/embed 外部面。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    dummy = type(
        "D",
        (),
        {
            "add": lambda self, *, ids, embeddings, metadatas: None,
            "delete": lambda self, *a, **k: None,
        },
    )()
    monkeypatch.setattr("lantai.storage.vector_store.get_vector_store", lambda: dummy)
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: dummy)
    import lantai.services.record_ops_service as ros

    monkeypatch.setattr(ros, "embed", lambda texts: [[0.1] * 1536 for _ in texts])

    with TestClient(app) as client:
        yield engine, client


def _principal(user_id="u1", role="user"):
    from lantai.core.auth import Principal

    return Principal(user_id=user_id, role=role, allowed_lanes=["general"], tenant_id=None)


def _add_owned(engine, *, mid="mem_t35", user_id="u1"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                content="票35 REST 面种子",
                memory_type="text",
                lane="general",
                tier="working",
                importance=0.5,
                decay_class="episodic",
                use_count=0,
                helpful_count=0,
                status="active",
                decay_score=1.0,
                user_id=user_id,
            )
        )
        s.commit()


def _override(client, principal, monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: principal)


class TestWriteFaceRest:
    @pytest.mark.parametrize(
        "raw_body,label",
        [
            (b'{"importance": -0.6}', "负值（strength=0.0 病灶形状）"),
            (b'{"importance": NaN}', "NaN（SQLite 绑 NULL 炸 NOT NULL 形态）"),
            (b'{"importance": Infinity}', "inf（永不衰减轻症形态）"),
        ],
    )
    def test_put_out_of_range_importance_rejected_422(self, rest_env, monkeypatch, raw_body, label):
        """票面修复目标 1：REST 更新面拒绝越界（422 而非 500/落库），越界请求
        不改库（宁 miss 不脏写）。NaN/inf 走 raw body——它们是非标准 JSON 字面量
        （httpx 的 json= 用 allow_nan=False 发不出去），宽松解析器才产出的形状。"""
        engine, client = rest_env
        _add_owned(engine)
        _override(client, _principal(), monkeypatch)
        resp = client.put(
            "/terminal/memory/mem_t35",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422, f"{label}: 期望 422，得到 {resp.status_code}"
        with Session(engine) as s:
            assert s.get(MemoryItem, "mem_t35").importance == 0.5, "422 请求不得改库"

    def test_put_boundary_importance_accepted(self, rest_env, monkeypatch):
        """现状即绿锁（如实申报非红转绿）：新约束的边界值 0.0/1.0 仍被接受，
        修复前即如此——锁「拒绝面只收窄越界，不误伤界内」。"""
        engine, client = rest_env
        _add_owned(engine)
        _override(client, _principal(), monkeypatch)
        resp = client.put("/terminal/memory/mem_t35", json={"importance": 0.0})
        assert resp.status_code == 200
        with Session(engine) as s:
            assert s.get(MemoryItem, "mem_t35").importance == 0.0
        resp = client.put("/terminal/memory/mem_t35", json={"importance": 1.0})
        assert resp.status_code == 200
        with Session(engine) as s:
            assert s.get(MemoryItem, "mem_t35").importance == 1.0


# ── 写面锁·沉潜生成面（修复目标 1，方向＝钳制 + 非有限跳过）─────────────────

# t21 同构种子（同主题偏好碎片，聚类稳定命中；owner 四元组从首碎片继承）
_OWNER = {
    "tenant_id": "t_acme",
    "user_id": "u_brother",
    "agent_id": "",
    "session_id": "s_tea",
}

_FRAG_IDS = ["mem_frag_01", "mem_frag_02", "mem_frag_03"]

_PURIFIED_NEG = {
    "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
    "importance": -5.0,
    "confidence": 0.95,
}
_PURIFIED_NAN = {
    "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
    "importance": float("nan"),
    "confidence": 0.95,
}


def _seed_fragments(s: Session):
    s.add_all(
        [
            MemoryItem(
                id="mem_frag_01",
                content="大哥今天早上泡了明前大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
                **_OWNER,
            ),
            MemoryItem(
                id="mem_frag_02",
                content="大哥喜欢喝大佛龙井茶，水温要求85度",
                lane="preference",
                domain="user",
                decay_score=0.88,
                status="active",
                **_OWNER,
            ),
            MemoryItem(
                id="mem_frag_03",
                content="大哥日常饮品偏好为浙江新昌大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.85,
                status="active",
                **_OWNER,
            ),
        ]
    )
    s.commit()


def _cluster_of_three(s: Session):
    clusters = find_consolidation_clusters(s, min_cluster_size=3)
    assert len(clusters) >= 1
    return clusters[0]


class TestWriteFaceConsolidation:
    def test_llm_negative_importance_clamped_in_enforce_proposal(self, param_env, monkeypatch):
        """票面修复目标 1·沉潜生成面：LLM 提取 importance=-5.0 钳入 [0,1] 落
        提案 patch（mokan.py:121 钳制习语）；confidence 不受钳制面波及。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")
        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_PURIFIED_NEG),
            ):
                out = consolidate_cluster(cluster, session=s)
        assert isinstance(out, MemoryProposal), "enforce 生成面应产出提案"
        assert out.proposed_patch["importance"] == 0.0
        assert out.proposed_patch["confidence"] == 0.95, "钳制只针对 importance"

    def test_llm_nan_importance_skips_fold(self, param_env, monkeypatch):
        """非有限值（NaN/inf）生成面跳过该簇：宁 miss 不脏写——碎片原状保留，
        不落提案（NaN 经 max/min 习语会洗成 1.0 脏写，故必须前置 isfinite）。"""
        session_factory, _ = param_env
        monkeypatch.setattr(settings, "CONSOLIDATION_AUDIT_MODE", "enforce")
        with session_factory() as s:
            _seed_fragments(s)
            cluster = _cluster_of_three(s)
            with patch(
                "lantai.services.consolidation_service.chat_json",
                return_value=dict(_PURIFIED_NAN),
            ):
                out = consolidate_cluster(cluster, session=s)
        assert out is None, "非有限 importance 应跳过折叠"
        with session_factory() as s:
            assert s.exec(select(MemoryProposal)).first() is None, "不得落提案行"
            for fid in _FRAG_IDS:
                assert s.get(MemoryItem, fid).status == "active", "碎片原状保留"


# ── 写面锁·promoter apply 面（修复目标 1，存量提案 patch 的最后一道防线）──────


def _seed_proposal(s: Session, *, importance) -> MemoryProposal:
    prop = MemoryProposal(
        id=new_id("prop"),
        proposal_type="consolidation",
        evidence_ids=[],
        reason="票35 apply 面锁",
        proposed_patch={
            "content": "大哥偏好喝龙井茶",
            "importance": importance,
            "confidence": 0.9,
            "domain": "user",
            "lane": "general",
            "source_ids": [],
            "memory_type": "semantic",
        },
        confidence=0.9,
        status=ProposalStatus.PENDING,
        decided_by="test",
    )
    s.add(prop)
    s.commit()
    return prop


class TestWriteFacePromoterApply:
    def test_apply_clamps_finite_out_of_range_patch(self, param_env):
        """apply 面对有限越界值钳入 [0,1] 后落主记忆（存量 patch 的最后一道
        防线——生成面立法前的提案照常可应用，值不再带毒）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            prop = _seed_proposal(s, importance=-5.0)
            prop_id = prop.id
        with patch("lantai.evolution.promoter.embed", lambda texts: [[0.1] * 8]):
            out = apply_proposal(prop_id)
        assert out["ok"] is True, f"apply 应成功：{out}"
        with session_factory() as s:
            master = s.exec(
                select(MemoryItem).where(MemoryItem.content == "大哥偏好喝龙井茶")
            ).first()
            assert master is not None
            assert master.importance == 0.0

    def test_apply_rejects_nonfinite_patch(self, param_env):
        """apply 面对非有限值落 REJECTED 终态（不早退——APPROVED 永留会让
        evolve_worker 每轮重试＝livelock，`_reject_proposal` docstring 口径）；
        不写主记忆（NaN 直落 SQLite 被 NOT NULL 拦下炸 IntegrityError）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            prop = _seed_proposal(s, importance=float("nan"))
            prop_id = prop.id
        with patch("lantai.evolution.promoter.embed", lambda texts: [[0.1] * 8]):
            out = apply_proposal(prop_id)
        assert out["ok"] is False
        assert out["reason"] == "importance_not_finite"
        with session_factory() as s:
            reloaded = s.get(MemoryProposal, prop.id)
            assert reloaded.status == ProposalStatus.REJECTED, "拒绝必须终态落库"
            assert reloaded.decision_reason == "importance_not_finite"
            assert (
                s.exec(select(MemoryItem).where(MemoryItem.content == "大哥偏好喝龙井茶")).first()
                is None
            ), "拒绝提案不得写主记忆"


# ── 循环容错（修复目标 3）：脏行跳过 + 留痕，主循环不死 ────────────────────


class TestLoopGuard:
    def test_dirty_row_skipped_with_trace_loop_continues(self, param_env, caplog):
        """TEXT importance 脏行（无闸 import 面的现实形态，票 47 收口前的入口；
        本文件探针实证：TEXT 落 REAL 列活着过 ORM fetch、在循环内
        `boost * 'oops'` TypeError）不再炸穿 keyset 主循环——脏行本周期跳过并
        留痕，健康行照常衰减（修复前异常炸穿，同页对照行衰减一起回滚）。"""
        session_factory, engine = param_env
        with session_factory() as s:
            _seed_memory(s, "mem_a_dirty", importance=0.5, created=_ago(40))
            _seed_memory(s, "mem_ctl", importance=0.5, created=_ago(40))
        with engine.connect() as c:
            c.execute(text("UPDATE memoryitem SET importance='oops' WHERE id='mem_a_dirty'"))
            c.commit()
        with caplog.at_level(logging.WARNING):
            apply_forgetting()
        with session_factory() as s:
            dirty = s.get(MemoryItem, "mem_a_dirty")
            ctl = s.get(MemoryItem, "mem_ctl")
        assert dirty.status == "active", "脏行本周期跳过（宁 miss 不脏写）"
        assert dirty.decay_score == 1.0, "脏行衰减分不得被误写"
        expected = math.exp(-40 / (_CHAT_BASE_S + _CHAT_BOOST * 0.5))
        assert ctl.decay_score == pytest.approx(expected, rel=1e-3), "健康行照常衰减"
        assert "mem_a_dirty" in caplog.text, "脏行跳过必须留痕（行 id 入日志）"
