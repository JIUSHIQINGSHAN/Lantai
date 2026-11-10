"""票 62 红测：认知读面 lifecycle/status 过滤缺失家族（八面病灶臂）。

口径（督导校准 + tables.py:13-31）：MemoryItem.status=管道态（active/candidate/
archived）与 lifecycle_status=认识论态（active/weakened/superseded/retired）正交；
supersede 只写 lifecycle_status=SUPERSEDED + superseded_by 列（lifecycle.py:73-75、
late_correction.py:91、zhiming.py:322），status 仍是 "active"。

正确读面范式：
- MemoryItem 读面 = status=="active" 且 lifecycle_status ∈ {active, weakened}
  （weakened 语义「仍参与检索但权重降低」放行；retired「等同于 archived」排除）；
- Evidence 读面 = status=="active"（对齐 zuozheng.py:148 同文件正确范式）。

纪律：真库直调（内存 SQLite + StaticPool + SQLModel.metadata.create_all），
superseded 行一律走真实写列路径（KnowledgeLifecycleManager.supersede），
不手搓字段，不 mock 被测链路。每臂自带 active 对照行（语义回归臂：正常主路径
零变化在红态即锁定）。

八面位点（亲读 HEAD=de9ff807）：
1. muci.build_catalog（L100 零过滤 / L126·L151 active 计数只看 status）
2. famen._collect_all_skills（L173-178 有 status 缺 lifecycle）
3. zuozheng（L132 target / L192 src_mem / L263 verify_claim 同病）
4. conflicts._evidence_stats（L91 Evidence 无 status，docstring 名实不符）
5. canshang（L350 arbitrate 双绕 / L295 多跳 target_node 双绕 / L233·236·258 缺 lifecycle）
6. gouchen.dredge_sunken_memories（L138 候选集含 retracted 笔削内容）
7. sanxing.evaluate_health（L160 死诊断：读 structure dict 键，全仓零写点）
8. cognition/evolution.propose_beliefs / propose_rules（L189/L247 Evidence 无 status）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.canshang import CanshangEngine
from lantai.cognition.conflicts import ConflictEngine
from lantai.cognition.evolution import EvolutionEngine
from lantai.cognition.famen import FamenEngine
from lantai.cognition.gouchen import GouchenEngine
from lantai.cognition.lifecycle import KnowledgeLifecycleManager
from lantai.cognition.muci import MuciEngine
from lantai.cognition.sanxing import SanxingEngine
from lantai.cognition.zuozheng import ZuozhengEngine
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import (
    CognitivePattern,
    Evidence,
    LifecycleStatus,
    MemoryEdge,
    MemoryItem,
)

WL = (LifecycleStatus.ACTIVE, LifecycleStatus.WEAKENED)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mem(
    s: Session,
    mid: str,
    content: str,
    *,
    status: str = "active",
    user_id: str | None = "user-1",
    tenant_id: str | None = "tenant-1",
    lane: str = "fact",
    domain: str = "user",
    confidence: float = 0.9,
    event_time: datetime | None = None,
    structure: dict | None = None,
    **extra,
) -> MemoryItem:
    item = MemoryItem(
        id=mid,
        content=content,
        status=status,
        user_id=user_id,
        tenant_id=tenant_id,
        lane=lane,
        domain=domain,
        confidence=confidence,
        event_time=event_time,
        structure=structure or {},
        created_at=utcnow() - timedelta(days=1),
        updated_at=utcnow(),
        **extra,
    )
    s.add(item)
    s.commit()
    return item


def _supersede(s: Session, old: MemoryItem, new_id: str) -> MemoryItem:
    """真实 supersede 写列路径（KnowledgeLifecycleManager.supersede，禁手搓字段）。"""
    return KnowledgeLifecycleManager(s).supersede(old, MemoryItem(id=new_id, content="新版"))


def _evidence(
    s: Session,
    eid: str,
    source_memory_id: str,
    *,
    reliability: float = 0.9,
    independence: float = 1.0,
    status: str = "active",
) -> Evidence:
    ev = Evidence(
        id=eid,
        evidence_type="observation",
        source_memory_id=source_memory_id,
        content=f"证据 {eid}",
        reliability=reliability,
        independence=independence,
        status=status,
        user_id="user-1",
        created_at=utcnow(),
    )
    s.add(ev)
    s.commit()
    return ev


# ── 臂 1：目次（muci）——四态混入目录 + superseded 计入活跃 ──────────────


def test_arm1_muci_catalog_excludes_archived_retracted_superseded(db_session):
    _mem(db_session, "mem-ok", "正常活跃记忆的正文", domain="user", lane="fact")
    _mem(
        db_session, "mem-arch", "已归档记忆的正文", status="archived", domain="user", lane="fact"
    )
    _mem(
        db_session,
        "mem-retr",
        "已撤回记忆的正文",
        status="retracted",
        domain="user",
        lane="fact",
    )
    sup = _mem(db_session, "mem-sup", "已被取代记忆的正文", domain="user", lane="fact")
    _supersede(db_session, sup, "mem-sup-new")

    engine = MuciEngine(session=db_session, principal=Principal(user_id="user-1", tenant_id="t1"))
    cat = engine.build_catalog(detail_level="full", max_entries_per_section=10)

    # 红病灶 1a：archived/retracted/superseded 全文混入目录
    assert cat["total_memories"] == 1, (
        f"目录该只收白名单态（active+active/weakened），实际 {cat['total_memories']} 条"
    )
    all_entries = " ".join(e["content"] for sec in cat["sections"] for e in sec["entries"])
    for bad in ("已归档记忆的正文", "已撤回记忆的正文", "已被取代记忆的正文"):
        assert bad not in all_entries, f"非生效态正文混进目次：{bad}"
    # 对照（语义回归）：正常 active 行主路径零变化
    assert "正常活跃记忆的正文" in all_entries

    # 红病灶 1b：superseded 行计入活跃计数
    assert cat["active_memories"] == 1, (
        f"活跃计数该只算认识论活跃行，实际 {cat['active_memories']}"
    )
    sec = next(s for s in cat["sections"] if s["domain"] == "user")
    assert sec["active_count"] == 1, f"板块活跃计数混入 superseded，实际 {sec['active_count']}"


# ── 臂 2：法门（famen）——status=active 但 lifecycle=superseded 的行混入技能集 ──


def test_arm2_famen_procedural_superseded_excluded(db_session):
    ok = _mem(
        db_session,
        "mem-skill-ok",
        "标准部署 SOP 流程正文",
        lane="rule",
        decay_class="procedural",
        structure={"name": "deploy-sop", "steps": ["备份", "发布"]},
    )
    old = _mem(
        db_session,
        "mem-skill-old",
        "已废除的旧版部署 SOP 流程正文",
        lane="rule",
        decay_class="procedural",
        structure={"name": "deploy-sop-old", "steps": ["旧步骤"]},
    )
    _supersede(db_session, old, "mem-skill-new")
    del ok

    engine = FamenEngine(session=db_session, principal=Principal(user_id="user-1"))
    skills = engine.list_skills(limit=50)
    ids = {a["id"] for a in skills}

    # 红病灶：只补 status 不补 lifecycle 的绿实现在这条臂上必须保持红
    assert "mem-skill-old" not in ids, "superseded（status 仍 active）技能混入法门列表"
    # 对照（语义回归）：active 技能照常列出
    assert "mem-skill-ok" in ids, "正常技能被误伤"

    detail = engine.get_skill_detail("mem-skill-old")
    assert detail is None, "superseded 技能经 get_skill_detail 仍可直取"


# ── 臂 3：佐证（zuozheng）——target/src_mem/verify_claim 三处 lifecycle 漏 ──


def test_arm3a_zuozheng_superseded_target_rejected(db_session):
    sup = _mem(db_session, "mem-sup-target", "已被取代的旧主张正文")
    _supersede(db_session, sup, "mem-sup-target-new")

    engine = ZuozhengEngine(session=db_session, principal=Principal(user_id="user-1"))
    chain = engine.inspect_evidence("mem-sup-target")

    assert chain["target_memory_id"] is None, (
        "superseded 行（status 仍 active）作为佐证目标被放行"
    )


def test_arm3b_zuozheng_superseded_neighbor_excluded_from_chain(db_session):
    ok = _mem(db_session, "mem-zz-ok", "现行主张正文")
    old = _mem(db_session, "mem-zz-old", "已取代旧主张正文")
    _supersede(db_session, old, "mem-zz-old-new")
    db_session.add(
        MemoryEdge(
            id="edge-zz-1",
            source_memory_id="mem-zz-old",
            target_memory_id="mem-zz-ok",
            relation="supports",
            confidence=0.9,
        )
    )
    db_session.commit()

    engine = ZuozhengEngine(session=db_session, principal=Principal(user_id="user-1"))
    chain = engine.inspect_evidence("mem-zz-ok", max_depth=1)
    node_ids = {n["id"] for n in chain["evidence_nodes"]}

    assert "mem-zz-old" not in node_ids, "superseded 邻居（status 仍 active）参与证据链扩散"
    assert "mem-zz-ok" in node_ids or chain["target_memory_id"] == "mem-zz-ok"  # 对照主路径


def test_arm3c_zuozheng_verify_claim_ignores_superseded(db_session):
    ok = _mem(db_session, "mem-claim-ok", "系统已全面启用 TLS 加密传输")
    old = _mem(db_session, "mem-claim-old", "旧版系统已全面启用 TLS 加密传输的过期配置")
    _supersede(db_session, old, "mem-claim-old-new")
    del ok

    engine = ZuozhengEngine(session=db_session, principal=Principal(user_id="user-1"))
    res = engine.verify_claim("系统 TLS 加密传输配置")
    node_ids = {n["id"] for n in res["supporting_nodes"]}

    assert "mem-claim-old" not in node_ids, "superseded 行（status 仍 active）参与断言佐证"


# ── 臂 4：冲突（conflicts）——_evidence_stats 混入非 active Evidence ────────


def test_arm4_conflict_evidence_stats_only_active_rows(db_session):
    a = _mem(db_session, "mem-conf-a", "生产服务器已全面启用 TLS 加密", confidence=0.5)
    b = _mem(db_session, "mem-conf-b", "生产服务器目前关闭 TLS 加密", confidence=0.5)
    _evidence(db_session, "ev-a1", "mem-conf-a", reliability=0.9, independence=1.0)
    _evidence(
        db_session, "ev-a2", "mem-conf-a", reliability=0.1, independence=0.0, status="archived"
    )

    result = ConflictEngine().resolve(a, b, session=db_session)
    comps_a = result.decision_trace.score_components_a

    assert comps_a["evidence_strength"] == pytest.approx(0.9), (
        f"evidence_strength 混入非 active Evidence 行（archived 拉低），"
        f"实际 {comps_a['evidence_strength']}"
    )
    assert comps_a["independence"] == pytest.approx(1.0), (
        f"independence 混入非 active 行，实际 {comps_a['independence']}"
    )


# ── 臂 5：参商（canshang）——仲裁双绕 / 多跳双绕 / audit 缺 lifecycle ───────


def test_arm5a_canshang_arbitrate_rejects_retracted_side(db_session, caplog):
    a = _mem(
        db_session,
        "side-active",
        "生产服务器已全面启用 TLS 1.3 强加密传输",
        confidence=0.85,
        event_time=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    )
    b = _mem(
        db_session,
        "side-retracted",
        "生产服务器目前完全关闭 TLS 加密使用明文通信",
        status="retracted",
        confidence=0.99,
        event_time=datetime(2026, 10, 2, 10, 0, tzinfo=UTC),
    )

    with caplog.at_level(logging.INFO, logger="lantai"):
        result = CanshangEngine(
            session=db_session, principal=Principal(user_id="user-1")
        ).arbitrate_deep_conflict(["side-active", "side-retracted"])

    # 红病灶：retracted 侧参与仲裁且胜出（confidence 0.99 + 更晚事件轴）
    assert result.dominant_memory_id == "side-active", (
        f"retracted 侧被拒绝后只剩 active 行自然生效，实际 dominant={result.dominant_memory_id}"
    )
    # 拒绝留日志（宁 miss 不脏写）
    assert any("side-retracted" in r.message or "retracted" in r.getMessage() for r in caplog.records), (
        "仲裁跳过非生效记忆未留日志"
    )


def test_arm5b_canshang_multihop_skips_retracted_target(db_session):
    # 种子设计：唯一矛盾路径必须经 retracted target_node（mh-pre 与 mh-other
    # 无反义词交叉，mh-target 与 mh-other 撞 _STATE_ANTONYM_PAIRS 单机/集群
    # 同现即判）——绿态 retracted 跳过后 violation_count 才能干净归零。
    pre = _mem(db_session, "mh-pre", "执行命令升级生产数据库至最新版本")
    _mem(db_session, "mh-other", "当前线上仅有单机独立数据库运行且无从节点")
    _mem(db_session, "mh-target", "系统处于多主集群高可用状态", status="retracted")
    db_session.add(
        MemoryEdge(
            id="edge-mh-1",
            source_memory_id="mh-pre",
            target_memory_id="mh-target",
            relation="causes",
            confidence=0.95,
        )
    )
    db_session.commit()

    report = CanshangEngine(
        session=db_session, principal=Principal(user_id="user-1")
    ).audit_consistency(memory_ids=["mh-pre", "mh-other"])

    assert report.violation_count == 0, (
        f"retracted target_node 参与多跳因果推演：{[v.description for v in report.violations]}"
    )


def test_arm5c_canshang_audit_scans_only_whitelisted_lifecycle(db_session):
    _mem(db_session, "aud-sup", "生产服务器已全面启用 TLS 加密传输")
    sup = db_session.get(MemoryItem, "aud-sup")
    _supersede(db_session, sup, "aud-sup-new")
    _mem(db_session, "aud-opp", "生产服务器目前完全关闭 TLS 加密使用明文通信")

    report = CanshangEngine(
        session=db_session, principal=Principal(user_id="user-1")
    ).audit_consistency(memory_ids=["aud-sup", "aud-opp"])

    assert report.total_scanned == 1, (
        f"superseded 行（status 仍 active）混入审计扫描集，实际 scanned {report.total_scanned}"
    )
    assert report.violation_count == 0, "superseded 行参与两两比对产出矛盾违例"


# ── 臂 6：钩沉（gouchen）——retracted 笔削内容混入沉埋打捞 ────────────────


def test_arm6_gouchen_dredge_excludes_retracted(db_session):
    # 对照（语义回归）：archived 沉埋行照常打捞
    _mem(
        db_session,
        "gc-archived",
        "早期遗留的已归档架构文档",
        status="archived",
        confidence=0.5,
        structure=None,
    )
    arch = db_session.get(MemoryItem, "gc-archived")
    arch.decay_score = 0.05
    arch.created_at = utcnow() - timedelta(days=90)
    db_session.add(arch)
    db_session.commit()

    # 红病灶：retracted 行（笔削：主张错误即全检索面禁用）sunken_score 高照入队
    retr = _mem(
        db_session, "gc-retracted", "已被裁定错误的机密档案", status="retracted", confidence=0.5
    )
    retr.decay_score = 0.05
    retr.created_at = utcnow() - timedelta(days=90)
    db_session.add(retr)
    db_session.commit()

    engine = GouchenEngine(session=db_session, principal=Principal(user_id="user-1"))
    res = engine.dredge_sunken_memories(query="档案", min_sunken_score=0.4, session=db_session)
    hit_ids = [it.memory_id for it in res.items]

    assert "gc-retracted" not in hit_ids, "retracted 笔削内容混入沉埋打捞队列"
    assert "gc-archived" in hit_ids, "archived 沉埋行被误伤（对照组回归）"


# ── 臂 7：三省（sanxing）——死诊断改读列 ────────────────────────────────


def test_arm7_sanxing_superseded_stale_reads_column(db_session):
    old = _mem(db_session, "sx-old", "老版认证接口规范")
    _supersede(db_session, old, "sx-old-new")

    snapshot = SanxingEngine(session=db_session).evaluate_health()

    # 红病灶：structure["superseded_by"] 全仓零写点 → 诊断恒假（死代码）
    assert len(snapshot.superseded_stale) == 1, (
        f"真实 supersede（写列）后 superseded_stale 诊断应命中 1 条，实际 "
        f"{len(snapshot.superseded_stale)}——死键读点未修"
    )
    stale = snapshot.superseded_stale[0]
    assert stale["id"] == "sx-old"
    assert stale["superseded_by"] == "sx-old-new", (
        f"诊断 superseded_by 该取列值，实际 {stale['superseded_by']!r}"
    )
    assert snapshot.health_score == pytest.approx(95.0), (
        f"诊断命中后健康分应扣 5 分，实际 {snapshot.health_score}"
    )


# ── 臂 8：演化（evolution）——Evidence 评分混入非 active 行 ───────────────


def _pattern(s: Session, pid: str, source_mid: str) -> CognitivePattern:
    now = utcnow()
    pat = CognitivePattern(
        id=pid,
        pattern_type="recurrence",
        description="发布前必须跑全量测试",
        source_ids=[source_mid],
        occurrence_count=3,
        independent_source_count=1,
        confidence=0.7,
        status="candidate",
        created_at=now,
        updated_at=now,
    )
    s.add(pat)
    s.commit()
    return pat


def test_arm8a_evolution_propose_beliefs_ignores_inactive_evidence(db_session):
    _mem(db_session, "mem-ev", "发布前必须跑全量测试")
    _evidence(db_session, "ev-strong", "mem-ev", reliability=0.9, independence=1.0)
    _evidence(
        db_session, "ev-dead", "mem-ev", reliability=0.0, independence=0.0, status="archived"
    )
    pat = _pattern(db_session, "pat-62a", "mem-ev")

    beliefs = EvolutionEngine(db=db_session).propose_beliefs([pat])

    # 红病灶可见后果：archived 证据把 evidence_quality 拖到 0.45，
    # 晋升分 < 0.70，候选被吞（期望：纯 active 证据 eq=0.9 → 分 0.755 晋升）。
    assert len(beliefs) == 1, (
        f"active 证据支撑的 pattern 晋升候选被非 active 行拖垮：产出 {len(beliefs)} 条"
    )
    comps = beliefs[0].promotion_trace["components"]
    assert comps["evidence_quality"] == pytest.approx(0.9), (
        f"evidence_quality 混入非 active Evidence，实际 {comps['evidence_quality']}"
    )


def test_arm8b_evolution_propose_rules_ignores_inactive_evidence(db_session):
    _mem(db_session, "mem-ev-rule", "发布前必须跑全量测试")
    _evidence(db_session, "ev-strong-r", "mem-ev-rule", reliability=0.9, independence=1.0)
    _evidence(
        db_session,
        "ev-dead-r",
        "mem-ev-rule",
        reliability=0.0,
        independence=0.0,
        status="archived",
    )
    now = utcnow()
    belief = MemoryItem(
        id="mem-bel-62",
        content="发布前必须跑全量测试",
        confidence=0.9,
        status="active",
        source_ids=["mem-ev-rule"],
        use_count=5,
        helpful_count=5,
        created_at=now,
        updated_at=now,
    )
    db_session.add(belief)
    db_session.commit()

    rules = EvolutionEngine(db=db_session).propose_rules([belief])

    assert len(rules) == 1, (
        f"active 证据支撑的 rule 晋升被非 active 行拖垮：产出 {len(rules)} 条"
    )
    comps = rules[0].promotion_trace["components"]
    assert comps["evidence_quality"] == pytest.approx(0.9), (
        f"rule 晋升 evidence_quality 混入非 active Evidence，实际 {comps['evidence_quality']}"
    )
