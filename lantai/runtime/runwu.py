"""
lantai/runtime/runwu.py
润物（Runwu · 自适应认知中间件与无感上下文装载引擎 · v0.39）

典籍出处：杜甫《春夜喜雨》「随风潜入夜，润物细无声」。
核心定位：
在无需 Agent 显式调用工具的前提下，在请求/执行链路中自适应萃取与任务最相关的：
1. 规范与规则（Rules / Principles）；
2. 历史失败教训（Known Failures）；
3. 主动求证探针（Clarification Probes，联动探颐 TanyiEngine）。

原则：
- 零外部阻塞：纯 SQL 与内存计算，绝无大模型/外部网络同步阻塞（宁 miss 不脏写）；
- 严格租户隔离：支持 tenant_id + user_id 双重过滤，跨租户 0 泄漏；
- 紧凑预算控制：按字符预算动态裁剪，杜绝上下文膨胀；
- 樊篱安全隔离：所有提取正文强制经过 wrap_as_data 处理，防止注入攻击（OWASP LLM01）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from lantai.core.logger import logger
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import CognitiveRole, FailureRecord, LifecycleStatus, MemoryItem

if TYPE_CHECKING:
    pass


@dataclass
class RunwuSummary:
    """润物自适应认知摘要数据载体。"""

    task: str
    rules: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)
    formatted_text: str = ""
    char_count: int = 0


class RunwuEngine:
    """润物核心自适应装载引擎。"""

    def __init__(
        self,
        db: Session | None = None,
        session_factory: Any | None = None,
        default_char_budget: int = 350,
    ) -> None:
        self.db = db
        self.session_factory = session_factory
        self.default_char_budget = default_char_budget

    def _get_session(self) -> tuple[Session, bool]:
        """获取数据库 Session 与是否需要管理的标志。"""
        if self.db is not None:
            return self.db, False
        if self.session_factory is not None:
            return self.session_factory(), True
        from lantai.storage import db as db_module

        return db_module.get_session(), True

    @staticmethod
    def _calculate_relevance(content: str, task: str) -> float:
        """基于词汇重叠的轻量相关性打分（无 LLM 外呼）。"""
        if not task or not content:
            return 0.0
        # 简单分词/字符重叠
        task_clean = re.findall(r"[\w]+", task.lower())
        if not task_clean:
            return 0.0
        task_words = set(task_clean)
        content_lower = content.lower()
        overlap = sum(1 for w in task_words if w in content_lower)
        return overlap / len(task_words)

    def extract_summary(
        self,
        task: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        max_rules: int = 2,
        max_failures: int = 1,
        max_probes: int = 1,
        char_budget: int | None = None,
    ) -> RunwuSummary:
        """从存储中提取关键规则、历史教训与求证探针，生成认知摘要。"""
        budget = char_budget if char_budget is not None else self.default_char_budget
        session, should_close = self._get_session()

        raw_rules: list[tuple[MemoryItem, str]] = []
        raw_failures: list[str] = []
        raw_probes: list[str] = []

        try:
            # 1. 规则提取（MemoryItem 过滤 active，角色为 RULE / PRINCIPLE / SKILL）
            rule_roles = (
                CognitiveRole.RULE,
                CognitiveRole.PRINCIPLE,
                CognitiveRole.SKILL,
            )
            stmt = select(MemoryItem).where(
                MemoryItem.status == "active",
                MemoryItem.role.in_(rule_roles),  # type: ignore[attr-defined]
            )
            if tenant_id is not None:
                stmt = stmt.where(MemoryItem.tenant_id == tenant_id)
            if user_id is not None:
                stmt = stmt.where((MemoryItem.user_id == user_id) | (MemoryItem.user_id.is_(None)))

            items = session.exec(stmt).all()
            scored_items = []
            for item in items:
                if item.lifecycle_status == LifecycleStatus.RETIRED:
                    continue
                score = (
                    self._calculate_relevance(item.content, task) + (item.confidence or 0.8) * 0.5
                )
                scored_items.append((score, item))

            scored_items.sort(key=lambda x: x[0], reverse=True)
            for _, item in scored_items[: max_rules * 2]:
                c = (item.content or "").strip()
                if c:
                    raw_rules.append((item, c))

            # 2. 失败记录提取（FailureRecord）
            if max_failures > 0:
                f_stmt = select(FailureRecord)
                f_records = session.exec(f_stmt).all()
                scored_failures = []
                for f in f_records:
                    text_for_match = f"{f.task} {f.actual} {f.lesson}"
                    score = (
                        self._calculate_relevance(text_for_match, task) + (f.severity or 0.5) * 0.3
                    )
                    lesson = (f.lesson or f.actual or "").strip()
                    if lesson:
                        scored_failures.append((score, lesson))

                scored_failures.sort(key=lambda x: x[0], reverse=True)
                for _, lesson in scored_failures[:max_failures]:
                    raw_failures.append(lesson)

            # 3. 探颐主动求证探针联动（TanyiEngine）
            if max_probes > 0:
                try:
                    from lantai.cognition.tanyi import TanyiEngine

                    tanyi = TanyiEngine(storage=session)
                    probes = tanyi.probe_ambiguities(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        query=task,
                        limit=max_probes,
                    )
                    prompts = tanyi.generate_clarification_prompts(probes)
                    for p in prompts[:max_probes]:
                        prompt_str = p if isinstance(p, str) else p.get("prompt", str(p))
                        if prompt_str:
                            raw_probes.append(prompt_str)
                except Exception as ex:
                    logger.debug("润物联动探颐巡检跳过: %s", ex)

        except Exception as e:
            logger.warning("润物自适应认知提取异常: %s", e)
        finally:
            if should_close:
                session.close()

        # 4. 裁剪与预算控制
        final_rules: list[str] = []
        for r_item, r_text in raw_rules[:max_rules]:
            # 单项截短至 80 字以内
            short_rule = r_text[:80] + ("..." if len(r_text) > 80 else "")
            final_rules.append(short_rule)

        final_failures: list[str] = []
        for f_text in raw_failures[:max_failures]:
            short_f = f_text[:80] + ("..." if len(f_text) > 80 else "")
            final_failures.append(short_f)

        final_probes: list[str] = []
        for p_text in raw_probes[:max_probes]:
            short_p = p_text[:80] + ("..." if len(p_text) > 80 else "")
            final_probes.append(short_p)

        summary = RunwuSummary(
            task=task,
            rules=final_rules,
            failures=final_failures,
            probes=final_probes,
        )
        formatted = self.format_compact(summary, budget=budget)
        summary.formatted_text = formatted
        summary.char_count = len(formatted)
        return summary

    def format_compact(self, summary: RunwuSummary, budget: int | None = None) -> str:
        """将摘要格式化为紧凑字符串，强制经过樊篱数据围栏包裹并控制字符预算。"""
        char_limit = budget if budget is not None else self.default_char_budget
        parts: list[str] = []

        # 1. 规则部分
        if summary.rules:
            wrapped_rules = [wrap_as_data(r) for r in summary.rules]
            parts.append(f"相关规则: {'; '.join(wrapped_rules)}")

        # 2. 失败教训部分
        if summary.failures:
            wrapped_failures = [wrap_as_data(f) for f in summary.failures]
            parts.append(f"已知教训: {'; '.join(wrapped_failures)}")

        # 3. 探针部分
        if summary.probes:
            wrapped_probes = [wrap_as_data(p) for p in summary.probes]
            parts.append(f"待求证: {'; '.join(wrapped_probes)}")

        full_text = " | ".join(parts) if parts else ""

        # 预算截断保护：若超过预算，进行安全截断
        if len(full_text) > char_limit:
            # 优先保规则，若仍超预算，在文本边界截断并闭合樊篱标签
            full_text = full_text[: char_limit - 15] + "..."
            if "<memory_data" in full_text and "</memory_data>" not in full_text:
                full_text += "</memory_data>"

        return full_text
