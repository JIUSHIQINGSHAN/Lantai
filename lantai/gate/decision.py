from sqlmodel import select

from lantai.core.acl import viewer_of
from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.evolution.promoter import _make_checkpoint
from lantai.gate.conflict_rules import check_antonyms, check_negation_pairs, check_rules
from lantai.gate.contradiction import check_contradiction
from lantai.gate.scorer import novelty_score
from lantai.llm.client import embed
from lantai.models.enums import GateDecision
from lantai.models.tables import CognitiveRole, ConflictEvent, MemoryCandidate, MemoryItem
from lantai.storage import db
from lantai.storage.vector_store import get_vector_store


def _load_conflict_candidates(s, summary_text: str, principal=None) -> list[MemoryItem]:
    """按向量相似度召回冲突检测候选（DD-03 修复：替代全表 [:10] 插入序抽查）。

    降级策略：向量检索失败时回退到全表前 CONFLICT_CHECK_TOP_K 条（宁有偏 miss 不全漏）。

    归属（票 `.scratch/readside-gaps/15`）：此前向量检索与全表兜底**一个
    过滤都不带**，A 只要构造一条与 B 的记忆关键词相撞的候选，
    `decide` 就会：把 B 的正文送进矛盾检测 LLM、按 salience **降 B 的
    importance**、并往 B 的记忆上写 ConflictEvent。三件事 A 都无权做。
    现按候选自身的属主收敛（候选是谁的，就只与谁的现有记忆比对）。
    """
    top_k = settings.CONFLICT_CHECK_TOP_K
    try:
        qv = embed([summary_text])[0]
        vs = get_vector_store()
        from lantai.core.acl import vector_owner_filter

        filters = vector_owner_filter(principal)
        vec_results = vs.search(qv, top_k=top_k, filters=filters)
        if vec_results:
            near_ids = [r["id"] for r in vec_results]
            candidates = s.exec(
                select(MemoryItem).where(MemoryItem.id.in_(near_ids), MemoryItem.status == "active")
            ).all()
            candidates = [m for m in candidates if _owns(m, principal)]
            if candidates:
                return candidates
    except Exception as e:
        logger.warning("conflict vector recall failed, falling back to rowid order: %s", e)

    # 降级：向量不可用时按 rowid 取前 top_k（行为与修复前一致但可配置）
    q = select(MemoryItem).where(MemoryItem.status == "active").limit(top_k)
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        viewer = viewer_of(principal)
        q = q.where((MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None)))
    return s.exec(q).all()


def _owns(mem, principal) -> bool:
    """行级归属判定（同 `memory_service._owns`，见票 readside-gaps/15）。"""
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return True
    return (mem.user_id or None) in (None, viewer_of(principal))


def _ensure_can_decide(principal, cand: MemoryCandidate) -> None:
    """裁决候选的归属校验（票 readside-gaps/15）。

    裁决是**破坏性操作**：`decide` 会往别人的记忆上写 ConflictEvent、
    按 salience 改 importance、并把候选推到 PROMOTE/WORKING_ONLY。
    此前 `/gate` 一个身份都不取，A 拿 B 的 `candidate_id` 就能裁 B 的候选
    ——冲突比对按候选自身属主收敛后，虽然 B 的记忆不再被误伤，但
    **B 的裁决结果仍由 A 说了算**（A 能让 B 的待决内容凭空晋升）。

    口径同票 02 的 `evolution_service._ensure_can_decide`（复用
    `acl.ensure_can_delete` 单一真源）：非 admin 只能裁自己的候选，
    NULL 属主老候选仅 admin 可裁。principal=None 仅限内部调用
    （worker/CLI/MCP），不校验——那些场景没有登录主体，按候选自身收敛。
    """
    if principal is None:
        return
    from lantai.core.acl import ensure_can_delete

    ensure_can_delete(
        principal,
        resource_user_id=cand.user_id,
        resource_tenant_id=cand.tenant_id,
    )


