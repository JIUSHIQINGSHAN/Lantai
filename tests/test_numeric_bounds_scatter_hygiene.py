"""票 68:数值边界与散点卫生九位点——真函数直调红臂(不 mock 被测链路)。

病灶(立案轮 8,HEAD 4559128b;行号以现行代码复核过):
1) conflicts._temporal_recency 未来时间无上钳——仲裁 recency 权重被时间异常行碾压;
2) niyi 拟议 patch.confidence 裸 float()——"high" 直接送 ValueError 冒 500,越界值不钳;
3) lifecycle falsy-or 吞 0.0——置信度 0.0 的 Active 逃逸 weaken、衰减分 0.0 的
   Superseded 逃逸 retire(「或 1.0」把合法零值当缺省);
4) zuozheng `confidence or 0.8`——零置信证据被静默抬成 0.8 权重;
5) shishi 写面——admin `.first()` 无 order_by 任意行覆盖、非 admin 可改写 ownerless 行;
6) canshang OPPOSITE_PAIRS 裸子串 + 全文字符集交叠——无关文本假矛盾、否定形
   (「不启用」含「启用」)假矛盾、同值否定形互判矛盾;
7) evolution dedup_key check-then-act 无 DB 唯一兜底——并发插双行;
8) cehou 终态窗口仍收样本 + JSON alerts 逐样本新串无上限;
   (票面行号基于内存 dict 形态,票 60 落库重写后病灶转移为上述两处,按现行形态立臂)
9) zhaji 落库截断硬编码 1000 无视 max_chars;diben 无 session_id 时全表加载、ACL 不下推。

红测纪律(AGENTS.md):真内存库 + 真引擎直调,不 mock 被测链路;jieba 为仓内既有
依赖(jiaochou 在用),canshang 分词判定不引入新依赖。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def bounds_env():
    """纯库面内存引擎:niyi/zuozheng/lifecycle/evolution/shishi 直调共用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _seed_memory(
    s,
    mid,
    content,
    *,
    user_id="default",
    tenant_id=None,
    confidence=0.5,
    status="active",
    structure=None,
    created_at=None,
):
    from lantai.models.tables import MemoryItem

    now = datetime.now(UTC)
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"k-{mid}",
            content=content,
            lane="fact",
            status=status,
            user_id=user_id,
            tenant_id=tenant_id,
            confidence=confidence,
            importance=0.5,
            decay_score=1.0,
            decay_class="episodic",
            structure=structure or {},
            created_at=created_at or now - timedelta(days=3),
            updated_at=now - timedelta(hours=1),
        )
    )


class _OnlyTimes:
    """`_temporal_recency` 的最小真实入参(同 test_canshang_api_smoke 先例,不 mock 函数本体)。"""

    def __init__(self, event_time=None, created_at=None):
        self.event_time = event_time
        self.created_at = created_at


# ── 位点 1:conflicts._temporal_recency 未来时间无上钳 ──────────────────────────


def test_recency_future_event_time_capped_at_one():
    """未来 event_time 修复前 recency 巨值(1.0 - 负天数/30),仲裁权重被异常行碾压;
    修复后上钳到 [0,1],未来时间至多按「今日」计,不奖励(宁降权不奖励取上钳口径)。"""
    from lantai.cognition.conflicts import _temporal_recency

    future_item = _OnlyTimes(event_time=datetime(3000, 1, 1, tzinfo=UTC))
    recency, axis = _temporal_recency(future_item)
    assert axis == "event"
    assert 0.0 <= recency <= 1.0, f"未来时间 recency 未上钳: {recency}"
    assert recency == 1.0, "未来时间应按上界 1.0 计(与今日同权,不加奖)"


def test_recency_regression_window_and_beyond():
    """回归锁(修复前即绿):窗内线性衰减、窗外下钳 0.0、轴判定语义不变。"""
    from lantai.cognition.conflicts import _temporal_recency

    now = datetime.now(UTC)
    newer = _OnlyTimes(event_time=now - timedelta(days=10))
    older = _OnlyTimes(event_time=now - timedelta(days=60))
    r_new, ax_new = _temporal_recency(newer)
    r_old, ax_old = _temporal_recency(older)
    assert r_new == pytest.approx(1.0 - 10 / 30.0)
    assert r_old == 0.0
    assert ax_new == "event"
    assert r_new > r_old
    created_only = _OnlyTimes(created_at=now - timedelta(days=1))
    r_c, ax_c = _temporal_recency(created_only)
    assert ax_c == "created"
    assert r_c > r_new


