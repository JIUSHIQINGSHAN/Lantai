from dataclasses import dataclass, field
from sqlmodel import Session, select

from lantai.models.tables import MemoryItem, CognitiveRole, FailureRecord


_ROLE_TO_SECTION = {
    CognitiveRole.OBSERVATION:  "facts",
    CognitiveRole.EXPERIENCE:   "experience",
    CognitiveRole.BELIEF:       "beliefs",
    CognitiveRole.RULE:         "rules",
    CognitiveRole.PRINCIPLE:    "principles",
    CognitiveRole.SKILL:        "rules",   # Skill flows into rules section
}

_SECTION_HEADINGS = {
    "facts":      "## Relevant Facts",
    "experience": "## Relevant Experience",
    "beliefs":    "## Applicable Beliefs",
    "rules":      "## Governing Rules",
    "principles": "## Principles",
    "failures":   "## Known Failures",
    "conflicts":  "## Active Conflicts",
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
                    lines.append(f"- {content}{meta}")
                else:
                    lines.append(f"- {item}")
            sections.append("\n".join(lines))

        header = f"# 🧠 Cognitive Context\n**Task**: {self.task}\n"
        return header + "\n\n".join(sections)


class CognitiveContextBuilder:
    """
    从 DB 中按 CognitiveRole 分流拉取记忆，构建结构化认知上下文。
    """

    def __init__(self, db: Session):
        self.db = db

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

        # 1. 拉取所有 MemoryItem，按综合分（task_relevance + confidence）全局排序
        memories = self.db.exec(select(MemoryItem)).all()

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
            ctx.failures.append({
                "id": f.id,
                "task": f.task,
                "action": f.action,
                "content": f.lesson or f.actual,
                "severity": f.severity,
            })

        # 4. conflicts 预留（后续接入 ConflictEngine 实时检测）
        ctx.conflicts = []

        return ctx

