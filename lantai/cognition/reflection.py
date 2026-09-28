from collections import Counter
from dataclasses import dataclass, field

from sqlmodel import Session, select

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import CognitivePattern, CognitiveRole, FailureRecord, MemoryItem


@dataclass
class ReflectionReport:
    new_patterns: int = 0
    failure_patterns: int = 0
    belief_candidates: int = 0
    rule_candidates: int = 0
    rules_weakened: int = 0
    principles_under_review: int = 0
    contradictions: int = 0
    failures: int = 0
    proposed_patterns: list = field(default_factory=list)
    failure_belief_candidates: list = field(default_factory=list)
    summary: str = ""
    # 归属（票 `.scratch/cognitive-write-gaps/01`）：`FailureRecord` 没有
    # 归属列，`failures` 计数无法按人收窄。**如实标注而不静默**——调用方
    # （面板 / 报告）看到 `false` 就知道这个数跨用户，不会误当成自己的。
    failures_scoped: bool = True


def _stamp_owner(row, principal) -> None:
    """给反思晋升出来的行打归属（票 `.scratch/cognitive-write-gaps/01`）。

    修前 `db.add(b)` 落的 Belief/Rule 候选**一个 `user_id` 都不带**——
    又一批 ownerless 行，而 NULL 在本仓的口径是"人人可见"。

    口径同 `obsidian_service.py:158`：带 `"default"` 兜底，`principal=None`
    （内部调用 / 脚本）时三列留 NULL——与改动前逐字一致，内部工具不被修废。
    """
    if principal is None:
        return
    row.user_id = getattr(principal, "user_id", None) or "default"
    row.tenant_id = getattr(principal, "tenant_id", None)
    row.agent_id = getattr(principal, "agent_id", None)