def decide(candidate_id: str, principal=None) -> dict:
    """闸门裁决。principal（票 readside-gaps/15）：冲突比对的归属边界，
    未传则按候选自身的 `user_id` / `tenant_id` 收敛。"""
    with db.get_session() as s:
        cand = s.get(MemoryCandidate, candidate_id)
        if not cand:
            return {"decision": GateDecision.REJECT, "reason": "candidate not found"}

        if cand.extractor_confidence < settings.GATE_MIN_EXTRACTOR_CONF:
            return {
                "decision": GateDecision.REJECT,
                "reason": f"low extractor confidence {cand.extractor_confidence:.2f}",
            }

        # 归属校验：非 admin 不能裁别人的候选（票 readside-gaps/15）。
        # 放在置信度检查之后、冲突比对之前——越权是 403，与「候选本身
        # 不合格」是两件事，不该互相掩盖。
        _ensure_can_decide(principal, cand)

        if principal is None:
            from lantai.core.auth import Principal

            principal = Principal(
                user_id=cand.user_id or "default",
                tenant_id=cand.tenant_id,
                allowed_lanes=None,
            )

        summary_text = cand.summary or " ".join(cand.claims)[:400]

        # DD-03: 用向量召回语义近邻作为冲突检测候选
        related = _load_conflict_candidates(s, summary_text, principal)
        related_texts = [m.content for m in related][: settings.GATE_NOVELTY_SAMPLE_SIZE]

        nv = novelty_score(summary_text, related_texts) if related_texts else 1.0

        conflicts = []  # 硬冲突（高 salience 确定性 + LLM）→ archive_conflict
        demoted = []  # 低 salience 确定性冲突 → 已降权放行（ADR-0020）
        demote_t = settings.CONFLICT_SALIENCE_MIN_IMPORTANCE
        demote_step = settings.CONFLICT_SALIENCE_DEMOTE_STEP
        # ADR-0020：确定性规则 + 反义词碰撞双通道优先（零 LLM、可复现）
        for m in related:
            hits = check_rules(summary_text, m.content) + check_antonyms(summary_text, m.content)
            for hit in hits:
                if m.importance < demote_t:
                    # salience 降权：弱旧记忆不挡新信息——降权（可回滚）+ 账本 resolved + 放行
                    old_imp = m.importance
                    m.importance = max(0.0, old_imp - demote_step)
                    _make_checkpoint(s, m, {"importance": old_imp}, "", trigger="salience_demote")
                    s.add(
                        ConflictEvent(
                            id=new_id("cfev"),
                            memory_id=m.id,
                            incoming_ref=summary_text[:200],
                            rule_name=hit["rule_name"],
                            kind="salience_demote",
                            detail={
                                "new_matched": hit.get("new_matched"),
                                "old_matched": hit.get("old_matched"),
                                "demoted_from": round(old_imp, 4),
                            },
                            status="resolved",
                        )
                    )
                    demoted.append(
                        {"memory_id": m.id, "reason": f"salience demote '{hit['rule_name']}'"}
                    )
                else:
                    conflicts.append(
                        {
                            "memory_id": m.id,
                            "severity": "high",
                            "reason": (
                                f"deterministic rule '{hit['rule_name']}' "
                                f"(new '{hit['new_matched']}' vs old '{hit['old_matched']}')"
                            ),
                            "rule_name": hit["rule_name"],
                        }
                    )
                    s.add(
                        ConflictEvent(
                            id=new_id("cfev"),
                            memory_id=m.id,
                            incoming_ref=summary_text[:200],
                            rule_name=hit["rule_name"],
                            kind=hit.get("kind", "mutex"),
                            detail={
                                "new_matched": hit["new_matched"],
                                "old_matched": hit["old_matched"],
                            },
                            status="open",
                        )
                    )
        # 规则/反义词均未命中（且无降权动作）→ 回落 LLM 矛盾检测（降级不阻断）
        check_unavailable = False
        if not conflicts and not demoted:
            for m in related:
                try:
                    c = check_contradiction(summary_text, m.content)
                except Exception as e:
                    # 双保险：连 check_contradiction 自身的 except 都兜不住时（如
                    # 签名变更 / patch 抛错），仍要留下「检不了」的痕迹。
                    # 旧实现此处把异常抹平成 {"contradicts": False}——与
                    # 「检测器说没矛盾」同形，调用方无从区分，只能放行。
                    logger.warning("候选 %s 的矛盾检测调用失败（按检不了处理）: %s", cand.id, e)
                    c = {"contradicts": False, "reason": "", "severity": "low"}
                    check_unavailable = True
                if c.get("contradicts"):
                    conflicts.append(
                        {
                            "memory_id": m.id,
                            "severity": c.get("severity", "low"),
                            "reason": c.get("reason", ""),
                        }
                    )
                elif c.get("check_unavailable"):
                    check_unavailable = True

        # 「检不了」≠「没矛盾」（票 .scratch/gate-fail-open/01）
        #
        # LLM 矛盾检测失败（超时/429/5xx/非 JSON/未配 key）时，旧链路把
        # contradicts: False 当「检测器说没矛盾」直接放行——LLM 一挂，
        # 矛盾检测整条通道静默失效，候选长驱直入 PROMOTE/WORKING_ONLY，
        # 日志里连一行都没有。
        #
        # 修法：check_contradiction 的失败返回带 `check_unavailable` 标记，
        # 此处据此判 REJECT，由 evolve_worker 走 reject 分支把候选推进
        # **待审队列**（pending_review + review_due_at）交人裁决。
        # 宁 miss 不脏写：miss 可以，但 miss 必须留痕、可被看见，
        # 不得伪装成「我检查过了，确实没矛盾」。
        #
        # 放在此处（而非与 ADR-0024 合并）的用意：确定性规则已命中的硬冲突
        # 走上面的 conflicts 分支原样 archive_conflict，不受影响；只有
        # 「本该靠 LLM 判断而 LLM 不可用」才降级为待审。
        # 不新加 GateDecision 成员——staged scorer 对未知成员会静默错算，
        # evolve_worker 的 `== "reject"` 分支也会漏接。
        if check_unavailable:
            return {
                "decision": GateDecision.REJECT,
                "reason": "contradiction check unavailable, needs human review",
                "check_unavailable": True,
                "conflicts": conflicts,
                "novelty": nv,
            }

        # ADR-0024：单字否定对候选（是/不是、会/不会…）→ LLM 裁决。
        # 候选不落硬规则；LLM 判非矛盾/失败 → 放行（宁 miss）。
        if settings.CONFLICT_NEGATION_ENABLED:
            for m in related:
                if check_negation_pairs(summary_text, m.content):
                    try:
                        c = check_contradiction(summary_text, m.content)
                    except Exception:
                        c = {"contradicts": False, "reason": "", "severity": "low"}
                    if c.get("contradicts"):
                        conflicts.append(
                            {
                                "memory_id": m.id,
                                "severity": c.get("severity", "low"),
                                "reason": f"negation candidate: {c.get('reason', '')}",
                            }
                        )

        if demoted:
            s.commit()  # 降权 + Checkpoint + resolved 账本持久化（宁 miss 不脏写：有迹可溯）

        if conflicts and any(c["severity"] == "high" for c in conflicts):
            # 引入 ConflictEngine 仲裁：检验候选与冲突现有记忆是否能 COEXIST（例如适用场景/任务边界互斥）
            from lantai.cognition.conflicts import ConflictEngine, ConflictResolution

            engine = ConflictEngine()
            cand_dummy = MemoryItem(
                id="cand_probe",
                content=summary_text,
                role=CognitiveRole.OBSERVATION,
                confidence=cand.extractor_confidence,
                structure={"scope": {"domain": getattr(cand, "lane", "general")}},
            )
            all_coexist = True
            for c_info in conflicts:
                if c_info["severity"] == "high":
                    exist_mem = s.get(MemoryItem, c_info["memory_id"])
                    if exist_mem:
                        c_res = engine.resolve(cand_dummy, exist_mem, session=s)
                        if c_res.resolution != ConflictResolution.COEXIST:
                            all_coexist = False
                            break
                    else:
                        all_coexist = False
                        break

            if all_coexist and conflicts:
                # 冲突双方适用任务/边界条件互斥，允许作为互斥分支共存（COEXIST），不作 ARCHIVE_CONFLICT 阻断
                logger.info("ConflictEngine resolved conflict as COEXIST for candidate %s", cand.id)
            else:
                s.commit()  # 账本落库
                return {
                    "decision": GateDecision.ARCHIVE_CONFLICT,
                    "reason": "hard contradiction with existing memory",
                    "conflicts": conflicts,
                    "novelty": nv,
                }

        if nv < settings.GATE_NOVELTY_THRESHOLD:
            # 语义高度重叠 ≠ 丢弃：可能有增量信息（如新配置项）。
            # 降级到 WORKING_ONLY，由提案系统走 update/merge 并入现有记忆，
            # 而不是静默 reject 丢数据（落地实战发现：显卡 RTX3050 增量被误杀）。
            return {
                "decision": GateDecision.WORKING_ONLY,
                "reason": f"low novelty {nv:.2f}, fallback to merge path",
                "novelty": nv,
                "conflicts": conflicts,
            }

        if cand.actions:
            return {
                "decision": GateDecision.PROMOTE_PROCEDURAL,
                "novelty": nv,
                "conflicts": conflicts,
            }

        return {"decision": GateDecision.WORKING_ONLY, "novelty": nv, "conflicts": conflicts}
