from dataclasses import dataclass, field

from sqlmodel import Session, select

from lantai.core.settings import settings
from lantai.models.tables import CognitiveRole, FailureRecord, MemoryItem

_ROLE_TO_SECTION = {
    CognitiveRole.OBSERVATION: "facts",
    CognitiveRole.EXPERIENCE: "experience",
    CognitiveRole.BELIEF: "beliefs",
    CognitiveRole.RULE: "rules",
    CognitiveRole.PRINCIPLE: "principles",
    CognitiveRole.SKILL: "rules",  # Skill flows into rules section
}

_SECTION_HEADINGS = {
    "facts": "## Relevant Facts",
    "experience": "## Relevant Experience",
    "beliefs": "## Applicable Beliefs",
    "rules": "## Governing Rules",
    "principles": "## Principles",
    "failures": "## Known Failures",
    "conflicts": "## Active Conflicts",
    "causal_provenance": "## Causal Provenance & Decision History",
    "archived_history": "## Archived History & Precedents",
    "reflection_audit": "## Memory Health Audit & Self-Reflection",
    "clarification_probes": "## Clarification Probes",
}


@dataclass
class CognitiveContext:
    task: str
    facts: list = field(default_factory=list)
    experience: list = field(default_factory=list)
    beliefs: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    principles: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    causal_provenance: list = field(default_factory=list)
    archived_history: list = field(default_factory=list)
    reflection_audit: list = field(default_factory=list)
    # 探颐（v0.38 Ticket 02）：主动求证追问插桩建议（被动切面，由调用方填充）
    clarification_probes: list = field(default_factory=list)
    # 卷宗（v0.42 Ticket 02 ADR-0063）：活动情境卷宗切面（包含案名、目标、里程碑状态与聚焦记忆）
    active_dossier: dict | None = None
    # 目次（v0.44 Ticket 02 ADR-0065）：多维记忆知识目录导览切面
    catalog_digest: dict | None = None
    # 起居注（v0.45 Ticket 02 ADR-0066）：时序演进全息纪事切面
    qijuzhu_digest: dict | None = None
    # 法门（v0.46 Ticket 02 ADR-0067）：程序性 SOP 技能切面
    famen_digest: list[dict] | None = None
    # 佐证（v0.47 Ticket 02 ADR-0068）：证据链反查与印证切面
    zuozheng_digest: list[dict] | None = None
    # 拾遗（v0.48 Ticket 02 ADR-0069）：遗漏自适应补救召回切面
    shiyi_digest: list[dict] | None = None
    # 校雠（v0.49 Ticket 02 ADR-0070）：智能语义去重与校勘切面
    jiaochou_digest: list[dict] | None = None
    # 拟议（v0.50 Ticket 02 ADR-0071）：认知演化提案审议与因果变化推演切面
    niyi_digest: list[dict] | None = None
    # 底本（v0.51 Ticket 02 ADR-0072）：跨会话现场定本与认知状态传承切面
    diben_digest: dict | None = None
    # 测候（v0.52 Ticket 02 ADR-0073）：影子观察期巡检与演化状态监控切面
    cehou_digest: list[dict] | None = None

    def to_prompt(self) -> str:
        """
        返回供 Agent 使用的结构化 Markdown 认知上下文，
        取代扁平化的 Memory 列表。
        """
        sections = []
        for section_key, heading in _SECTION_HEADINGS.items():
            items = getattr(self, section_key)
            if not items:
                sections.append(f"{heading}\n_（无）_")
                continue

            lines = [heading]
            for item in items:
                if isinstance(item, dict):
                    # 处理 causal_provenance 归因结构字典
                    if section_key == "causal_provenance" and "target_id" in item:
                        from lantai.llm.fence import wrap_as_data

                        target = item.get("target_id")
                        narrative = item.get("narrative", "")
                        lines.append(f"- [决策目标: {target}] {wrap_as_data(str(narrative))}")
                        tl = item.get("timeline", [])
                        for event in tl:
                            ts = event.get("timestamp") or "未知时间"
                            lines.append(f"  * [{ts}] {wrap_as_data(str(event.get('content')))}")
                        # 反事实替代路径支持（ADR-0061 / v0.40）
                        alt_paths = item.get("alternative_paths", [])
                        if alt_paths:
                            lines.append(f"  * 备用替代因果路径: {len(alt_paths)} 条")
                        continue

                    # 探颐（v0.38 Ticket 02）：求证探针专化渲染——只出问题文本，
                    # 不 repr 整个结构字典（正文照例过樊篱）
                    if section_key == "clarification_probes":
                        from lantai.llm.fence import wrap_as_data

                        question = item.get("question") or str(item)
                        lines.append(f"- {wrap_as_data(str(question))}")
                        continue

                    # 参商（v0.41 Ticket 02）：冲突违例专化渲染——经过樊篱包裹
                    if section_key == "conflicts" and "conflicting_memory_ids" in item:
                        from lantai.llm.fence import wrap_as_data

                        desc = item.get("description") or str(item)
                        severity = item.get("severity", "medium")
                        ids_str = ", ".join(item.get("conflicting_memory_ids", []))
                        lines.append(
                            f"- [参商抵触 ({severity}) 涉及: {ids_str}] {wrap_as_data(str(desc))}"
                        )
                        continue

                    content = item.get("content") or item.get("lesson") or str(item)
                    scope = item.get("scope", "")
                    conf = item.get("confidence", "")
                    at = item.get("event_time", "")  # 注入帧带时间（票据 inject-frame-time/01）
                    meta = ""
                    if conf:
                        meta += f" _(conf: {conf:.2f})_"
                    if scope:
                        meta += f" _(scope: {scope})_"
                    if at:
                        meta += f" _(at: {at})_"
                    # 樊篱（P0 票03）：记忆正文是数据不是指令
                    from lantai.llm.fence import wrap_as_data

                    lines.append(f"- {wrap_as_data(str(content), item_id=item.get('id'))}{meta}")
                else:
                    from lantai.llm.fence import wrap_as_data

                    if (
                        section_key == "conflicts"
                        and hasattr(item, "conflicting_memory_ids")
                        and hasattr(item, "description")
                    ):
                        desc = getattr(item, "description", "")
                        severity = getattr(item, "severity", "medium")
                        ids_str = ", ".join(getattr(item, "conflicting_memory_ids", []))
                        lines.append(
                            f"- [参商抵触 ({severity}) 涉及: {ids_str}] {wrap_as_data(str(desc))}"
                        )
                    else:
                        lines.append(f"- {wrap_as_data(str(item))}")
            sections.append("\n".join(lines))

        header = f"# 🧠 Cognitive Context\n**Task**: {self.task}\n"

        # 卷宗（ADR-0063）：若有活动卷宗，在顶部优先渲染卷宗情境切面，全面施加樊篱保护
        if self.active_dossier:
            from lantai.llm.fence import wrap_as_data

            dos = self.active_dossier
            d_title = dos.get("title") or dos.get("dossier_title") or "未命名卷宗"
            d_goal = dos.get("goal") or dos.get("dossier_goal") or ""
            d_milestones = dos.get("milestones") or []
            d_memories = dos.get("focused_memories") or []

            d_lines = [f"## Active Dossier & Strategic Goal: {wrap_as_data(str(d_title))}"]
            if d_goal:
                d_lines.append(f"- **Strategic Goal**: {wrap_as_data(str(d_goal))}")
            if d_milestones:
                d_lines.append("- **Milestones Progress**:")
                for m in d_milestones:
                    m_title = m.get("title", "")
                    m_status = m.get("status", "pending")
                    m_notes = m.get("notes", "")
                    note_str = f" - {wrap_as_data(str(m_notes))}" if m_notes else ""
                    d_lines.append(f"  * [{m_status}] {wrap_as_data(str(m_title))}{note_str}")
            if d_memories:
                d_lines.append("- **Focused Memories**:")
                for fm in d_memories:
                    c = fm.get("content") or str(fm)
                    d_lines.append(f"  * {wrap_as_data(str(c), item_id=fm.get('id'))}")

            sections.insert(0, "\n".join(d_lines))

        # 目次（ADR-0065）：若有目录大纲，渲染全局知识目次导航切面，全面施加樊篱保护
        if self.catalog_digest:
            from lantai.llm.fence import wrap_as_data

            cat = self.catalog_digest
            c_summary = cat.get("summary") or "知识目录概览"
            c_sections = cat.get("sections") or []

            c_lines = [f"## Memory Catalog & Knowledge Index: {wrap_as_data(str(c_summary))}"]
            if c_sections:
                c_lines.append("- **Knowledge Sections**:")
                for s in c_sections:
                    d = s.get("domain", "")
                    l = s.get("lane", "")
                    cnt = s.get("count", 0)
                    tags = s.get("tags", [])
                    tag_str = f" [标签: {', '.join(tags[:5])}]" if tags else ""
                    c_lines.append(f"  * {d}/{l}: {cnt} items{tag_str}")

            sections.insert(0, "\n".join(c_lines))

        # 起居注（ADR-0066）：若有日度全息纪事，置顶渲染时序脉络切面，正文经樊篱保护
        if self.qijuzhu_digest:
            from lantai.llm.fence import wrap_as_data

            qjz = self.qijuzhu_digest
            q_date = qjz.get("date") or "今日"
            q_summary = qjz.get("summary") or "时序全息纪事"
            q_events = qjz.get("chronicle_events") or qjz.get("events") or []

            q_lines = [
                f"## Recent Chronicle & Activity Digest (起居注 · {q_date}): {wrap_as_data(str(q_summary))}"
            ]
            if q_events:
                q_lines.append("- **Chronicle Events**:")
                for ev in q_events[:5]:
                    e_type = ev.get("event_type", "event")
                    e_title = ev.get("title", "")
                    e_detail = ev.get("detail", "")
                    detail_str = f" - {e_detail}" if e_detail else ""
                    q_lines.append(f"  * [{e_type}] {wrap_as_data(str(e_title))}{detail_str}")

            sections.insert(0, "\n".join(q_lines))

        # 法门（ADR-0067）：若有匹配的程序性 SOP 技能，在上下文渲染技能执行切面
        if self.famen_digest:
            from lantai.llm.fence import wrap_as_data

            fm_skills = self.famen_digest
            fm_lines = [f"## Applicable Procedural Skills & SOP (法门 · {len(fm_skills)}):"]
            for s in fm_skills[:3]:
                name = s.get("name") or "未命名法门"
                trigger = s.get("trigger_rule") or ""
                steps = s.get("steps") or []
                trigger_str = f" _(触发: {wrap_as_data(str(trigger))})_" if trigger else ""
                fm_lines.append(f"- **{wrap_as_data(str(name))}**{trigger_str}")
                for i, st in enumerate(steps[:5], 1):
                    fm_lines.append(f"  {i}. {wrap_as_data(str(st))}")

            sections.insert(0, "\n".join(fm_lines))

        # 佐证（ADR-0068）：若有支撑证据链或断言印证节点，渲染佐证切面
        if self.zuozheng_digest:
            from lantai.llm.fence import wrap_as_data

            zz_items = self.zuozheng_digest
            zz_lines = [f"## Supporting Evidence & Proof Chain (佐证 · {len(zz_items)}):"]
            for idx, item in enumerate(zz_items[:5], 1):
                c = item.get("content") or ""
                direction = item.get("support_direction") or "supports"
                conf = item.get("confidence")
                conf_str = f" [置信度: {conf}]" if conf is not None else ""
                reason = item.get("relation_reason") or ""
                reason_str = f" - {wrap_as_data(str(reason))}" if reason else ""
                zz_lines.append(
                    f"- [佐证·E{idx} ({direction}){conf_str}] {wrap_as_data(str(c), item_id=item.get('id'))}{reason_str}"
                )

            sections.insert(0, "\n".join(zz_lines))

        # 拾遗（ADR-0069）：若有遗漏自适应补救召回记忆，渲染补救切面
        if self.shiyi_digest:
            from lantai.llm.fence import wrap_as_data

            sy_items = self.shiyi_digest
            sy_lines = [f"## Compensated Recall & Remedied Memories (拾遗 · {len(sy_items)}):"]
            for idx, item in enumerate(sy_items[:5], 1):
                c = item.get("content") or ""
                tier = item.get("remedy_tier") or "fuzzy"
                conf = item.get("confidence")
                conf_str = f" [置信度: {conf}]" if conf is not None else ""
                reason = item.get("remedy_reason") or ""
                reason_str = f" - {wrap_as_data(str(reason))}" if reason else ""
                sy_lines.append(
                    f"- [拾遗·R{idx} ({tier}){conf_str}] {wrap_as_data(str(c), item_id=item.get('id'))}{reason_str}"
                )

            sections.insert(0, "\n".join(sy_lines))

        # 校雠（ADR-0070）：若有对齐订误与冗余校勘建议，渲染校雠切面
        if self.jiaochou_digest:
            from lantai.llm.fence import wrap_as_data

            jc_items = self.jiaochou_digest
            jc_lines = [f"## Semantic Alignment & Collation (校雠 · {len(jc_items)}):"]
            for idx, item in enumerate(jc_items[:5], 1):
                prop = item.get("synthesized_proposal") or item.get("text_b") or ""
                action = item.get("action_suggestion") or "merge"
                sim = item.get("similarity")
                sim_str = f" [相似度: {sim}]" if sim is not None else ""
                summary = item.get("divergence_summary") or ""
                summary_str = f" - {wrap_as_data(str(summary))}" if summary else ""
                jc_lines.append(
                    f"- [校雠·C{idx} ({action}){sim_str}] {wrap_as_data(str(prop))}{summary_str}"
                )

            sections.insert(0, "\n".join(jc_lines))

        # 拟议（ADR-0071）：若有认知演化提案审议结果，渲染拟议切面
        if self.niyi_digest:
            from lantai.llm.fence import wrap_as_data

            ny_items = self.niyi_digest
            ny_lines = [f"## Evolution Proposals & Deliberation (拟议 · {len(ny_items)}):"]
            for idx, item in enumerate(ny_items[:5], 1):
                ptype = item.get("proposal_type") or "change"
                risk = item.get("risk_level") or "low"
                edges_c = item.get("impacted_edges_count", 0)
                reason = item.get("deliberation_reason") or ""
                tid = item.get("target_memory_id")
                tid_str = f" [目标: {tid}]" if tid else ""
                reason_str = f" - {wrap_as_data(str(reason))}" if reason else ""
                ny_lines.append(
                    f"- [拟议·P{idx} ({ptype}·风险: {risk}·拓扑边: {edges_c}){tid_str}]{reason_str}"
                )

            sections.insert(0, "\n".join(ny_lines))

        # 底本（ADR-0072）：若有跨会话现场定本，渲染底本切面
        if self.diben_digest:
            from lantai.llm.fence import wrap_as_data

            db_data = self.diben_digest
            sid = db_data.get("session_id", "active")
            intent = db_data.get("active_intent", "")
            action = db_data.get("next_action", "")
            work = db_data.get("working_context", "")
            decisions = db_data.get("key_decisions") or []
            notes = db_data.get("open_notes") or []

            db_lines = [f"## Canonical Session Anchor (底本 · 会话 {sid}):"]
            if intent:
                db_lines.append(f"- **在做 (Intent)**: {wrap_as_data(str(intent))}")
            if action:
                db_lines.append(f"- **下一步 (Next Action)**: {wrap_as_data(str(action))}")
            if work:
                db_lines.append(f"- **工作区 (Working Context)**: {wrap_as_data(str(work))}")
            if decisions:
                db_lines.append(f"- **关键决策 (Decisions)**: {wrap_as_data('; '.join(decisions))}")
            if notes:
                db_lines.append(f"- **未竟待办 (Open Notes)**: {wrap_as_data('; '.join(notes))}")

            sections.insert(0, "\n".join(db_lines))

        # 测候（ADR-0073）：若有影子观察期视窗与实验评估，渲染测候切面
        if self.cehou_digest:
            from lantai.llm.fence import wrap_as_data

            ch_items = self.cehou_digest
            ch_lines = [f"## Shadow Observation & Graduation (测候 · {len(ch_items)}):"]
            for idx, item in enumerate(ch_items[:5], 1):
                wid = item.get("window_id", "")
                st = item.get("state", "observing")
                rate = item.get("success_rate", 0.0)
                summary = item.get("observation_summary") or ""
                ch_lines.append(
                    f"- [测候·W{idx} ({st}·胜率: {round(rate * 100, 1)}%) [视窗: {wid}]] - {wrap_as_data(str(summary))}"
                )

            sections.insert(0, "\n".join(ch_lines))

        from lantai.llm.fence import fence_declaration

        decl = fence_declaration()
        if decl:
            header += f"\n> {decl}\n"
        return header + "\n\n".join(sections)