# ── 位点 2:niyi 拟议 confidence 裸 float() ────────────────────────────────────


def test_niyi_non_numeric_confidence_returns_error_receipt(bounds_env):
    """LLM 传 "high" 修复前 ValueError 冒泡 500;修复后按本函数既有失败形态
    (「未找到目标记忆,推演终止」同族)返回错误回执:confidence_delta=0.0、
    reason 如实说明拒绝原因,不中断不静默修正。"""
    from lantai.cognition.niyi import NiyiEngine

    _seed_memory(bounds_env, "nm-target", "目标记忆", confidence=0.5)
    bounds_env.commit()
    engine = NiyiEngine(session=bounds_env)

    out = engine.deliberate_proposal("update", {"confidence": "high"}, "nm-target")
    assert isinstance(out, dict), f"非数值 confidence 未按回执形态处理: {out!r}"
    assert out["confidence_delta"] == 0.0
    assert "非数值" in out["deliberation_reason"]
    assert "high" in out["deliberation_reason"], "回执应回显毒值原文以便上游排查"
    assert "推演终止" in out["deliberation_reason"]


def test_niyi_non_finite_confidence_rejected_not_coerced(bounds_env):
    """审查补臂(票 35 单一真源纪律):NaN/±inf 是「非数值」不是 0.0——修复实现
    曾手写 min(1.0, max(0.0, x)),对 NaN 得 0.0(max(0.0,nan)=0.0 实参顺序巧合),
    把毒值捏造成合法零置信;拒绝路径必须接管(代码审查轴确认的红:committed
    版本对 NaN 返回 0.0 且无回执)。"""
    from lantai.cognition.niyi import NiyiEngine

    _seed_memory(bounds_env, "nm-nan", "目标记忆", confidence=0.5)
    bounds_env.commit()
    engine = NiyiEngine(session=bounds_env)

    for poison in (float("nan"), float("inf"), "nan", "-inf"):
        out = engine.deliberate_proposal("update", {"confidence": poison}, "nm-nan")
        assert isinstance(out, dict), f"非有限值 {poison!r} 未按回执形态处理: {out!r}"
        assert out["confidence_delta"] == 0.0
        assert "非数值" in out["deliberation_reason"], (
            f"非有限值 {poison!r} 未走拒绝回执: {out['deliberation_reason']}"
        )
        assert "推演终止" in out["deliberation_reason"]


def test_niyi_confidence_clamped_into_unit_interval(bounds_env):
    """越界数值修复前直接进 conf_delta(1.5-0.5=1.0 的幽灵增量);修复后钳 [0,1]
    再算 delta——1.5→1.0、-0.2→0.0。"""
    from lantai.cognition.niyi import NiyiEngine

    _seed_memory(bounds_env, "nm-clamp", "目标记忆", confidence=0.5)
    bounds_env.commit()
    engine = NiyiEngine(session=bounds_env)

    hi = engine.deliberate_proposal("update", {"confidence": 1.5}, "nm-clamp")
    assert hi["confidence_delta"] == pytest.approx(0.5), hi
    lo = engine.deliberate_proposal("update", {"confidence": -0.2}, "nm-clamp")
    assert lo["confidence_delta"] == pytest.approx(-0.5), lo


def test_niyi_numeric_string_confidence_still_accepted(bounds_env):
    """回归锁(修复前即绿):数值字符串 "0.9" 一直可用,拒绝面只对非数值。"""
    from lantai.cognition.niyi import NiyiEngine

    _seed_memory(bounds_env, "nm-str", "目标记忆", confidence=0.5)
    bounds_env.commit()
    engine = NiyiEngine(session=bounds_env)

    out = engine.deliberate_proposal("update", {"confidence": "0.9"}, "nm-str")
    assert out["confidence_delta"] == pytest.approx(0.4)


# ── 位点 3:lifecycle falsy-or 吞 0.0 ─────────────────────────────────────────