def _reflection_scope(column, principal):
    """反思查询的归属过滤条件（票 `.scratch/cognitive-write-gaps/01`）。

    口径同票 04 的 `_digest_scope`：`user_id == viewer OR IS NULL`。
    NULL 老行**可见**——认知表里大量历史行没有属主（单人部署下判不可见
    等于反思空转）。

    `principal=None`（内部 worker / 脚本 / MCP）与 admin 全量：前者定时
    任务不带身份，一过滤就空转，那是把内部工具修废。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    return (column == viewer) | (column.is_(None))


class ReflectionEngine:
    """
    每次 Reflection 运行执行以下步骤：
    1. 统计最近的 FailureRecord，从中归纳 failure_pattern（v0.3 新增）
    2. 查找重复的 Observation 并归纳为 Pattern 候选
    3. 检查 Belief 是否有反证（置信度下降）
    4. 检查 Rule 是否有违反
    5. 晋升 Pattern→Belief，Belief→Rule（failure_pattern 用低阈值 0.55）
    """

    REPETITION_THRESHOLD = 2  # 达到此次数才视为"重复模式"
    FAILURE_PROMOTION_THRESHOLD = 0.55  # failure_pattern 低阈值（单次失败即预警）

    def __init__(self, db: Session, *, principal=None):
        """归属（票 `.scratch/cognitive-write-gaps/01`）：`principal=None`
        （内部调用 / 脚本）与 admin 全量；其余四个查询按 viewer 收窄。
        """
        self.db = db
        self.principal = principal

    def _failures_to_observations(self, failures: list[FailureRecord]) -> list[MemoryItem]:
        """
        将 FailureRecord 的 lesson/cause 字段转化为临时 OBSERVATION，
        供 detect_patterns 归纳 failure_pattern。
        临时对象不写 DB，仅用于模式检测输入。
        """
        obs = []
        for f in failures:
            if f.lesson and f.lesson.strip():
                obs.append(
                    MemoryItem(
                        id=f"lesson_{f.id}",
                        content=f.lesson.strip(),
                        role=CognitiveRole.OBSERVATION,
                        source_ids=[f.id],
                        status="active",
                    )
                )
            if f.cause and f.cause.strip():
                obs.append(
                    MemoryItem(
                        id=f"cause_{f.id}",
                        content=f.cause.strip(),
                        role=CognitiveRole.OBSERVATION,
                        source_ids=[f.id],
                        status="active",
                    )
                )
        return obs

    def run_reflection(self) -> ReflectionReport:
        report = ReflectionReport()

        # 归属（票 .scratch/cognitive-write-gaps/01）：FailureRecord 无归属列，
        # 这一项**无法**按人收窄——如实标注，不发明归属。
        report.failures_scoped = False

        # 步骤 1：统计失败记录，并从 lesson/cause 归纳 failure_pattern
        failures = self.db.exec(select(FailureRecord)).all()
        report.failures = len(failures)

        failure_belief_candidates: list[MemoryItem] = []
        if failures:
            failure_obs = self._failures_to_observations(failures)
            if failure_obs:
                from lantai.cognition.evolution import EvolutionEngine

                evo = EvolutionEngine(self.db)
                # failure_pattern 不写入 DB（内存聚类），仅用于晋升
                failure_patterns = evo.detect_patterns(failure_obs)
                report.failure_patterns = len(failure_patterns)

                # 标记为 failure_pattern 类型
                for pat in failure_patterns:
                    pat.pattern_type = "failure_pattern"

                # 低阈值晋升：0.55
                if failure_patterns:
                    fb_cands = evo.propose_beliefs(
                        failure_patterns,
                        promotion_threshold=self.FAILURE_PROMOTION_THRESHOLD,
                    )
                    for b in fb_cands:
                        # 覆盖 promoted_from 标注来源
                        if b.promotion_trace:
                            b.promotion_trace["promoted_from"] = "failure_pattern"
                        _stamp_owner(b, self.principal)
                        self.db.add(b)
                    failure_belief_candidates = fb_cands
                    report.failure_belief_candidates = fb_cands

        # 步骤 2：查找重复 Observation，归纳 Pattern 候选（复用 EvolutionEngine 词袋与语义签名聚类）
        # 归属（票 01）：按 viewer 收窄，否则 A 与 B 的观测会被**一起聚类**
        obs_scope = _reflection_scope(MemoryItem.user_id, self.principal)
        obs_query = select(MemoryItem).where(MemoryItem.role == CognitiveRole.OBSERVATION)
        if obs_scope is not None:
            obs_query = obs_query.where(obs_scope)
        observations = self.db.exec(obs_query).all()

        from lantai.cognition.evolution import EvolutionEngine

        evo = EvolutionEngine(self.db)
        patterns = evo.detect_patterns(observations)
        report.proposed_patterns = patterns
        report.new_patterns = len(patterns)

        # 步骤 3：检查 Belief 是否置信度过低（反证衰减导致）
        belief_scope = _reflection_scope(MemoryItem.user_id, self.principal)
        belief_query = select(MemoryItem).where(MemoryItem.role == CognitiveRole.BELIEF)
        if belief_scope is not None:
            belief_query = belief_query.where(belief_scope)
        beliefs = self.db.exec(belief_query).all()
        for b in beliefs:
            if b.confidence < 0.4:
                report.principles_under_review += 1

        # 步骤 4：检查 Rule 是否置信度下降
        rule_scope = _reflection_scope(MemoryItem.user_id, self.principal)
        rule_query = select(MemoryItem).where(MemoryItem.role == CognitiveRole.RULE)
        if rule_scope is not None:
            rule_query = rule_query.where(rule_scope)
        rules = self.db.exec(rule_query).all()
        for r in rules:
            if r.confidence < 0.5:
                report.rules_weakened += 1

        # 步骤 5：使用 EvolutionEngine 从发现的 Pattern 候选晋升 Belief 候选，从 Belief 晋升 Rule 候选
        if report.proposed_patterns:
            from lantai.cognition.evolution import EvolutionEngine

            evo = EvolutionEngine(self.db)
            b_cands = evo.propose_beliefs(report.proposed_patterns)
            report.belief_candidates = len(b_cands)
            for b in b_cands:
                _stamp_owner(b, self.principal)
                self.db.add(b)

            if beliefs:
                r_cands = evo.propose_rules(beliefs)
                report.rule_candidates = len(r_cands)
                for r in r_cands:
                    _stamp_owner(r, self.principal)
                    self.db.add(r)

        if report.new_patterns > 0 or report.failures > 0 or report.failure_patterns > 0:
            report.summary = (
                f"Reflection completed: {report.new_patterns} new pattern(s) detected, "
                f"{report.failure_patterns} failure pattern(s), "
                f"{len(failure_belief_candidates)} failure belief candidate(s), "
                f"{report.belief_candidates} belief candidate(s), "
                f"{report.failures} failure(s) on record, "
                f"{report.rules_weakened} rule(s) weakened."
            )
        else:
            report.summary = "Reflection completed: no significant changes detected."

        self.db.commit()
        return report
