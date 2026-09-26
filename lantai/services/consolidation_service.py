"""沉潜（ADR-0036）：闲时夜梦沉淀与记忆折叠压缩服务。

功能：
1. find_consolidation_clusters: 发现同域/同分轨下高重合度的碎片记忆集；
2. consolidate_cluster: 概念提纯并生成主记忆，折叠碎片记忆（status="consolidated"）；
3. prune_decayed_synapses: 自动修剪极度衰减的边缘碎片（status="archived"）；
4. run_consolidation_cycle: 调度执行完整沉潜周期。

沉潜过审（ADR-0050）：巩固产物生效通道随 CONSOLIDATION_AUDIT_MODE 三模式分流——
off（默认）直写行为逐字节不变；shadow 直写照旧另落影子提案（对照留痕，结构性
不可裁决/不可应用）；enforce 只产 pending consolidation 提案（不落主记忆不折叠），
裁决 apply 后才生效（promoter.apply_proposal consolidation 分支）。非法模式值
fail-loud：拒绝执行本周期巩固并 ERROR 留痕——宁巩固停摆，不静默直写。
"""

import contextlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import jieba.analyse
from sqlmodel import Session, select
from ulid import ULID

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.llm.client import chat_json
from lantai.models.enums import ProposalStatus
from lantai.models.tables import (
    ConsolidationRun,
    MemoryCheckpoint,
    MemoryItem,
    MemoryProposal,
)
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage import db

_LAST_CONSOLIDATION_REPORT: dict = {
    "last_run": None,
    "consolidated_groups": 0,
    "new_memories": 0,
    "pruned_count": 0,
    "status": "idle",
    # 增量键（ADR-0050 决策 3 报告契约）：初值须与运行后 report 键集对齐——
    # REST/MCP 在首次巩固运行前直接透传本 dict，缺键即 KeyError。
    "mode": "off",
    "proposals_created": 0,
    "skipped_dupes": 0,
    "skipped_rejected_cooldown": 0,
    "skipped_lowq": 0,
    "error": "",
}

_CONSOLIDATION_AUDIT_MODES = ("off", "shadow", "enforce")


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite DATETIME 读回 naive，统一按 UTC 升维（范式同 reflector._as_utc）。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _resolve_audit_mode() -> str | None:
    """读取并校验 CONSOLIDATION_AUDIT_MODE；非法值返回 None（调用方拒绝执行，宁停摆不静默直写）。

    非法值 fail-loud 不静默回落 off（ADR-0050 决策 1）：off 恰是本 ADR 要消灭的
    静默直写面——非法值静默回落 off 等于一个尾随空格就让系统无感回到脏写。
    故不做 strip/lower 归一，精确匹配三值。
    """
    mode = settings.CONSOLIDATION_AUDIT_MODE
    if mode not in _CONSOLIDATION_AUDIT_MODES:
        logger.error(
            "沉潜过审：CONSOLIDATION_AUDIT_MODE 非法值 %r（合法 off/shadow/enforce）"
            "——拒绝执行巩固（宁停摆不静默直写）",
            mode,
        )
        return None
    return mode


def _master_patch(
    content: str, importance: float, confidence: float, dom: str, lane: str, source_ids: list[str]
) -> dict:
    """主记忆构造全集（ADR-0050 决策 3）：现状主记忆构造平移入 proposed_patch，
    apply 侧照此构造不重推断（decay_class 恒 semantic；四元组不设——行为零漂移）。"""
    return {
        "content": content,
        "importance": importance,
        "confidence": confidence,
        "domain": dom,
        "lane": lane,
        "source_ids": list(source_ids),
        "decay_class": "semantic",
    }


def _trustmem_reason(cluster_size: int) -> str:
    """TrustMem 校验结论（提案 reason 字段）。校验属生成时刻语义，apply 不重跑。"""
    return f"TrustMem 迁移校验通过：{cluster_size} 条碎片提纯为 1 条主记忆（长度合规 + 共识关键词覆盖）"


def _proposal_quadruple(cluster_items: list[MemoryItem]) -> dict:
    """提案四元组取首碎片（ADR-0050 决策 3）：聚类仅按 (domain, lane) 分组，
    同簇未必同租户，以首碎片为准。"""
    first = cluster_items[0]
    return {
        "tenant_id": getattr(first, "tenant_id", None),
        "user_id": getattr(first, "user_id", None),
        "agent_id": getattr(first, "agent_id", None),
        "session_id": getattr(first, "session_id", None),
    }