def test_lifecycle_zero_confidence_still_weakened(bounds_env):
    """置信度 0.0 是合法值:修复前 `0.0 or 1.0`=1.0 逃逸 weaken(低置信记忆
    永不衰减);修复后显式 None 判定,0.0 < 0.5 正常 weaken。"""
    from lantai.cognition.lifecycle import KnowledgeLifecycleManager
    from lantai.models.tables import LifecycleStatus, MemoryItem

    _seed_memory(bounds_env, "lc-zero", "零置信记忆", confidence=0.0)
    bounds_env.commit()
    item = bounds_env.get(MemoryItem, "lc-zero")
    item.lifecycle_status = LifecycleStatus.ACTIVE

    mgr = KnowledgeLifecycleManager(bounds_env)
    weakened = mgr.scan_and_weaken([item])
    assert [w.id for w in weakened] == ["lc-zero"], "0.0 置信记忆逃逸 weaken(falsy-or 吞 0.0)"
    assert item.lifecycle_status == LifecycleStatus.WEAKENED


def test_lifecycle_zero_decay_score_still_retired(bounds_env):
    """衰减分 0.0(衰减到底)修复前 `0.0 or 1.0`=1.0 逃逸 retire;修复后正常退役。"""
    from lantai.cognition.lifecycle import KnowledgeLifecycleManager
    from lantai.models.tables import LifecycleStatus

    _seed_memory(bounds_env, "lc-decay", "衰减到底的记忆")
    bounds_env.commit()
    from lantai.models.tables import MemoryItem

    item = bounds_env.get(MemoryItem, "lc-decay")
    item.lifecycle_status = LifecycleStatus.SUPERSEDED
    item.decay_score = 0.0

    mgr = KnowledgeLifecycleManager(bounds_env)
    retired = mgr.scan_and_retire([item])
    assert [r.id for r in retired] == ["lc-decay"], "0.0 衰减分记忆逃逸 retire(falsy-or 吞 0.0)"
    assert item.lifecycle_status == LifecycleStatus.RETIRED


def test_lifecycle_none_confidence_keeps_default_semantics(bounds_env):
    """回归锁(修复前即绿):confidence 为 None(老库未记录)保持缺省 1.0 语义,
    不被误 weaken——显式 None 判定只收回 0.0 的合法值,不改 None 缺省。"""
    from lantai.cognition.lifecycle import KnowledgeLifecycleManager
    from lantai.models.tables import LifecycleStatus, MemoryItem

    _seed_memory(bounds_env, "lc-none", "未记录置信的记忆")
    bounds_env.commit()
    item = bounds_env.get(MemoryItem, "lc-none")
    item.lifecycle_status = LifecycleStatus.ACTIVE
    item.confidence = None

    mgr = KnowledgeLifecycleManager(bounds_env)
    weakened = mgr.scan_and_weaken([item])
    assert weakened == [], "None 置信(缺省 1.0 语义)被误 weaken"
    assert item.lifecycle_status == LifecycleStatus.ACTIVE


# ── 位点 4:zuozheng `confidence or 0.8` 抬升零置信证据 ────────────────────────


def test_zuozheng_zero_confidence_source_not_silently_lifted(bounds_env):
    """源记忆 confidence=0.0(合法零值)修复前被 `or 0.8` 抬成 0.8,再被
    min(edge=0.9, 0.8) 定为 0.8——零置信证据以近满权进入证据链;修复后
    显式 None 判定,0.0 保持 0.0。"""
    from lantai.cognition.zuozheng import ZuozhengEngine
    from lantai.models.tables import MemoryEdge

    _seed_memory(bounds_env, "zz-target", "目标主张")
    _seed_memory(bounds_env, "zz-src", "零置信支撑", confidence=0.0)
    bounds_env.add(
        MemoryEdge(
            id="zz-edge",
            source_memory_id="zz-src",
            target_memory_id="zz-target",
            relation="supports",
            confidence=0.9,
            reason="支撑边",
            user_id="default",
        )
    )
    bounds_env.commit()

    engine = ZuozhengEngine(session=bounds_env)
    chain = engine.inspect_evidence("zz-target")
    nodes = {n["id"]: n for n in chain["evidence_nodes"]}
    assert "zz-src" in nodes, f"支撑记忆未入证据链: {sorted(nodes)}"
    assert nodes["zz-src"]["confidence"] == 0.0, (
        f"零置信证据被抬升: {nodes['zz-src']['confidence']}(or 0.8 病灶)"
    )


