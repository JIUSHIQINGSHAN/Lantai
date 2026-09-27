from dataclasses import dataclass, field

from sqlmodel import Session, select

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
                    content = item.get("content") or item.get("lesson") or str(item)
                    scope = item.get("scope", "")
                    conf = item.get("confidence", "")
                    meta = ""
                    if conf:
                        meta += f" _(conf: {conf:.2f})_"
                    if scope:
                        meta += f" _(scope: {scope})_"
                    # 樊篱（P0 票03）：记忆正文是数据不是指令
                    from lantai.llm.fence import wrap_as_data

                    lines.append(f"- {wrap_as_data(str(content), item_id=item.get('id'))}{meta}")
                else:
                    from lantai.llm.fence import wrap_as_data

                    lines.append(f"- {wrap_as_data(str(item))}")
            sections.append("\n".join(lines))

        header = f"# 🧠 Cognitive Context\n**Task**: {self.task}\n"
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
        """带归属条件的 MemoryItem 查询（票 .scratch/readside-gaps/03）。

        判据复用 `work_item_service._viewer_of`（同一仓里已有现成的，
        不另写一份——票 01 的既有决定），但**可见性口径与票 01/02 刻意
        分歧**：这里是 `user_id == viewer OR user_id IS NULL`。

        分歧的理由是部署事实，不是偏好：真实库 `memoryitem` 650 行里
        629 行 `user_id IS NULL`，4 把 API key 全是 `user_id='default'`
        ——单人部署。照票 01 判「NULL 一律不可见」会让本端点对唯一的真实
        用户返回空上下文，Agent 直接失明；那是把功能修废，不是收窄泄漏。
        NULL 是「未记录」的事实状态，不是「属于所有人」——同
        `memory_service.get_core_memory` 那一支的口径。

        admin/system 全权（不走本函数）。
        """
        from lantai.services.work_item_service import _viewer_of

        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return select(MemoryItem)
        viewer = _viewer_of(principal)
        return select(MemoryItem).where(
            (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
        )

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