def find_consolidation_clusters(
    session: Session, min_cluster_size: int | None = None
) -> list[list[MemoryItem]]:
    """扫描活跃记忆，按 domain/lane 与主题聚类出可折叠的碎片记忆集。

    min_cluster_size 缺省取 settings.CONSOLIDATION_MIN_CLUSTER_SIZE（ADR-0002 零硬编码）。
    """
    if min_cluster_size is None:
        min_cluster_size = settings.CONSOLIDATION_MIN_CLUSTER_SIZE
    """扫描活跃记忆，按 domain/lane 与主题聚类出可折叠的碎片记忆集。"""
    active_items = session.exec(select(MemoryItem).where(MemoryItem.status == "active")).all()

    # 1. 按 (domain, lane) 分组
    group_map = defaultdict(list)
    for m in active_items:
        # 跳过已是聚合主记忆（带有多 source_ids）的项，避免无限递归折叠
        # 阈值取 settings（ADR-0002 零硬编码）
        if len(m.source_ids or []) >= settings.CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES:
            continue
        dom = getattr(m, "domain", "user")
        group_map[(dom, m.lane)].append(m)

    clusters: list[list[MemoryItem]] = []

    # 2. 组内主题关键词聚类
    for (dom, _lane), items in group_map.items():
        if len(items) < min_cluster_size:
            continue

        keyword_item_map = defaultdict(list)
        for item in items:
            # 提取 2~4 个核心关键词
            tags = jieba.analyse.extract_tags(item.content, topK=4)
            for tag in tags:
                if len(tag.strip()) >= 2:
                    keyword_item_map[tag.strip()].append(item)

        # 找出命中同一关键词且数量 >= min_cluster_size 的子集
        clustered_ids = set()
        for _kw, cand_items in keyword_item_map.items():
            unique_items = {i.id: i for i in cand_items if i.id not in clustered_ids}
            if len(unique_items) >= min_cluster_size:
                cluster = list(unique_items.values())
                clusters.append(cluster)
                for i in cluster:
                    clustered_ids.add(i.id)

    logger.info("沉潜：聚类扫描发现 %d 组可折叠碎片集", len(clusters))
    return clusters


def _verify_trustmem_transition(cluster_items: list[MemoryItem], content: str) -> bool:
    """可信记忆迁移校验器（TrustMem Verifier，借鉴 arXiv:2606.25161）。

    校验：
    1. 长度与基础非空校验；
    2. 核心共识关键词覆盖校验（防止关键实体遗漏与幻觉丢失）；
    3. 异常截断防护。
    """
    clean_c = content.strip()
    if not (5 <= len(clean_c) <= 2000):
        logger.warning("沉潜 TrustMem 校验失败：提纯长度不合规 (%d 字符)", len(clean_c))
        return False

    kw_freq = defaultdict(int)
    for m in cluster_items:
        tags = jieba.analyse.extract_tags(m.content, topK=3)
        for t in tags:
            if len(t.strip()) >= 2:
                kw_freq[t.strip()] += 1

    # 找出在多数碎片（>=2）中都出现的共识关键词
    consensus_kws = [k for k, count in kw_freq.items() if count >= 2]
    if consensus_kws:
        has_match = any(kw in clean_c for kw in consensus_kws)
        if not has_match:
            logger.warning("沉潜 TrustMem 校验失败：提纯内容丢失共识关键词 %s", consensus_kws)
            return False
    return True