# ── 位点 5:shishi 写面 admin 任意行 + ownerless 改写 ─────────────────────────


def _seed_cmb(
    s, cid, *, user_id, content, version=1, block="identity", namespace="default", tenant_id=None
):
    from lantai.models.tables import CoreMemoryBlock

    s.add(
        CoreMemoryBlock(
            id=cid,
            block=block,
            namespace=namespace,
            user_id=user_id,
            tenant_id=tenant_id,
            content=content,
            version=version,
            agent_id="sha256=stub:sealed",
            updated_at=datetime.now(UTC),
        )
    )


def test_shishi_nonadmin_seal_does_not_rewrite_ownerless_row(bounds_env):
    """ownerless 行(user_id IS NULL)修复前落在非 admin 写范围
    (`user_id == 我 | IS NULL`)内被直接改写——任意用户可篡改无主核心记忆;
    修复后写面只认自己的行,ownerless 行原样,另落自己名下新行(宁 miss 不脏写)。"""
    from lantai.cognition.shishi import ShishiEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import CoreMemoryBlock

    _seed_cmb(bounds_env, "cmb-ownerless", user_id=None, content="ownerless-v1", version=2)
    bounds_env.commit()

    principal = Principal(user_id="u1", tenant_id="t1")
    engine = ShishiEngine(session=bounds_env, principal=principal)
    engine.seal_core_memory("identity", "u1-content")

    bounds_env.expire_all()
    orphan = bounds_env.get(CoreMemoryBlock, "cmb-ownerless")
    assert orphan.content == "ownerless-v1", f"ownerless 行被非 admin 改写: {orphan.content}"
    assert orphan.version == 2, f"ownerless 行版本被推进: {orphan.version}"
    own = bounds_env.exec(select(CoreMemoryBlock).where(CoreMemoryBlock.user_id == "u1")).all()
    assert len(own) == 1 and own[0].content == "u1-content", "非 admin 未能落自己的行"


def test_shishi_admin_seal_targets_deterministic_row(bounds_env):
    """同 (block, namespace) 多用户行并存时,admin 封存 `.first()` 无 order_by
    → SQLite 实况 rowid 序 = 先插者(user-z)被任意命中;修复后按 user_id 确定性
    定靶:字典序最小的 user-a 行被更新,user-z 行原样。"""
    from lantai.cognition.shishi import ShishiEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import CoreMemoryBlock

    _seed_cmb(bounds_env, "cmb-z", user_id="user-z", content="z-v1")
    _seed_cmb(bounds_env, "cmb-a", user_id="user-a", content="a-v1")
    bounds_env.commit()

    admin = Principal(user_id="root", role="admin")
    engine = ShishiEngine(session=bounds_env, principal=admin)
    engine.seal_core_memory("identity", "admin-v2")

    bounds_env.expire_all()
    row_z = bounds_env.get(CoreMemoryBlock, "cmb-z")
    row_a = bounds_env.get(CoreMemoryBlock, "cmb-a")
    assert row_a.content == "admin-v2", f"user-a 行未被 admin 封存命中: {row_a.content}"
    assert row_a.version == 2
    assert row_z.content == "z-v1", f"user-z 行被无序 .first() 误命中改写: {row_z.content}"
    assert row_z.version == 1


def test_shishi_nonadmin_seal_does_not_cross_tenant(bounds_env):
    """写面 tenant 缺席半边(票 68 位点 5):同 user_id 在另一租户的行,修复前
    写查找只按 user_id 命中被直接改写(跨租户脏写);修复后非 admin 写面按
    _check_access 同款租户判据收窄(异租户行不进写范围,宁 miss 不脏写,
    落自己租户名下新行)。"""
    from lantai.cognition.shishi import ShishiEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import CoreMemoryBlock

    _seed_cmb(bounds_env, "cmb-cross", user_id="u1", tenant_id="t2", content="t2-v1")
    bounds_env.commit()

    principal = Principal(user_id="u1", tenant_id="t1")
    engine = ShishiEngine(session=bounds_env, principal=principal)
    engine.seal_core_memory("identity", "u1-t1-content")

    bounds_env.expire_all()
    cross = bounds_env.get(CoreMemoryBlock, "cmb-cross")
    assert cross.tenant_id == "t2"
    assert cross.content == "t2-v1", f"异租户行被跨租户改写: {cross.content}"
    assert cross.version == 1, f"异租户行版本被推进: {cross.version}"
    own = bounds_env.exec(
        select(CoreMemoryBlock).where(
            CoreMemoryBlock.user_id == "u1", CoreMemoryBlock.tenant_id == "t1"
        )
    ).all()
    assert len(own) == 1 and own[0].content == "u1-t1-content", "未落本租户名下新行"


