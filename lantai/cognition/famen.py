"""法门（Famen）程序性技能资产与执行流水线引擎 (ADR-0067)。

出处：《法华经》「深入法门，通达诸法」；喻指可循之法、可行之门与标准作业规程（SOP）。
职责：
1. 聚合全库程序性规则（MemoryItem procedural steps）与技能结晶（SkillCrystal）；
2. 提取标准 SOP 结构（名称、触发条件、步骤列表、参数插槽）；
3. 基于任务意图与关键词相关度执行动态触发规则匹配；
4. 步骤明细强制使用樊篱（wrap_as_data）防注入围栏包裹；
5. 落实租户、属主与公共共享范围行级 ACL 隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem, SkillCrystal
from lantai.storage import db


@dataclass
class SkillAsset:
    """归一化的法门程序性技能资产。"""

    id: str
    name: str
    trigger_rule: str
    procedure: str
    steps: list[str] = field(default_factory=list)
    source_type: str = "crystal"  # crystal | procedural_memory
    confidence: float = 1.0
    shared_scope: str = "private"
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = False) -> dict[str, Any]:
        """序列化为字典，支持樊篱安全包裹。"""
        if fenced:
            fenced_steps = [wrap_as_data(s, item_id=self.id) for s in self.steps]
            fenced_proc = wrap_as_data(self.procedure, item_id=self.id)
            fenced_trigger = wrap_as_data(self.trigger_rule, item_id=self.id)
            return {
                "id": self.id,
                "name": self.name,
                "trigger_rule": fenced_trigger,
                "procedure": self.procedure,
                "fenced_procedure": fenced_proc,
                "steps": fenced_steps,
                "source_type": self.source_type,
                "confidence": self.confidence,
                "shared_scope": self.shared_scope,
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "metadata": self.metadata,
            }
        return {
            "id": self.id,
            "name": self.name,
            "trigger_rule": self.trigger_rule,
            "procedure": self.procedure,
            "steps": self.steps,
            "source_type": self.source_type,
            "confidence": self.confidence,
            "shared_scope": self.shared_scope,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


def _extract_steps(procedure: str) -> list[str]:
    """从文本流程中提取清晰的编号/列表步骤。"""
    if not procedure:
        return []
    lines = procedure.strip().splitlines()
    steps: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # 匹配 - 步骤1: xxx / 1. xxx / - xxx / * xxx
        cleaned = re.sub(r"^([-\*]|\d+\.|\d+、|步骤\d+[:：]?)\s*", "", s).strip()
        if cleaned:
            steps.append(cleaned)
    return steps if steps else [procedure.strip()]


class FamenEngine:
    """法门（Famen）程序性技能资产与执行流水线引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
        shared_scope: str = "private",
    ) -> bool:
        """行级安全隔离校验（宁 miss 不脏写）。

        - admin 全权放行；
        - shared_scope == 'swarm_shared' 全局公共共享放行；
        - 租户匹配且属主匹配放行。
        """
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

        # 公共结晶开放给集群伙伴
        if shared_scope == "swarm_shared":
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    def _collect_all_skills(self, s: Session) -> list[SkillAsset]:
        """从 DB 中聚合 SkillCrystal 与 MemoryItem procedural 步骤。"""
        assets: list[SkillAsset] = []

        # 1. 扫描已审核/已晋升的 SkillCrystal
        crystals = s.exec(
            select(SkillCrystal).where(SkillCrystal.status.in_(("approved", "promoted")))
        ).all()
        for sc in crystals:
            if not self._check_access(
                owner_id=sc.user_id,
                tenant_id=sc.tenant_id,
                shared_scope=sc.shared_scope,
            ):
                continue
            steps = _extract_steps(sc.procedure)
            assets.append(
                SkillAsset(
                    id=sc.id,
                    name=sc.skill_name,
                    trigger_rule=sc.trigger_rule or f"适用任务关键词: {sc.skill_name}",
                    procedure=sc.procedure,
                    steps=steps,
                    source_type="crystal",
                    confidence=1.0,
                    shared_scope=sc.shared_scope,
                    user_id=sc.user_id,
                    tenant_id=sc.tenant_id,
                    created_at=sc.created_at,
                    metadata={
                        "hit_count": sc.hit_count,
                        "success_count": sc.success_count,
                    },
                )
            )

        # 2. 扫描 procedural 或带 structure.steps 的 MemoryItem
        memories = s.exec(
            select(MemoryItem).where(
                MemoryItem.status == "active",
                (MemoryItem.decay_class == "procedural") | (MemoryItem.lane.in_(("rule", "skill"))),
            )
        ).all()
        for m in memories:
            if not self._check_access(
                owner_id=m.user_id,
                tenant_id=m.tenant_id,
                shared_scope="private",
            ):
                continue
            struct = m.structure if isinstance(m.structure, dict) else {}
            raw_steps = struct.get("steps") or []
            if isinstance(raw_steps, list) and raw_steps:
                steps = [str(x) for x in raw_steps]
            else:
                steps = _extract_steps(m.content)

            skill_name = struct.get("name") or (m.key or f"sop-{m.id[:8]}")
            assets.append(
                SkillAsset(
                    id=m.id,
                    name=skill_name,
                    trigger_rule=m.content[:100],
                    procedure=m.content,
                    steps=steps,
                    source_type="procedural_memory",
                    confidence=m.confidence if m.confidence is not None else 0.9,
                    shared_scope="private",
                    user_id=m.user_id,
                    tenant_id=m.tenant_id,
                    created_at=m.created_at,
                    metadata={"lane": m.lane, "decay_class": m.decay_class},
                )
            )

        return assets

    def list_skills(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取当前租户权限范围内的法门列表。"""
        if self.session is not None:
            assets = self._collect_all_skills(self.session)
            return [a.to_dict(fenced=False) for a in assets[:limit]]

        with db.get_session() as s:
            assets = self._collect_all_skills(s)
            return [a.to_dict(fenced=False) for a in assets[:limit]]

    def get_skill_detail(self, skill_id: str) -> dict[str, Any] | None:
        """获取单个法门的详细 SOP，带樊篱保护包裹。"""

        def _find(s: Session) -> dict[str, Any] | None:
            # 1. 尝试找 SkillCrystal
            sc = s.exec(select(SkillCrystal).where(SkillCrystal.id == skill_id)).first()
            if sc:
                if not self._check_access(
                    owner_id=sc.user_id,
                    tenant_id=sc.tenant_id,
                    shared_scope=sc.shared_scope,
                ):
                    return None
                steps = _extract_steps(sc.procedure)
                asset = SkillAsset(
                    id=sc.id,
                    name=sc.skill_name,
                    trigger_rule=sc.trigger_rule or f"适用任务关键词: {sc.skill_name}",
                    procedure=sc.procedure,
                    steps=steps,
                    source_type="crystal",
                    confidence=1.0,
                    shared_scope=sc.shared_scope,
                    user_id=sc.user_id,
                    tenant_id=sc.tenant_id,
                    created_at=sc.created_at,
                    metadata={
                        "hit_count": sc.hit_count,
                        "success_count": sc.success_count,
                    },
                )
                return asset.to_dict(fenced=True)

            # 2. 尝试找 MemoryItem
            m = s.exec(select(MemoryItem).where(MemoryItem.id == skill_id)).first()
            if m:
                if not self._check_access(
                    owner_id=m.user_id,
                    tenant_id=m.tenant_id,
                    shared_scope="private",
                ):
                    return None
                struct = m.structure if isinstance(m.structure, dict) else {}
                raw_steps = struct.get("steps") or []
                if isinstance(raw_steps, list) and raw_steps:
                    steps = [str(x) for x in raw_steps]
                else:
                    steps = _extract_steps(m.content)
                skill_name = struct.get("name") or (m.key or f"sop-{m.id[:8]}")
                asset = SkillAsset(
                    id=m.id,
                    name=skill_name,
                    trigger_rule=m.content[:100],
                    procedure=m.content,
                    steps=steps,
                    source_type="procedural_memory",
                    confidence=m.confidence if m.confidence is not None else 0.9,
                    shared_scope="private",
                    user_id=m.user_id,
                    tenant_id=m.tenant_id,
                    created_at=m.created_at,
                    metadata={"lane": m.lane, "decay_class": m.decay_class},
                )
                return asset.to_dict(fenced=True)

            return None

        if self.session is not None:
            return _find(self.session)
        with db.get_session() as s:
            return _find(s)

    def match_skills(self, task: str, limit: int = 5) -> list[dict[str, Any]]:
        """根据任务描述与触发条件进行相关度匹配。"""
        if not task:
            return []

        def _score(asset: SkillAsset) -> float:
            score = 0.0
            task_lower = task.lower()
            name_lower = asset.name.lower()
            trigger_lower = asset.trigger_rule.lower()

            # 1. 显式名字匹配
            if name_lower in task_lower or task_lower in name_lower:
                score += 5.0

            # 2. 词元重叠匹配
            task_tokens = set(re.findall(r"\w+", task_lower))
            trigger_tokens = set(re.findall(r"\w+", trigger_lower))
            name_tokens = set(re.findall(r"\w+", name_lower))

            common_trigger = task_tokens & trigger_tokens
            common_name = task_tokens & name_tokens

            score += len(common_name) * 2.0
            score += len(common_trigger) * 1.5

            # 3. 步骤上下文奖励
            for step in asset.steps:
                step_tokens = set(re.findall(r"\w+", step.lower()))
                score += len(task_tokens & step_tokens) * 0.5

            return score

        if self.session is not None:
            all_assets = self._collect_all_skills(self.session)
        else:
            with db.get_session() as s:
                all_assets = self._collect_all_skills(s)

        scored: list[tuple[float, SkillAsset]] = []
        for a in all_assets:
            s = _score(a)
            if s > 0:
                scored.append((s, a))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for s, a in scored[:limit]:
            d = a.to_dict(fenced=True)
            d["score"] = round(s, 2)
            results.append(d)

        return results