class CognitiveContextBuilder:
    """
    从 DB 中按 CognitiveRole 分流拉取记忆，构建结构化认知上下文。

    归属收窄（票 .scratch/readside-gaps/03）：此前 `select(MemoryItem)`
    是全表，而 `content` 一字不漏、还带记忆 ULID `id`——A 调一次
    `/cognitive/context` 就拿到全库最敏感的正文，而这个端点的用途正是
    「把记忆喂给 Agent」，泄漏面直接是模型上下文。三个 handler
    （`/context`、`/context/prompt`、`/summary`）此前一个身份都不取。

    `principal=None` 仅限内部调用（`runtime/middleware.py` 的
    `build_cognitive_summary` 是环境式中间件、`cli/mcp.py` 的工具没有
    调用方身份），按 `"default"` 收敛。
    """

    def __init__(self, db: Session, *, principal=None):
        self.db = db
        self.principal = principal

    def _owned_memory_stmt(self):
        """带归属与状态条件的 MemoryItem 查询（票 .scratch/readside-gaps/03）。

        判据复用 `work_item_service._viewer_of`（同一仓里已有现成的，
        不另写一份——票 01 的既有决定），但**可见性口径与票 01/02 刻意
        分歧**：这里是 `user_id == viewer OR user_id IS NULL`。

        分歧的理由是部署事实，不是偏好：真实库 `memoryitem` 650 行里
        629 行 `user_id IS NULL`，4 把 API key 全是 `user_id='default'`
        ——单人部署。照票 01 判「NULL 一律不可见」会让本端点对唯一的真实
        用户返回空上下文，Agent 直接失明；那是把功能修废，不是收窄泄漏。
        NULL 是「未记录」的事实状态，不是「属于所有人」——同
        `memory_service.get_core_memory` 那一支的口径。

        status 过滤（票 review-fixes-2026-10/06）：与全库读侧口径对齐，
        只注入 `status == "active"`——撤回/归档/已巩固的主张不得再进
        Agent 提示词（同 `hybrid.py` 读侧三处的无条件 active 过滤，
        admin 也不例外：retracted 对 admin 同样是"已停止使用"）。
        归档例外白名单若将来有产品诉求，须开票明列。

        admin/system 全权（不走本函数的归属半边）。
        """
        from lantai.services.work_item_service import _viewer_of

        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return stmt
        viewer = _viewer_of(principal)
        return stmt.where((MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None)))

    @staticmethod
    def _task_relevance(content: str, task: str) -> float:
        """计算内容与任务的词汇重叠相关度（Jaccard 系数）。"""
        task_words = set(task.lower().split())
        content_words = set(content.lower().split())
        if not task_words and not content_words:
            return 0.0
        return len(task_words & content_words) / len(task_words | content_words)

    def build(self, task: str, top_k: int = 12) -> CognitiveContext:
        ctx = CognitiveContext(task=task)

        # 1. 拉取归属范围内的 MemoryItem，按综合分（task_relevance + confidence）排序
        memories = self.db.exec(self._owned_memory_stmt()).all()

        def _score(mem: MemoryItem) -> float:
            relevance = self._task_relevance(mem.content or "", task)
            confidence = mem.confidence if mem.confidence is not None else 0.0
            return 0.6 * relevance + 0.4 * confidence

        memories_sorted = sorted(memories, key=_score, reverse=True)

        # 2. 按 role 分流到各切面，每切面最多 top_k 条
        for mem in memories_sorted:
            section_key = _ROLE_TO_SECTION.get(mem.role)
            if section_key is None:
                continue

            target: list = getattr(ctx, section_key)
            if len(target) >= top_k:
                continue

            record = {
                "id": mem.id,
                "content": mem.content,
                "confidence": mem.confidence,
                "role": mem.role,
            }
            # 注入帧带时间（票据 .scratch/inject-frame-time/01）：双轴优先
            # event_time，回落 valid_from；渲染走 core.time.render_event_time
            # 单一真源（粒度 SHELL_HOOK_INJECT_DATE，off 时空串不加 meta）。
            from lantai.core.time import render_event_time

            time_str = render_event_time(mem, settings.SHELL_HOOK_INJECT_DATE)
            if time_str:
                record["event_time"] = time_str
            if mem.role == CognitiveRole.PRINCIPLE and isinstance(mem.structure, dict):
                scope = mem.structure.get("scope", {})
                record["scope"] = scope.get("domain", "")

            target.append(record)

        # 3. 拉取 FailureRecord
        failures = self.db.exec(select(FailureRecord)).all()
        for f in failures[:top_k]:
            ctx.failures.append(
                {
                    "id": f.id,
                    "task": f.task,
                    "action": f.action,
                    "content": f.lesson or f.actual,
                    "severity": f.severity,
                }
            )

        # 4. conflicts 预留（后续接入 ConflictEngine 实时检测）
        ctx.conflicts = []

        return ctx