# ── 位点 6:canshang OPPOSITE_PAIRS 裸子串 + 全文字符集假矛盾 ──────────────────


def _canshang_items(structure_a=None, content_a="", structure_b=None, content_b=""):
    from lantai.models.tables import MemoryItem

    now = datetime.now(UTC)
    item_a = MemoryItem(
        id="cs-a",
        content=content_a,
        structure=structure_a or {},
        user_id="u",
        tenant_id="t",
        status="active",
        created_at=now,
    )
    item_b = MemoryItem(
        id="cs-b",
        content=content_b,
        structure=structure_b or {},
        user_id="u",
        tenant_id="t",
        status="active",
        created_at=now,
    )
    return item_a, item_b


def test_canshang_unrelated_topics_no_false_contradiction():
    """交叉主题假矛盾:两段毫不相干的配置文本,修复前子串「启用」命中 + 全文字符
    集交叠 5 字 → direct_contradiction;修复后「不启用」的「启用」被否定语素
    拦下不作断言,互斥配对无从谈起。

    如实登记取舍:主题交叉闸保留原字符集口径——仓库唯一 multihop 真矛盾用例
    (test_audit_consistency_multihop_causal)两段文本分词后零交集,任何内容
    相似度闸在杀假阳性的同时必漏报该真矛盾;非否定形的跨题互斥词(如「已启用
    缓存」vs「关闭了缓存功能」)仍会按字符集交叠判矛盾,属保留宽容度的已知
    限制,不是本臂断言的语义。"""
    from lantai.cognition.canshang import CanshangEngine

    item_a, item_b = _canshang_items(
        content_a="系统配置中不启用此模式",
        content_b="该模式默认关闭,需手动开启配置",
    )
    engine = CanshangEngine()
    violation = engine.detect_pairwise_contradiction(item_a, item_b)
    assert violation is None, f"无关文本被误判矛盾: {violation}"


def test_canshang_negated_word_not_asserted_no_false_contradiction():
    """否定形假矛盾(票面「不升」含「升」家族):「不启用自动重试」与「已关闭自动
    重试」语义一致(都是关),修复前子串「启用」命中 + 字符集交叠 → 误判矛盾;
    修复后 jieba 分词 token 命中且紧邻前置否定语素的不作断言。"""
    from lantai.cognition.canshang import CanshangEngine

    item_a, item_b = _canshang_items(
        content_a="该服务当前不启用自动重试",
        content_b="该服务已关闭自动重试,属正常配置",
    )
    engine = CanshangEngine()
    violation = engine.detect_pairwise_contradiction(item_a, item_b)
    assert violation is None, f"同义否定表述被误判矛盾: {violation}"


def test_canshang_identical_negated_values_no_false_contradiction():
    """同值互判:两条 value 同为「不支持」的记忆,修复前 (支持,不支持) 裸子串
    双向命中 → 误判 attribute_conflict;修复后「不支持」中的「支持」被否定语素
    拦下,不再断言。"""
    from lantai.cognition.canshang import CanshangEngine

    item_a, item_b = _canshang_items(
        structure_a={"entity": "存储引擎", "value": "不支持"},
        structure_b={"entity": "存储引擎", "value": "不支持"},
    )
    engine = CanshangEngine()
    violation = engine.detect_pairwise_contradiction(item_a, item_b)
    assert violation is None, f"完全相同的取值被误判矛盾: {violation}"


def test_canshang_multi_char_negator_value_not_asserted():
    """审查补臂:取值面否定语素按串前缀匹配——「没有启用」的命中前一字是
    「有」,单字判定漏拦,与「关闭」仍误判矛盾;修复后多字否定语素(没有)
    结尾于命中处即不作断言。正文面 jieba 分词本就切出「没有」token,此臂只
    收取值面。"""
    from lantai.cognition.canshang import CanshangEngine

    item_a, item_b = _canshang_items(
        structure_a={"entity": "缓存", "value": "没有启用"},
        structure_b={"entity": "缓存", "value": "已关闭"},
    )
    engine = CanshangEngine()
    violation = engine.detect_pairwise_contradiction(item_a, item_b)
    assert violation is None, f"多字否定语素未拦下:「没有启用」被当作肯定「启用」: {violation}"