def _propose_consolidation(
    s: Session,
    cluster_items: list[MemoryItem],
    content: str,
    importance: float,
    confidence: float,
    dom: str,
    lane: str,
    source_ids: list[str],
) -> MemoryProposal:
    """enforce 生成侧（ADR-0050 决策 3）：落恰一条 pending consolidation 提案。

    不落主记忆、不折叠、不写索引、不写 checkpoint——生成时刻零变更，提案行
    （proposed_patch/reason/evidence_ids）即生成留痕；裁决 apply 后才生效。
    LLM 失败/输出无效/TrustMem 不过不会走到这里（维持「保持原库不变」）。
    """
    prop = MemoryProposal(
        id=new_id("prop"),
        proposal_type="consolidation",
        evidence_ids=list(source_ids),
        reason=_trustmem_reason(len(cluster_items)),
        proposed_patch=_master_patch(content, importance, confidence, dom, lane, source_ids),
        confidence=confidence,
        status=ProposalStatus.PENDING,
        decided_by="consolidation",
        **_proposal_quadruple(cluster_items),
        provenance={
            "mode": "enforce",
            "cluster_size": len(cluster_items),
            "quadruple_from": "first_fragment",
        },
    )
    s.add(prop)
    s.commit()
    logger.info(
        "沉潜过审：%d 条碎片提纯 → pending consolidation 提案 %s（只产提案，不代裁决）",
        len(cluster_items),
        prop.id,
    )
    return prop


