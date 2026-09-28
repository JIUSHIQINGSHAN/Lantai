from fastapi import HTTPException
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.provenance import PROVENANCE_PROMPT_DIALOGUE_IMPORT
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.llm.client import embed
from lantai.memory.decay_class import infer_decay_class
from lantai.models.enums import MemoryTier, ProposalStatus
from lantai.models.tables import (
    Evidence,
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryEdge,
    MemoryItem,
    MemoryProposal,
)
from lantai.retrieval.hybrid import delete_memory_item, index_memory_item
from lantai.storage import db
from lantai.storage.fts import sync_fts

# 提案类型白名单（票据 `.scratch/proposal-type-whitelist/`）：
# apply_proposal 的末行兜底 `elif proposal_type == "add" or not existing:`
# 会吞掉任何未知类型——新建记忆 + trigger 记 gate + 多出 supports 边，
# 而提案本想做的事一件没做。故类型必须显式枚举，不认识的即拒绝。
# consolidation 由 consolidation_service 直写产生，必须含；
# 与 reflector._VALID_TYPES 的差异（后者无 consolidation）是两者产出面不同。
VALID_PROPOSAL_TYPES = {"add", "update", "merge", "deprecate", "consolidation"}


def _make_checkpoint(session, mem: MemoryItem, before: dict, proposal_id: str, trigger: str):
    session.add(
        MemoryCheckpoint(
            id=new_id("ckpt"),
            memory_id=mem.id,
            version=mem.version,
            before=before,
            after=mem.model_dump(mode="json"),
            proposal_id=proposal_id,
            trigger=trigger,
        )
    )


def _reject_proposal(prop: MemoryProposal, reason: str, detail: dict | None = None) -> dict:
    """提案落终态 REJECTED（含 decided_at 与 decision_reason）并提交，返回拒绝结果。

    口径同 ADR-0050 决策 3 的 stale 硬门：apply 若仅早退而不改状态，
    decide_proposal 先置的 APPROVED 将永留，evolve_worker.run_pending_proposals
    （专捞 APPROVED）每轮重试每次失败＝livelock。故拒绝必须是**终态落库**。
    decided_at（ADR-0053）：拒绝即系统裁决时刻，与人工 reject 同口径。
    """
    prop.status = ProposalStatus.REJECTED
    prop.decision_reason = reason
    prop.decided_at = utcnow()
    return {"ok": False, "reason": reason, "detail": detail or {}}


def _resolve_update_target(s, prop: MemoryProposal, key: str | None) -> tuple[object, str]:
    """为 update / merge / deprecate 解析**唯一** active 目标。

    返回 (existing_or_None, refusal_reason)。refusal_reason 非空即须拒绝。

    寻址口径（票据 `.scratch/proposal-target-gap/issues/02-*.md`，实证真实开发库
    72 条非 add 提案中 43% 目标寻址失败后静默落入 add 分支新建平行记忆、
    24% key 多义时任取其一）：
    1. `target_memory_id` 指向 active 记忆 → 用之（reflect 路径的正规来源）；
    2. 否则按 `key` 查 active 记忆：**恰好一条** → 用之，并回填
       `prop.target_memory_id`（留痕本次寻址，供审计追溯）；
    3. 零条 → 拒绝「目标不存在」。多义 → 拒绝并列全部候选 id 交人工裁决，
       **不任取其一**（`MemoryItem.key` 无 unique 约束，`.first()` 无 order_by
       取哪条由执行计划决定，属静默改错目标）。

    宁 miss 不脏写：猜不中就不写，绝不降级为 add 新建平行记忆。
    """
    if prop.target_memory_id:
        existing = s.get(MemoryItem, prop.target_memory_id)
        if existing is not None and existing.status == "active":
            return existing, ""
        return None, "target memory not active"

    if not key:
        return None, "no target: neither target_memory_id nor proposed_patch.key"

    candidates = s.exec(
        select(MemoryItem).where(MemoryItem.key == key, MemoryItem.status == "active")
    ).all()
    if len(candidates) == 1:
        # key 回退解析成功：回填 target 留痕（原字段为空即「未记录」的事实状态，
        # 此处是系统解析结果而非猜测，故如实补记）
        prop.target_memory_id = candidates[0].id
        s.add(prop)
        return candidates[0], ""
    if len(candidates) > 1:
        return None, f"ambiguous target: key {key!r} matches {len(candidates)} active memories"
    return None, f"target not found: no active memory with key {key!r}"