def test_canshang_genuine_contradiction_still_detected():
    """回归锁(修复前即绿):真矛盾——正文互斥(启用/关闭 + 同题 token 交叠 ≥3)、
    结构化取值互斥(on/off)两条路径都还报得出。"""
    from lantai.cognition.canshang import CanshangEngine

    item_a, item_b = _canshang_items(
        content_a="数据库自动清理任务已启用,保留七天",
        content_b="数据库自动清理任务已关闭,保留三十天",
    )
    engine = CanshangEngine()
    violation = engine.detect_pairwise_contradiction(item_a, item_b)
    assert violation is not None, "真矛盾(同题互斥正文)漏报"
    assert violation.violation_type == "direct_contradiction"

    item_c, item_d = _canshang_items(
        structure_a={"entity": "svc", "value": "on"},
        structure_b={"entity": "svc", "value": "off"},
    )
    structured = engine.detect_pairwise_contradiction(item_c, item_d)
    assert structured is not None, "结构化取值互斥(on/off)漏报"


# ── 位点 7:evolution dedup_key 无 DB 唯一兜底 ────────────────────────────────


def test_evolution_dedup_key_unique_enforced_at_db_level(bounds_env):
    """修复前 cognitive_patterns.dedup_key 只有普通索引——同键两行可直接落库
    (check-then-act 竞态双行的实锤入口);修复后 DB 唯一索引兜底,同键第二行
    IntegrityError。NULL 键不参与唯一(存量旧行语义不变)。"""
    from sqlalchemy.exc import IntegrityError

    from lantai.models.tables import CognitivePattern

    bounds_env.add(
        CognitivePattern(
            id="p1",
            pattern_type="recurrence",
            description="先落者",
            dedup_key="dupk",
        )
    )
    bounds_env.commit()
    bounds_env.add(
        CognitivePattern(
            id="p2",
            pattern_type="recurrence",
            description="并发后来者",
            dedup_key="dupk",
        )
    )
    with pytest.raises(IntegrityError):
        bounds_env.commit()
    bounds_env.rollback()

    # NULL 键不参与唯一性
    for pid in ("p-null-1", "p-null-2"):
        bounds_env.add(
            CognitivePattern(
                id=pid, pattern_type="recurrence", description="老库形态", dedup_key=None
            )
        )
    bounds_env.commit()
    from sqlmodel import select

    nulls = bounds_env.exec(
        select(CognitivePattern).where(CognitivePattern.dedup_key.is_(None))  # type: ignore[attr-defined]
    ).all()
    assert len(nulls) == 2, "NULL dedup_key 行被误判唯一冲突"


def test_evolution_detect_patterns_idempotent_regression(bounds_env):
    """回归锁(修复前即绿):同输入两轮 detect_patterns 只落一行(幂等去重主路径),
    唯一索引兜底不得改变正常路径语义。"""
    from lantai.cognition.evolution import EvolutionEngine
    from lantai.models.tables import CognitivePattern, MemoryItem

    experiences = [
        MemoryItem(id=f"exp-{i}", content=f"部署失败时回滚到上一版本 step {i}", status="active")
        for i in range(3)
    ]
    engine = EvolutionEngine(db=bounds_env)
    round1 = engine.detect_patterns(experiences)
    assert len(round1) >= 1
    round2 = engine.detect_patterns(experiences)
    assert round2 == [], "同输入第二轮仍产出新模式"
    rows = bounds_env.exec(select(CognitivePattern)).all()
    assert len(rows) == 1


# ── 位点 7 迁移:v42 去重 + 唯一索引 ──────────────────────────────────────────