def consolidate_cluster(
    cluster_items: list[MemoryItem], session: Session | None = None
) -> MemoryItem | MemoryProposal | None:
    """对一组碎片记忆进行概念提纯与合成（ADR-0050 三模式分流）。

    返回「本次产物载体」：off/shadow 返回主记忆 MemoryItem（直写路径）、
    enforce 返回 MemoryProposal（pending 提案）、被过滤/失败返回 None——
    调用方据此分桶计数，杜绝 enforce 期被 `res is not None` 误读为新落主记忆。
    """
    if not cluster_items:
        return None

    def _execute(s: Session) -> MemoryItem | MemoryProposal | None:
        mode = _resolve_audit_mode()
        if mode is None:
            return None

        sources_text = "\n".join(f"- [ID: {m.id}] {m.content}" for m in cluster_items)
        sys_prompt = (
            "你是一个专业的认知记忆综合提纯专家。请将以下多条碎片化的日常记忆/偏好/事实，"
            "提纯归纳为 1 条高阶概括性、准确且简练的主记忆。\n"
            "要求：\n"
            "1. 保留关键实体、偏好参数、事实细节，去除重复冗余与口语口水话；\n"
            "2. 返回 JSON 格式：\n"
            "{\n"
            '  "consolidated_content": "提纯后的一句话主记忆内容",\n'
            '  "importance": 0.8,\n'
            '  "confidence": 0.95\n'
            "}"
        )
        user_prompt = f"待提纯碎片记忆列表：\n{sources_text}"

        try:
            res = chat_json(system=sys_prompt, user=user_prompt)
        except Exception as exc:
            logger.warning("沉潜：LLM 提纯失败，保持原库不变（宁 miss 不脏写）: %s", exc)
            return None

        if not isinstance(res, dict) or not res.get("consolidated_content"):
            logger.warning("沉潜：LLM 提纯未返回有效内容，跳过折叠")
            return None

        content = str(res["consolidated_content"]).strip()
        if not _verify_trustmem_transition(cluster_items, content):
            logger.warning("沉潜：TrustMem 迁移校验不通过，放弃折叠以防脏写")
            return None

        importance = float(res.get("importance", 0.8))
        confidence = float(res.get("confidence", 0.9))

        dom = getattr(cluster_items[0], "domain", "user")
        lane = cluster_items[0].lane
        source_ids = [m.id for m in cluster_items]

        if mode == "enforce":
            return _propose_consolidation(
                s, cluster_items, content, importance, confidence, dom, lane, source_ids
            )

        # off/shadow：直写路径（off 与现状逐字节一致；shadow 增影子提案与 checkpoint 关联留痕）
        master_id = f"mem_{ULID()}"
        shadow_prop = None
        if mode == "shadow":
            # 影子提案（ADR-0050 决策 4）：恰一条 ProposalStatus.SHADOW 纯留痕行，
            # evidence_ids/proposed_patch/reason 与 enforce 同构——「若走提案制会产出什么」
            # 的对照样本；decided_by="shadow"，结构性不可裁决/不可应用（双门禁天然拦截）。
            shadow_prop = MemoryProposal(
                id=new_id("prop"),
                proposal_type="consolidation",
                evidence_ids=list(source_ids),
                reason=_trustmem_reason(len(cluster_items)),
                proposed_patch=_master_patch(
                    content, importance, confidence, dom, lane, source_ids
                ),
                confidence=confidence,
                status=ProposalStatus.SHADOW,
                decided_by="shadow",
                **_proposal_quadruple(cluster_items),
                provenance={
                    "mode": "shadow",
                    "master_id": master_id,
                    "sources": list(source_ids),
                },
            )
            s.add(shadow_prop)

        # 1. 记录 Checkpoint 快照（off：伪 id、proposal_id=None 现状不变；
        #    shadow：proposal_id=影子提案 id，生成留痕↔产物对账键）
        cp_kwargs: dict = {}
        if shadow_prop is not None:
            cp_kwargs["proposal_id"] = shadow_prop.id
        cp = MemoryCheckpoint(
            id=f"cp_{ULID()}",
            memory_id="cluster_consolidation",
            version=1,
            before={"source_ids": source_ids},
            after={"content": content, "domain": dom, "lane": lane},
            trigger="consolidation",
            created_at=utcnow(),
            **cp_kwargs,
        )
        s.add(cp)

        # 2. 创建提纯后的主记忆
        master = MemoryItem(
            id=master_id,
            content=content,
            domain=dom,
            lane=lane,
            source_ids=source_ids,
            confidence=confidence,
            importance=importance,
            decay_score=1.0,
            decay_class="semantic",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        s.add(master)

        # 3. 将原碎片记忆标记为 consolidated 折叠状态
        for m in cluster_items:
            m.status = "consolidated"
            m.updated_at = utcnow()
            s.add(m)

        s.commit()
        s.refresh(master)

        # 4. 同步更新向量库与 FTS 索引
        try:
            from lantai.llm.client import embed

            embeddings = embed([master.content])
            if embeddings:
                index_memory_item(
                    master.id,
                    embeddings[0],
                    {
                        "lane": getattr(master, "lane", "general") or "general",
                        "domain": getattr(master, "domain", "user") or "user",
                        "tenant_id": getattr(master, "tenant_id", "") or "",
                        "user_id": getattr(master, "user_id", "") or "",
                        "session_id": getattr(master, "session_id", "") or "",
                        "agent_id": getattr(master, "agent_id", "") or "",
                    },
                )
        except Exception as exc:
            logger.warning("沉潜：主记忆向量索引同步异常（已落库）: %s", exc)

        logger.info(
            "沉潜：成功将 %d 条碎片折叠为主记忆 %s: %s",
            len(cluster_items),
            master.id,
            content[:30],
        )
        return master

    if session is not None:
        return _execute(session)
    with db.get_session() as s:
        return _execute(s)


def prune_decayed_synapses(threshold: float | None = None, session: Session | None = None) -> int:
    """自动修剪极度衰减的边缘碎片（转为 archived 休眠）。

    threshold 缺省取 settings.CONSOLIDATION_PRUNE_THRESHOLD（ADR-0002 零硬编码）。
    """
    if threshold is None:
        threshold = settings.CONSOLIDATION_PRUNE_THRESHOLD

    def _prune(s: Session) -> int:
        decayed_items = s.exec(
            select(MemoryItem)
            .where(MemoryItem.status == "active")
            .where(MemoryItem.decay_score < threshold)
            .where(MemoryItem.helpful_count == 0)
        ).all()

        pruned = 0
        for item in decayed_items:
            item.status = "archived"
            item.updated_at = utcnow()
            s.add(item)
            pruned += 1

        if pruned > 0:
            s.commit()
            logger.info("沉潜：成功修剪 %d 条极度衰减的边缘碎片记忆", pruned)
        return pruned

    if session is not None:
        return _prune(session)
    with db.get_session() as s:
        return _prune(s)


def _consolidation_proposal_blocked(s: Session, evidence_ids: list[str]) -> str | None:
    """enforce 生成侧拦截判定（ADR-0050 决策 3）：返回拦截桶名，未拦截返回 None。

    - "dupes"：同 evidence_ids 冻结集已存在 pending 的 consolidation 提案（幂等去重：
      enforce 期碎片在 pending 期间保持 active，会被下一轮再次聚出，不去重则每夜
      重复生成同簇提案直至裁决）；
    - "cooldown"：存在冷却期内 rejected 的同簇提案（拒的是「当时产物」非永久禁令，
      冷却期满允许再奏）。起算点按 **decided_at or created_at**（ADR-0053）：decided_at
      为人/系统做出拒绝决定的时刻，精确；老行 decided_at IS NULL 时回退 created_at
      （提案生成时刻）——旧口径逐字节不变，宁 miss 不猜（不拿 created_at 冒充 decided_at）。

    状态过滤下推 SQL（本函数只关心 pending/rejected 两态）：applied/shadow 行与判定
    无关却在库中永久累积，全表取回再于 Python 内过滤的代价随运行时长线性增长。
    """
    ev = frozenset(evidence_ids)
    if not ev:
        return None
    rows = s.exec(
        select(MemoryProposal)
        .where(MemoryProposal.proposal_type == "consolidation")
        .where(MemoryProposal.status.in_([ProposalStatus.PENDING, ProposalStatus.REJECTED]))
    ).all()
    now = utcnow()
    cooldown = timedelta(days=settings.CONSOLIDATION_REJECTED_COOLDOWN_DAYS)
    for row in rows:
        if frozenset(row.evidence_ids or []) != ev:
            continue
        if row.status == ProposalStatus.PENDING:
            return "dupes"
        if row.status == ProposalStatus.REJECTED:
            # decided_at 优先（ADR-0053）：裁决时刻精确起算；老行 NULL 回退 created_at
            decided = _as_utc(row.decided_at) or _as_utc(row.created_at)
            if decided is not None and now - decided <= cooldown:
                return "cooldown"
    return None


def _shadow_deadline_exceeded(s: Session) -> str | None:
    """shadow 硬时限（ADR-0050 决策 4）：自 ConsolidationRun 首条 mode=shadow 留痕起算，
    超期返回拒绝原因（调用方拒绝执行巩固并 ERROR 留痕），未超期/无留痕返回 None。

    「影子跑一周后由维护者决定切 enforce」由此获得牙齿：静默直写不能在
    过审制名义下无限合法存续。"""
    first = s.exec(
        select(ConsolidationRun)
        .where(ConsolidationRun.mode == "shadow")
        .order_by(ConsolidationRun.ran_at.asc())
    ).first()
    if first is None:
        return None
    first_at = _as_utc(first.ran_at)
    if first_at is None:
        return None
    max_days = settings.CONSOLIDATION_SHADOW_MAX_DAYS
    elapsed_days = (utcnow() - first_at).total_seconds() / 86400.0
    if elapsed_days > max_days:
        return (
            f"shadow 模式已超 {max_days} 天硬时限（首条留痕 {first_at.isoformat()}，"
            f"已 {elapsed_days:.1f} 天）——拒绝执行巩固，请维护者拍板切 enforce 或回 off"
        )
    return None


def _record_consolidation_run(s: Session, **fields) -> None:
    """巩固运行结论落库（consolidation_run 表；失败不阻断运行，宁 miss 不静默）。"""
    try:
        s.add(ConsolidationRun(id=new_id("run"), ran_at=utcnow(), **fields))
        s.commit()
    except Exception as exc:
        logger.warning("consolidation_run 落库失败（审计留痕不静默）: %s", exc)
        # 回滚失败同样不阻断：回滚本身失败说明会话已失效，静默收场（留痕已由 warning 承担）
        with contextlib.suppress(Exception):
            s.rollback()


def _refused_report(mode: str, error: str) -> dict:
    """拒绝执行本周期巩固时的 report（既有键不变，status=refused 可见）。"""
    return {
        "last_run": utcnow().isoformat(),
        "consolidated_groups": 0,
        "new_memories": 0,
        "pruned_count": 0,
        "status": "refused",
        "mode": mode,
        "proposals_created": 0,
        "skipped_dupes": 0,
        "skipped_rejected_cooldown": 0,
        "skipped_lowq": 0,
        "error": error,
    }


def run_consolidation_cycle(session: Session | None = None) -> dict:
    """运行一次完整的沉潜夜梦沉淀周期（ADR-0050：产物生效通道随模式分流）。"""

    def _run(s: Session) -> dict:
        global _LAST_CONSOLIDATION_REPORT
        mode = _resolve_audit_mode()
        if mode is None:
            # 非法值 fail-loud（ADR-0050 决策 1）：拒绝执行本周期巩固——
            # 宁巩固停摆，不静默直写；ERROR 留痕 + report status 可见。
            illegal = settings.CONSOLIDATION_AUDIT_MODE
            error = (
                f"CONSOLIDATION_AUDIT_MODE 非法值 {illegal!r}（合法 off/shadow/enforce）"
                "——拒绝执行本周期巩固（宁停摆不静默直写）"
            )
            # 拒绝事件持久留痕（与 shadow 硬时限拒绝同轨，ADR-0050 决策 9）：
            # 仅内存 report + logger 无持久痕，事后排查配置错误无所依凭；
            # mode 记原非法值（而非回落字面量）以保留线索。
            _record_consolidation_run(
                s,
                mode=illegal,
                clusters=0,
                purified_ok=0,
                proposals_created=0,
                skipped_dupes=0,
                skipped_rejected_cooldown=0,
                skipped_lowq=0,
                pruned=0,
                error=error,
            )
            report = _refused_report(illegal, error)
            _LAST_CONSOLIDATION_REPORT = report
            return report

        deadline_msg = _shadow_deadline_exceeded(s) if mode == "shadow" else None
        if deadline_msg is not None:
            # shadow 硬时限（ADR-0050 决策 4）：超期拒绝执行巩固并 ERROR 留痕（同非法值处置）
            logger.error("沉潜过审：%s", deadline_msg)
            _record_consolidation_run(
                s,
                mode="shadow",
                clusters=0,
                purified_ok=0,
                proposals_created=0,
                skipped_dupes=0,
                skipped_rejected_cooldown=0,
                skipped_lowq=0,
                pruned=0,
                error=deadline_msg,
            )
            report = _refused_report(mode, deadline_msg)
            _LAST_CONSOLIDATION_REPORT = report
            return report

        clusters = find_consolidation_clusters(s)
        new_count = 0
        proposals_created = 0
        purified_ok = 0
        skipped_dupes = 0
        skipped_cooldown = 0
        skipped_lowq = 0
        for cluster in clusters:
            if mode == "enforce":
                # 生成侧幂等去重与拒绝冷却（ADR-0050 决策 3）：先查后奏，
                # 防每夜重复提纯（真实 LLM 成本）与重复提案打扰
                blocked = _consolidation_proposal_blocked(s, [m.id for m in cluster])
                if blocked == "dupes":
                    skipped_dupes += 1
                    continue
                if blocked == "cooldown":
                    skipped_cooldown += 1
                    continue
            res = consolidate_cluster(cluster, session=s)
            if res is None:
                # LLM 失败/输出无效/TrustMem 不过：保持原库不变，计入 skipped 不静默
                # （同 autodream skipped 纪律）
                skipped_lowq += 1
            elif isinstance(res, MemoryProposal):
                proposals_created += 1
                purified_ok += 1
            else:
                new_count += 1
                purified_ok += 1
                if mode == "shadow":
                    # shadow 每笔直写恰同步落一条影子提案（consolidate_cluster 内）——
                    # report 契约：proposals_created 在 shadow 期 = 影子提案数 = 直写数
                    proposals_created += 1

        pruned = prune_decayed_synapses(session=s)
        # status 判定扩展（ADR-0050 决策 3）：只产提案的运行不再误报 idle
        status = "success" if (new_count + proposals_created + pruned) > 0 else "idle"

        report = {
            "last_run": utcnow().isoformat(),
            "consolidated_groups": len(clusters),
            "new_memories": new_count,
            "pruned_count": pruned,
            "status": status,
            "mode": mode,
            "proposals_created": proposals_created,
            "skipped_dupes": skipped_dupes,
            "skipped_rejected_cooldown": skipped_cooldown,
            "skipped_lowq": skipped_lowq,
            "error": "",
        }
        _LAST_CONSOLIDATION_REPORT = report

        # 运行留痕（ADR-0050 决策 9）：验收比例的分母地基；off 期不留痕（零漂移）
        if mode in ("shadow", "enforce"):
            _record_consolidation_run(
                s,
                mode=mode,
                clusters=len(clusters),
                purified_ok=purified_ok,
                proposals_created=proposals_created,
                skipped_dupes=skipped_dupes,
                skipped_rejected_cooldown=skipped_cooldown,
                skipped_lowq=skipped_lowq,
                pruned=pruned,
            )
        return report

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def consolidation_audit_report(window_days: int = 7) -> dict:
    """验收统计出口（ADR-0050 决策 9）：近 N 天巩固产物带提案记录比例（可证伪口径）。

    - enforce 自证（①②合取方构成票面「100% 可证伪」的完整断言）：
      ①比例＝窗口内创建（created_at∈窗口、排除 shadow 行）的 consolidation 提案数
      （status∈{pending, applied, rejected}）÷ 同窗口 mode="enforce" 留痕 purified_ok
      总数，目标恰 100%（偏高偏低均判异常）；
      ②直写指纹＝窗口内伪 id checkpoint（memory_id="cluster_consolidation"）新增行数
      必须为 0——独立于提案与留痕两路的持久化证据（仅①则同路径写两表的 bug 不可见，
      仅②则绕过两路的直写不可见）。
    - shadow 三方互证：直写主记忆数（伪 id checkpoint 行数）vs 影子提案数 vs
      mode="shadow" 留痕 purified_ok，三者应相等。
    - 无样本（分母为 0）时比例返回 None 不编造。
    - 如实声明：伪 id checkpoint 行 off/shadow 期同样产生，②的「必须为 0」仅在
      纯 enforce 期窗口内成立（跨模式切换窗口按桶自行判读）。
    """
    now = utcnow()
    start = now - timedelta(days=window_days)
    with db.get_session() as s:
        proposals = s.exec(
            select(MemoryProposal).where(MemoryProposal.proposal_type == "consolidation")
        ).all()
        runs = s.exec(select(ConsolidationRun)).all()
        pseudo_ckpts = s.exec(
            select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == "cluster_consolidation")
        ).all()

    def _in_window(dt: datetime | None) -> bool:
        v = _as_utc(dt)
        return v is not None and start <= v <= now

    win_props = [p for p in proposals if _in_window(p.created_at)]
    win_runs = [r for r in runs if _in_window(r.ran_at)]
    win_ckpts = [c for c in pseudo_ckpts if _in_window(c.created_at)]

    enforce_statuses = {ProposalStatus.PENDING, ProposalStatus.APPLIED, ProposalStatus.REJECTED}
    enforce_props = [p for p in win_props if p.status in enforce_statuses]
    shadow_props = [p for p in win_props if p.status == ProposalStatus.SHADOW]
    enforce_runs = [r for r in win_runs if r.mode == "enforce"]
    shadow_runs = [r for r in win_runs if r.mode == "shadow"]
    enforce_purified = sum(r.purified_ok for r in enforce_runs)
    shadow_purified = sum(r.purified_ok for r in shadow_runs)

    buckets: dict = {}
    for p in win_props:
        buckets[p.status] = buckets.get(p.status, 0) + 1

    # ①②合取（ADR-0050 决策 9）：单①则「同路径写两表」的 bug 不可见，单②则绕过
    # 两路的直写不可见——合取方是票面「100% 可证伪」的完整断言。分母为 0 时返回
    # None（不编造「通过」，与 ratio_ok 同纪律）。
    enforce_self_ok = (
        (len(enforce_props) == enforce_purified) and (len(win_ckpts) == 0)
        if enforce_purified
        else None
    )

    return {
        "window_days": window_days,
        "enforce": {
            "proposals_created": len(enforce_props),
            "purified_ok": enforce_purified,
            "proposal_ratio": (
                round(len(enforce_props) / enforce_purified, 4) if enforce_purified else None
            ),
            "ratio_ok": (len(enforce_props) == enforce_purified if enforce_purified else None),
            "pseudo_id_checkpoints": len(win_ckpts),
            "direct_write_fingerprint_ok": len(win_ckpts) == 0,
            "self_attestation_ok": enforce_self_ok,
        },
        "shadow": {
            "shadow_proposals": len(shadow_props),
            "purified_ok": shadow_purified,
            "pseudo_id_checkpoints": len(win_ckpts),
            "three_way_ok": (
                len(win_ckpts) == len(shadow_props) == shadow_purified
                if (shadow_props or shadow_purified or shadow_runs)
                else None
            ),
        },
        "proposal_buckets": buckets,
        "run_buckets": {
            "enforce_runs": len(enforce_runs),
            "shadow_runs": len(shadow_runs),
            "skipped_dupes": sum(r.skipped_dupes for r in win_runs),
            "skipped_rejected_cooldown": sum(r.skipped_rejected_cooldown for r in win_runs),
            "skipped_lowq": sum(r.skipped_lowq for r in win_runs),
        },
    }


def get_consolidation_report() -> dict:
    """获取最近一次沉潜运行报告。"""
    return _LAST_CONSOLIDATION_REPORT