def _target_ids(prop: MemoryProposal) -> list[str]:
    """本提案要**写**的全部目标 id（寻址字段全来自 LLM 输出，见票 16）。

    三类寻址字段都要算进去，一个都不能漏：
    - `target_memory_id`：reflect/向量/寻址硬门的正规来源；
    - `evidence_ids`：merge 证据环与 consolidation 折叠的**删除对象**，
      add 分支则为它建 supports 边；
    - `proposed_patch["key"]`：`_resolve_update_target` 的 key 回退解析结果
      （`target_memory_id` 为空时它才是真正的目标，单看前者判不出问题）。

    去重保序：同一 id 既是 target 又是 evidence 是常见形状（merge 的自引用）。
    """
    ids: list[str] = []
    if prop.target_memory_id:
        ids.append(prop.target_memory_id)
    for eid in prop.evidence_ids or []:
        if eid and eid not in ids:
            ids.append(eid)
    key = (prop.proposed_patch or {}).get("key")
    if key and not prop.target_memory_id:
        # key 只在 target 为空时参与寻址（`_resolve_update_target` 的优先级）
        ids.append(f"__key__{key}")
    return ids


def _ensure_targets_owned(s, prop: MemoryProposal, principal) -> str:
    """apply 边界的归属硬门（票 `.scratch/proposal-apply-gaps/issues/16-*.md`）。

    **为什么是一道门而不是逐个 `session.get` 后补判定**：merge / consolidation
    一次写多个目标。逐条补判定时，evidence 环那条照样能删别人的记忆（票 14 的
    rejecter 就是这么漏的），或者先把自己的应用了、再把别人的跳过了——半 apply
    比不 apply 更脏（A 的记忆已被折叠且没有 undo）。故在动手之前**一次性**解析
    全部目标 id 并统一校验，任一条不属于即整体拒绝。

    返回空串 = 全部通过；否则返回拒绝理由（调用方落 REJECTED + 留痕）。

    归属口径同前 14 票：admin / `principal=None` 全不过滤（worker/CLI/scheduler
    收窄成空转会让演化与遗忘整体停摆）；非 admin 要求 `user_id == viewer`
    或 `user_id IS NULL`（真实库 636/657 行是 NULL 属主，判不可见会让单人部署
    整体空转——NULL 是「未记录」不是「属于所有人」）。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return ""
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    ids = _target_ids(prop)
    if not ids:
        return ""
    rows = s.exec(select(MemoryItem).where(MemoryItem.id.in_(ids))).all()
    by_id = {m.id: m for m in rows}
    # key 回退：按 key 查 active 记忆（与 _resolve_update_target 同口径）。
    # 必须在 SQL 层做，因为这条路径本来就没有 target_memory_id 可判。
    key_ids = [i[len("__key__") :] for i in ids if i.startswith("__key__")]
    if key_ids:
        for m in s.exec(
            select(MemoryItem).where(MemoryItem.key.in_(key_ids), MemoryItem.status == "active")
        ).all():
            by_id.setdefault(m.id, m)
    foreign = [mid for mid, m in by_id.items() if (m.user_id or None) not in (None, viewer)]
    if foreign:
        return (
            f"target not owned: {len(foreign)} of {len(ids)} target(s) belong to "
            f"another user (宁 miss 不脏写，不降级为 add)"
        )
    # 目标 id 一个都没解析到：不在这里拒绝——寻址硬门会按自己的口径报
    # 「target not found」/「ambiguous」（那两条的拒绝理由信息量更大）。
    return ""


def apply_proposal(proposal_id: str, principal=None) -> dict:
    """应用提案。

    `principal`（票 `.scratch/proposal-apply-gaps/issues/16-*.md`）：apply 是
    **写**不是读，而三个寻址字段（`target_memory_id` / `evidence_ids` /
    `proposed_patch["key"]`）全部来自 LLM 输出——curator 回一个别人的记忆 id，
    这里就按主键直读然后改写它（deprecate 归档、merge 证据环从 FTS 与向量库
    除名、consolidation 折叠），且都没有 undo 入口。故在动手之前一次性校验
    全部目标归属，任一条不属于即整体拒绝。口径同前 14 票：admin /
    `principal=None`（worker/CLI/scheduler）不过滤。
    """
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        # 可应用状态：PENDING（evolve 自动路径）或 APPROVED（人工审批/补跑路径）
        if not prop or prop.status not in (ProposalStatus.PENDING, ProposalStatus.APPROVED):
            return {"ok": False, "reason": "not applicable"}

        # ── 归属硬门（先于一切分支，见 docstring）──────────────────────
        # 放在类型白名单之前：越权与「类型不合法」是两件事，但越权更严重，
        # 且此时一个字段都还没解析过，是全链唯一「还没写任何东西」的时刻。
        refusal = _ensure_targets_owned(s, prop, principal)
        if refusal:
            logger.warning(
                "提案 %s apply 拒绝：%s（type=%s，宁 miss 不脏写，不降级为 add）",
                prop.id,
                refusal,
                prop.proposal_type,
            )
            out = _reject_proposal(prop, refusal)
            s.add(prop)
            s.commit()
            return out

        patch = prop.proposed_patch
        mem_type = patch.get("memory_type", "semantic")
        key = patch.get("key")
        content = patch.get("content", "")
        lane = patch.get("lane", settings.DEFAULT_LANE)

        # ── 类型白名单硬门 ──────────────────────────────────────────
        # 不认识的类型一律拒绝，**不落入下方 add 兜底分支**：那会新建一条
        # 平行记忆、trigger 记 gate、多出 supports 边与 Evidence，而提案
        # 本想做的事（折叠/拆分/关联……）一件没做——脏写且不可逆。
        # 与下方目标寻址硬门同构：显式 REJECTED + 留痕，宁 miss 不脏写。
        if prop.proposal_type not in VALID_PROPOSAL_TYPES:
            reason = f"unknown proposal_type: {prop.proposal_type!r}"
            logger.warning(
                "提案 %s apply 拒绝：%s（合法类型 %s，宁 miss 不脏写，不降级为 add）",
                prop.id,
                reason,
                sorted(VALID_PROPOSAL_TYPES),
            )
            out = _reject_proposal(prop, reason)
            s.add(prop)
            s.commit()
            return out

        # ── 目标寻址硬门（update / merge / deprecate）──────────────────
        # add 本就不该有目标；consolidation 的主记忆是新建实体、碎片由 evidence_ids
        # 寻址，均不经此门。其余三类解析不到唯一 active 目标即显式拒绝，
        # 不落入下方 add 分支新建平行记忆（新旧矛盾并存即脏写）。
        needs_target = prop.proposal_type in ("update", "merge", "deprecate")
        existing = None
        if needs_target:
            existing, refusal = _resolve_update_target(s, prop, key)
            if refusal:
                detail = None
                if refusal.startswith("ambiguous target"):
                    cands = s.exec(
                        select(MemoryItem).where(
                            MemoryItem.key == key, MemoryItem.status == "active"
                        )
                    ).all()
                    detail = {"candidates": [m.id for m in cands]}
                logger.warning(
                    "提案 %s apply 拒绝：%s（type=%s，宁 miss 不脏写，不降级为 add）",
                    prop.id,
                    refusal,
                    prop.proposal_type,
                )
                out = _reject_proposal(prop, refusal, detail)
                s.add(prop)
                s.commit()
                return out
        else:
            # add / consolidation 保持原有寻址（target 命中或 key 回退，取首个）
            if prop.target_memory_id:
                existing = s.get(MemoryItem, prop.target_memory_id)
                if existing and existing.status != "active":
                    existing = None
            if existing is None and key:
                existing = s.exec(
                    select(MemoryItem).where(MemoryItem.key == key, MemoryItem.status == "active")
                ).first()

        emb = embed([content])[0] if content else []
        # 分支附加返回（ADR-0050：evidence 三分缺口记入 apply 返回与日志）；其余分支为空
        apply_extra: dict = {}

        # 反思模块（spec: docs/plans/reflection-module-spec.md）：deprecate / merge 分支。
        # add/update 语义保持不动（门面铁律）；deprecate/merge 都走 checkpoint + supersedes 边。
        # 沉潜过审（ADR-0050）：consolidation 分支见下方显式 elif（必须先于 add 捕获分支）。
        if prop.proposal_type == "deprecate" and existing:
            before = existing.model_dump(mode="json")
            existing.valid_to = utcnow()
            existing.status = "archived"
            existing.version += 1
            existing.updated_at = utcnow()
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="reflect")
            if prop.evidence_ids:
                dup = s.exec(
                    select(MemoryEdge).where(
                        MemoryEdge.relation == "supersedes",
                        MemoryEdge.source_memory_id == prop.evidence_ids[0],
                        MemoryEdge.target_memory_id == existing.id,
                    )
                ).first()
                if dup is None:
                    s.add(
                        MemoryEdge(
                            id=new_id("edge"),
                            source_memory_id=prop.evidence_ids[0],
                            target_memory_id=existing.id,
                            relation="supersedes",
                            confidence=prop.confidence,
                        )
                    )
            sync_fts(s, existing.id, None)
            delete_memory_item(existing.id)
        elif prop.proposal_type == "merge" and existing:
            before = existing.model_dump(mode="json")
            existing.content = content or existing.content
            existing.version += 1
            existing.updated_at = utcnow()
            existing.source_ids = list(set(existing.source_ids + prop.evidence_ids))
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="reflect")
            emb2 = embed([existing.content])[0]
            index_memory_item(
                existing.id,
                emb2,
                {
                    "key": existing.key,
                    "memory_type": existing.memory_type,
                    "lane": getattr(existing, "lane", "general") or "general",
                    "domain": getattr(existing, "domain", "user") or "user",
                    "tenant_id": getattr(existing, "tenant_id", "") or "",
                    "user_id": getattr(existing, "user_id", "") or "",
                    "session_id": getattr(existing, "session_id", "") or "",
                    "agent_id": getattr(existing, "agent_id", "") or "",
                },
            )
            sync_fts(s, existing.id, existing.content)
            for eid in prop.evidence_ids:
                if eid == existing.id:
                    continue
                src = s.get(MemoryItem, eid)
                if not src or src.status != "active":
                    continue
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=existing.id,
                        target_memory_id=src.id,
                        relation="supersedes",
                        confidence=prop.confidence,
                    )
                )
                src_before = src.model_dump(mode="json")
                src.status = "archived"
                src.version += 1
                src.updated_at = utcnow()
                s.add(src)
                _make_checkpoint(s, src, src_before, prop.id, trigger="reflect")
                sync_fts(s, src.id, None)
                delete_memory_item(src.id)
        elif prop.proposal_type == "consolidation":
            # 沉潜过审（ADR-0050/票 07）：巩固提案 apply——一个事务内主记忆落库＋碎片折叠
            # ＋supersedes 边＋逐条真实 id checkpoint＋索引同步。本分支必须显式插在 add
            # 捕获分支之前：add 分支会吞掉任何未知 proposal_type（落主记忆但不折叠碎片、
            # checkpoint 变 gate、多出 supports 边与 Evidence）。
            evidence_ids = list(dict.fromkeys(prop.evidence_ids or []))
            # stale 硬门（ADR-0050 决策 3）：任一 evidence 已 consolidated 即拒绝——
            # consolidated 只能由一次折叠产生，出现即意味存在 off 期直写产物或先行
            # apply 的重叠提案，继续执行将产出双主记忆重复召回（宁 miss 不脏写）。
            stale_hits = []
            for eid in evidence_ids:
                src = s.get(MemoryItem, eid)
                if src is not None and src.status == "consolidated":
                    stale_hits.append(eid)
            if stale_hits:
                logger.warning(
                    "巩固提案 %s apply 拒绝：evidence 已 consolidated（stale 硬门）%s",
                    prop.id,
                    stale_hits,
                )
                # 落终态 REJECTED（ADR-0050 决策 3「拒绝该提案」的终态语义）：apply 若仅
                # 早退而不改状态，decide_proposal 先置的 APPROVED 将永留，evolve_worker
                # .run_pending_proposals（专捞 APPROVED）每轮重试每次失败＝livelock。
                # 拒绝理由与命中的 evidence 一并留痕（宁 miss 不脏写、不静默）。
                prop.status = ProposalStatus.REJECTED
                prop.decision_reason = (
                    f"stale: evidence already consolidated {stale_hits}"
                    "（ADR-0050 决策 3 硬门：consolidated 只能由一次折叠产生，"
                    "继续执行将产出双主记忆重复召回）"
                )
                # decided_at（ADR-0053）：stale 硬门即系统裁决时刻，与人工 reject 同口径落值
                prop.decided_at = utcnow()
                s.add(prop)
                s.commit()
                return {
                    "ok": False,
                    "reason": "stale: evidence already consolidated",
                    "detail": {"consolidated_evidence_ids": stale_hits},
                }
            patch_full = prop.proposed_patch
            # 主记忆构造取 proposed_patch 不重推断（生成侧 _master_patch 平移现状构造：
            # decay_class 恒 semantic、decay_score=1.0、不设四元组——行为零漂移）
            master = MemoryItem(
                id=new_id("mem"),
                content=patch_full.get("content", ""),
                domain=patch_full.get("domain", "user"),
                lane=patch_full.get("lane", settings.DEFAULT_LANE),
                source_ids=list(patch_full.get("source_ids") or evidence_ids),
                confidence=float(patch_full.get("confidence", prop.confidence)),
                importance=float(patch_full.get("importance", 0.5)),
                decay_score=1.0,
                decay_class=patch_full.get("decay_class", "semantic"),
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(master)
            s.flush()
            # 主记忆为新建实体：before={} 空 dict（仿 add 分支先例），checkpoint 带真实 id
            _make_checkpoint(s, master, {}, prop.id, trigger="consolidation")
            index_memory_item(
                master.id,
                emb,
                {
                    "key": master.key,
                    "memory_type": master.memory_type,
                    "lane": getattr(master, "lane", "general") or "general",
                    "domain": getattr(master, "domain", "user") or "user",
                    "tenant_id": getattr(master, "tenant_id", "") or "",
                    "user_id": getattr(master, "user_id", "") or "",
                    "session_id": getattr(master, "session_id", "") or "",
                    "agent_id": getattr(master, "agent_id", "") or "",
                },
            )
            sync_fts(s, master.id, master.content)
            # 碎片现状三分（pending 期碎片并非零变更：遗忘/同周期 prune/笔削/晚更正均可
            # 变更或移除；TrustMem 不重跑，提纯基线过时风险由裁决者凭 evidence 现状判断）
            folded = edge_only = missing = 0
            for eid in evidence_ids:
                src = s.get(MemoryItem, eid)
                if src is None:
                    # 行已删除：不建边（边指向幽灵碎片即脏写），缺口留日志与返回
                    missing += 1
                    continue
                # supersedes 边（主记忆→碎片，方向同 merge 分支）；巩固不是新证据入树，
                # 不复用 add 分支的 supports 边与 Evidence
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=master.id,
                        target_memory_id=src.id,
                        relation="supersedes",
                        confidence=prop.confidence,
                    )
                )
                if src.status == "active":
                    # 仍 active：折叠＋checkpoint（before＝apply 时刻实际现状，
                    # 非「巩固前现场」——生成时刻基线由提案 proposed_patch/provenance 承载）
                    src_before = src.model_dump(mode="json")
                    src.status = "consolidated"
                    src.version += 1
                    src.updated_at = utcnow()
                    s.add(src)
                    _make_checkpoint(s, src, src_before, prop.id, trigger="consolidation")
                    sync_fts(s, src.id, None)
                    delete_memory_item(src.id)
                    folded += 1
                else:
                    # 已 archived（或 retracted 等其他非 active 态）：仅补 supersedes 边
                    # 血缘补记，不改状态、无 checkpoint
                    edge_only += 1
            if missing or edge_only:
                logger.info(
                    "巩固提案 %s apply：主记忆 %s 落库，折叠 %d 条碎片，"
                    "%d 条已非 active 仅补血缘边，%d 条已删除不建边",
                    prop.id,
                    master.id,
                    folded,
                    edge_only,
                    missing,
                )
            else:
                logger.info(
                    "巩固提案 %s apply：主记忆 %s 落库，折叠 %d 条碎片",
                    prop.id,
                    master.id,
                    folded,
                )
            apply_extra = {
                "master_id": master.id,
                "folded": folded,
                "edge_only": edge_only,
                "evidence_missing": missing,
            }
        elif prop.proposal_type == "add" or not existing:
            source_ids = list(set(prop.evidence_ids))
            tier = (
                MemoryTier.LONG_TERM
                if len(source_ids) >= settings.PROMOTE_SEMANTIC_MIN_SOURCES
                else MemoryTier.WORKING
            )
            structure = patch.get("structure") or {}
            mem_kwargs = dict(
                id=new_id("mem"),
                memory_type=mem_type,
                key=key or content[:60],
                content=content,
                tier=tier,
                source_ids=source_ids,
                confidence=prop.confidence,
                importance=0.5,
                lane=lane,
                structure=structure,
                provenance=prop.provenance or {},
                # 不信任 proposal 携带的 metadata 覆盖 decay_class（不可信来源）；
                # 仅按内容关键词推断，显式调级走 service 层 set_decay_class（带 checkpoint）
                decay_class=infer_decay_class(key or "", content),
            )
            # 冷启动导入（dialogue-session-import）：候选原始时间戳继承到
            # MemoryItem.created_at——历史会话时间线不压平到导入时刻
            src_cand = s.get(MemoryCandidate, prop.candidate_id) if prop.candidate_id else None
            if prop.provenance.get("prompt") == PROVENANCE_PROMPT_DIALOGUE_IMPORT:
                if src_cand and src_cand.created_at:
                    mem_kwargs["created_at"] = src_cand.created_at
            # 来源链继承（v022 票据 01）：出身由写入方显式声明，随
            # candidate → proposal → MemoryItem 落值；空则如实 NULL
            if src_cand is not None:
                if src_cand.session_id:
                    mem_kwargs["session_id"] = src_cand.session_id
                if src_cand.user_id:
                    mem_kwargs["user_id"] = src_cand.user_id
            # 更漏（ADR-0048/票 08）：提案携带显式事件时间 → I1 校验后落列；
            # 违例拒写留痕（宁 miss 不脏写，不静默修正）
            et = patch.get("event_time")
            etp = patch.get("event_time_precision", "")
            if et is not None or etp:
                from lantai.core.time_precision import validate_event_time_pair

                if isinstance(et, str):
                    from lantai.core.time import parse_iso_utc

                    try:
                        et = parse_iso_utc(et)
                    except ValueError:
                        # 落终态 REJECTED（票 .scratch/proposal-livelock/01）：与 stale
                        # 硬门同口径。只早退不改状态的话，decide_proposal 先置的
                        # APPROVED 将永留，evolve_worker.run_pending_proposals（专捞
                        # APPROVED）每轮重试每次失败＝livelock。
                        # 宁 miss 不脏写：坏配对不静默修正，显式拒绝 + 留痕。
                        out = _reject_proposal(
                            prop,
                            "invalid event_time pair (I1)",
                            {"event_time": et, "event_time_precision": etp},
                        )
                        s.add(prop)
                        s.commit()
                        return out
                if not validate_event_time_pair(et, etp):
                    out = _reject_proposal(
                        prop,
                        "invalid event_time pair (I1)",
                        {"event_time": str(et), "event_time_precision": etp},
                    )
                    s.add(prop)
                    s.commit()
                    return out
                mem_kwargs["event_time"] = et
                mem_kwargs["event_time_precision"] = etp
            mem = MemoryItem(**mem_kwargs)
            # Skill 资产化：提案携带步骤结构 → 视为技能（procedural 永不衰减）
            if structure.get("steps"):
                mem.decay_class = "procedural"
            s.add(mem)
            s.flush()
            _make_checkpoint(s, mem, {}, prop.id, trigger="gate")
            index_memory_item(
                mem.id,
                emb,
                {
                    "key": mem.key,
                    "memory_type": mem.memory_type,
                    "lane": getattr(mem, "lane", "general") or "general",
                    "domain": getattr(mem, "domain", "user") or "user",
                    "tenant_id": getattr(mem, "tenant_id", "") or "",
                    "user_id": getattr(mem, "user_id", "") or "",
                    "session_id": getattr(mem, "session_id", "") or "",
                    "agent_id": getattr(mem, "agent_id", "") or "",
                },
            )
            sync_fts(s, mem.id, mem.content)
            # 自动创建关系边——用外层 session 同事务写入（独立 session 会触发 SQLite 自锁）
            for evidence_id in prop.evidence_ids:
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=evidence_id,
                        target_memory_id=mem.id,
                        relation="supports",
                        confidence=prop.confidence,
                    )
                )
            # 闭环：新写入 MemoryItem 时为其创建对应的根证据 Evidence
            s.add(
                Evidence(
                    id=new_id("ev"),
                    tenant_id=mem.tenant_id,
                    user_id=mem.user_id,
                    agent_id=mem.agent_id,
                    session_id=mem.session_id,
                    evidence_type="proposal_applied",
                    source_memory_id=mem.id,
                    content=mem.content,
                    reliability=max(0.6, prop.confidence),
                    independence=1.0,
                    provenance=prop.provenance or {},
                    created_at=utcnow(),
                )
            )
        else:
            before = existing.model_dump(mode="json")
            existing.content = content
            existing.version += 1
            existing.updated_at = utcnow()
            existing.source_ids = list(set(existing.source_ids + prop.evidence_ids))
            existing.confidence = max(existing.confidence, prop.confidence)
            s.add(existing)
            s.flush()
            _make_checkpoint(s, existing, before, prop.id, trigger="evolve")
            index_memory_item(
                existing.id,
                emb,
                {
                    "key": existing.key,
                    "memory_type": existing.memory_type,
                    "lane": getattr(existing, "lane", "general") or "general",
                    "domain": getattr(existing, "domain", "user") or "user",
                    "tenant_id": getattr(existing, "tenant_id", "") or "",
                    "user_id": getattr(existing, "user_id", "") or "",
                    "session_id": getattr(existing, "session_id", "") or "",
                    "agent_id": getattr(existing, "agent_id", "") or "",
                },
            )
            sync_fts(s, existing.id, existing.content)

        prop.status = ProposalStatus.APPLIED
        prop.applied_at = utcnow()
        s.add(prop)
        s.commit()
        return {"ok": True, "proposal_id": prop.id, **apply_extra}


def rollback(memory_id: str, principal=None) -> dict:
    """回滚记忆到上一版本（Checkpoint 快照）。

    归属（票 .scratch/readside-gaps/13）：此前只按 id 取行、一个身份都不取——
    `prev.after` 逐字段 `setattr` 覆盖，**能把别人的正文整条换成历史任意版本**，
    且没有 undo 入口。三个入口（REST / MCP / worker）共用此处，只修一处不够。
    校验复用 `acl.ensure_can_delete` 单一真源（同票 04/06/11 写侧范式）。

    `principal=None`（worker/CLI/scheduler）保持全表，与已修各票逐字一致。
    """
    with db.get_session() as s:
        ckpts = s.exec(
            select(MemoryCheckpoint)
            .where(MemoryCheckpoint.memory_id == memory_id)
            .order_by(MemoryCheckpoint.version.desc())
        ).all()
        if len(ckpts) < 2:
            return {"ok": False, "reason": "no previous version"}
        prev = ckpts[1]
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False, "reason": "memory missing"}
        if principal is not None:
            from lantai.core.acl import ensure_can_delete

            try:
                ensure_can_delete(
                    principal,
                    resource_user_id=mem.user_id,
                    resource_tenant_id=mem.tenant_id,
                    lane=mem.lane,
                )
            except HTTPException as exc:
                # service 契约是 dict（被 worker/eval/MCP 多处消费，形状不能动），
                # 403 语义在路由边界由 _ok_or_raise 翻译；这里只报「不允许」。
                return {"ok": False, "reason": f"forbidden: {exc.detail}"}
        before = mem.model_dump(mode="json")
        for k, v in prev.after.items():
            if hasattr(mem, k) and k != "id":
                setattr(mem, k, v)
        mem.version += 1
        mem.updated_at = utcnow()
        s.add(mem)
        _make_checkpoint(s, mem, before, proposal_id="", trigger="rollback")
        sync_fts(s, mem.id, mem.content)
        s.commit()
        return {"ok": True}


def delete_memory(memory_id: str, principal=None) -> dict:
    """删除记忆（从 SQLite + 向量存储）

    归属（票 `.scratch/readside-gaps/18`）：此前无 principal，`s.get` 取到就删。
    这是全代码库最具破坏性的操作，而调用方 `source_service.delete_document`
    会把它用在**级联目标**上——文档属于 A，不等于它指向的记忆属于 A
    （票 17 修前 `MemoryEdge` 无归属过滤，票 16 的无归属 apply 也造这种边）。
    取到行后过 `ensure_can_delete`，同票 04/06/11/13 写侧范式。

    `ok: False` 而非抛异常：service 契约是 dict（被 worker/eval/MCP 多处消费，
    形状不能动），403 语义在路由边界由 `_ok_or_raise` 翻译（同 `rollback`）。
    """
    with db.get_session() as s:
        mem = s.get(MemoryItem, memory_id)
        if not mem:
            return {"ok": False, "reason": "memory missing"}
        if principal is not None:
            from fastapi import HTTPException

            from lantai.core.acl import ensure_can_delete

            try:
                ensure_can_delete(
                    principal,
                    resource_user_id=mem.user_id,
                    resource_tenant_id=mem.tenant_id,
                    lane=mem.lane,
                )
            except HTTPException as exc:
                return {"ok": False, "reason": f"forbidden: {exc.detail}"}
        s.delete(mem)
        sync_fts(s, memory_id, None)
        s.commit()
    delete_memory_item(memory_id)
    return {"ok": True}