def test_migration_dedupes_legacy_dup_rows_and_enforces_unique():
    """老库(迁移前形状)同 dedup_key 双行:修复前迁移链到 41 为止,双行原样;
    修复后 v42 先落者胜去重 + 建唯一索引,同键第二行被 DB 拒绝;NULL 键多行保留。"""
    from lantai.storage.migrations import apply_migrations

    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE cognitive_patterns ("
        "id TEXT PRIMARY KEY, pattern_type TEXT, description TEXT, dedup_key TEXT)"
    )
    con.execute("PRAGMA user_version = 41")
    for pid, key in [("p1", "dupk"), ("p2", "dupk"), ("p3", None), ("p4", None)]:
        con.execute(
            "INSERT INTO cognitive_patterns (id, pattern_type, description, dedup_key)"
            " VALUES (?, 'recurrence', 'legacy', ?)",
            (pid, key),
        )
    con.commit()

    apply_migrations(con)

    dup_rows = con.execute("SELECT id FROM cognitive_patterns WHERE dedup_key = 'dupk'").fetchall()
    assert [r[0] for r in dup_rows] == ["p1"], f"重复 dedup_key 未按先落者胜去重: {dup_rows}"

    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO cognitive_patterns (id, pattern_type, description, dedup_key)"
            " VALUES ('p5', 'recurrence', 'post-migration', 'dupk')"
        )

    null_rows = con.execute("SELECT id FROM cognitive_patterns WHERE dedup_key IS NULL").fetchall()
    assert len(null_rows) == 2, "NULL dedup_key 旧行被误去重"


# ── 位点 8:cehou 终态窗口收样本 + alerts 无界 ────────────────────────────────


def test_cehou_terminal_window_rejects_new_samples():
    """rolled_back(终态)窗口修复前仍接受样本(实验已裁决还继续改计数——
    证据被污染);修复后终态拒收,样本数冻结在终态时刻。"""
    from lantai.cognition.cehou import CehouEngine
    from lantai.core.auth import Principal

    tag = _uid()
    p = Principal(user_id=f"user-t68-{tag}", tenant_id=f"tenant-{tag}")
    engine = CehouEngine(principal=p)
    wid = f"win-terminal-{tag}"

    engine.create_observation_window(wid, "param", f"sug-{tag}", required_days=2, min_samples=10)
    for _ in range(6):
        engine.record_sample(wid, success=False)
    verdict = engine.inspect_window(wid, mock_days_observed=1)
    assert verdict["state"] == "rolled_back", f"前置熔断未生效: {verdict}"

    late = engine.record_sample(wid, success=True, latency_ms=1.0)
    assert late["success"] is False, f"终态窗口仍收样本: {late}"
    after = engine.inspect_window(wid)
    assert after["sample_count"] == 6, f"终态后样本数被推进: {after['sample_count']}"


def test_cehou_alert_list_capped():
    """失败率报警逐样本生成新串(比率每步都不同),修复前 alerts 随样本数无界
    增长(JSON 列膨胀 + 巡检报告风暴);修复后按上限截留(先到先留)。"""
    from lantai.cognition.cehou import CehouEngine
    from lantai.core.auth import Principal

    tag = _uid()
    p = Principal(user_id=f"user-t68-{tag}", tenant_id=f"tenant-{tag}")
    engine = CehouEngine(principal=p)
    wid = f"win-alerts-{tag}"

    engine.create_observation_window(wid, "param", f"sug-{tag}", required_days=30, min_samples=50)
    for i in range(1, 41):
        engine.record_sample(wid, success=(i % 6 == 0))

    report = engine.scan_active_windows(target_type="param")
    alerts = report["alerts"]
    assert isinstance(alerts, list)
    assert len(alerts) <= 20, f"报警列表无界增长: {len(alerts)} 条"


# ── 位点 9:zhaji 截断硬编码 + diben 全表加载 ─────────────────────────────────


def test_zhaji_persisted_content_respects_max_chars(bounds_env):
    """save_and_digest(max_chars=200) 修复前落库仍按硬编码 1000 截断(调用方
    指定的上限被无视);修复后落库内容尊重 max_chars。"""
    from lantai.cognition.zhaji import ZhajiEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import SessionScratchpad

    raw = "札" * 1500
    principal = Principal(user_id="u-zhaji", tenant_id="t-zhaji")
    engine = ZhajiEngine(session=bounds_env, principal=principal)
    engine.save_and_digest(session_id="sess-zhaji-t68", raw_text=raw, write=True, max_chars=200)

    row = bounds_env.get(SessionScratchpad, "sess-zhaji-t68")
    assert row is not None
    assert len(row.content) == 200, f"落库内容无视 max_chars: {len(row.content)}"


def test_diben_no_session_uses_acl_pushdown_not_full_table_scan():
    """无 session_id 修复前 `select(SessionCheckpoint)` 全表加载后 Python 侧逐行
    ACL(大库 O(全表));修复后属主/租户判据下推 SQL——用事件探针抓首条
    session_checkpoint SELECT,断言 WHERE 带 user_id 判据(不是 mock,是真执行
    语句的原文)。"""
    from lantai.cognition.diben import DibenEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import SessionCheckpoint

    sa_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(sa_engine)
    base = datetime.now(UTC)
    with Session(sa_engine) as s:
        for i in range(30):
            s.add(
                SessionCheckpoint(
                    session_id="sess-other",
                    block_key=f"cp_{i % 5}",
                    content="别人的现场",
                    user_id="user-other",
                    tenant_id="t1",
                    created_at=base + timedelta(minutes=i),
                )
            )
        for i in range(5):
            s.add(
                SessionCheckpoint(
                    session_id="sess-mine",
                    block_key=f"cp_{i}",
                    content="我的现场",
                    user_id="user-me",
                    tenant_id="t1",
                    created_at=base - timedelta(hours=1, minutes=i),
                )
            )
        s.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        stripped = statement.lstrip().upper()
        if "session_checkpoint" in statement.lower() and stripped.startswith("SELECT"):
            statements.append(statement)

    event.listen(sa_engine, "before_cursor_execute", _capture)
    try:
        with Session(sa_engine) as s:
            engine = DibenEngine(session=s, principal=Principal(user_id="user-me", tenant_id="t1"))
            anchor = engine.get_active_anchor()
            assert anchor is not None, "自己的现场定本未找到"
            assert anchor["session_id"] == "sess-mine"
    finally:
        event.remove(sa_engine, "before_cursor_execute", _capture)

    assert statements, "未捕获到 session_checkpoint 查询"
    first = statements[0]
    # SQLAlchemy 渲染的 SQL 带换行缩进（FROM …\nWHERE …），按单空格切会漏判——
    # 先折叠全部空白再找 WHERE（不弱化断言：仍要求真执行语句原文里有
    # 带 user_id 判据的 WHERE 子句）。
    normalized = " ".join(first.lower().split())
    where_part = normalized.split(" where ", 1)
    assert len(where_part) == 2, f"首条查询无 WHERE(仍全表加载): {first}"
    assert "user_id" in where_part[1], f"属主判据未下推进 WHERE: {first}"
    assert "limit" in normalized, f"定靶查询未加 limit(票 68 位点 9 limit 缺失半边): {first}"


def test_diben_pushdown_keeps_visibility_semantics():
    """回归锁(修复前即绿):下推不得改可见性——别人的 30 行更新仍不可见,
    自己的旧会话定本照常返回;按 session_id 精确读他人会话也拿不到。"""
    from lantai.cognition.diben import DibenEngine
    from lantai.core.auth import Principal
    from lantai.models.tables import SessionCheckpoint

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    base = datetime.now(UTC)
    with Session(engine) as s:
        for i in range(30):
            s.add(
                SessionCheckpoint(
                    session_id="sess-other",
                    block_key=f"cp_{i % 5}",
                    content="别人的现场",
                    user_id="user-other",
                    tenant_id="t1",
                    created_at=base + timedelta(minutes=i),
                )
            )
        for i in range(5):
            s.add(
                SessionCheckpoint(
                    session_id="sess-mine",
                    block_key=f"cp_{i}",
                    content="我的现场",
                    user_id="user-me",
                    tenant_id="t1",
                    created_at=base - timedelta(hours=1, minutes=i),
                )
            )
        s.commit()

    with Session(engine) as s:
        mine = DibenEngine(session=s, principal=Principal(user_id="user-me", tenant_id="t1"))
        anchor = mine.get_active_anchor()
        assert anchor is not None and anchor["session_id"] == "sess-mine"
        foreign = mine.get_active_anchor(session_id="sess-other")
        assert foreign is None, "按 session_id 读到了他人会话定本"

        admin = DibenEngine(session=s, principal=Principal(user_id="root", role="admin"))
        admin_view = admin.get_active_anchor()
        assert admin_view is not None and admin_view["session_id"] == "sess-other"
