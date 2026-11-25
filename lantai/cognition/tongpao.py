"""同袍（Tongpao）多智能体记忆联邦统一编排层。

该模块把已有的 `shared_scope`、技能结晶和测候能力收敛为一条可验证的
业务边界：私有工作区只允许同一用户/Agent 读取；公共典籍必须同时满足租户
与联邦边界；技能结晶先进入影子观察，再经过跨 Agent 复现评考，达标后才会
发布为公共 `MemoryItem`，并留下审核流痕与 `leads_to` 图谱边。

设计原则：
- 宁 miss 不脏写：身份、作用域、步骤和阈值先验后写；
- 不替换现有 SQLite/FTS/Chroma 底座，晋升复用 `create_skill`；
- 读侧再做一次 ACL 终闸，不依赖上游调用方已经过滤。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, false, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, select

from lantai.core.acl import Principal
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import (
    MemoryEdge,
    MemoryItem,
    SkillCrystal,
    SkillPromotionAudit,
    SwarmKnowledgeEvent,
)
from lantai.security.injection_guard import assert_no_prompt_injection
from lantai.storage import db

PRIVATE_SCOPE = "private"
SHARED_SCOPE = "swarm_shared"
VALID_SCOPES = frozenset({PRIVATE_SCOPE, SHARED_SCOPE})


@dataclass(frozen=True)
class CrystalDraft:
    """从多次成功/失败执行记录提取出的待观察技能结晶。"""

    skill_name: str
    trigger_rule: str
    steps: list[str]
    hit_count: int
    success_count: int
    agent_ids: list[str] = field(default_factory=list)

    @property
    def procedure(self) -> str:
        return "\n".join(f"- {step}" for step in self.steps)


class TongpaoEngine:
    """同袍联邦存储、影子评考、晋升与增量分发引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None) -> None:
        self.session = session
        self.principal = principal

    @staticmethod
    def extract_crystals(observations: list[dict[str, Any]]) -> list[CrystalDraft]:
        """从执行观测中按技能名提取结晶，不调用 LLM，也不产生写副作用。

        观测数据命中獬豸时整条观测跳过；空技能名或空步骤同样不生成候选，
        避免把不完整或可执行注入带入影子评考。
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            skill_name = str(observation.get("skill_name", "")).strip()
            raw_steps = observation.get("steps", [])
            if isinstance(raw_steps, str):
                raw_steps = raw_steps.splitlines()
            steps = [str(step).strip() for step in (raw_steps or []) if str(step).strip()]
            trigger_rule = str(observation.get("trigger_rule", "")).strip()
            if not skill_name or not steps:
                continue
            try:
                assert_no_prompt_injection(skill_name)
                assert_no_prompt_injection(trigger_rule)
                for step in steps:
                    assert_no_prompt_injection(step)
            except ValueError:
                continue
            grouped.setdefault(skill_name, []).append(
                {
                    "trigger_rule": trigger_rule,
                    "steps": steps,
                    "agent_id": str(observation.get("agent_id", "")).strip(),
                    "success": bool(observation.get("success", False)),
                }
            )

        drafts: list[CrystalDraft] = []
        for skill_name, rows in grouped.items():
            first = rows[0]
            trigger_rule = first["trigger_rule"]
            steps = first["steps"]
            agent_ids = sorted({row["agent_id"] for row in rows if row["agent_id"]})
            drafts.append(
                CrystalDraft(
                    skill_name=skill_name,
                    trigger_rule=trigger_rule,
                    steps=steps,
                    hit_count=len(rows),
                    success_count=sum(1 for row in rows if row["success"]),
                    agent_ids=agent_ids,
                )
            )
        return drafts

    @staticmethod
    def _valid_scope(shared_scope: str) -> str:
        scope = (shared_scope or PRIVATE_SCOPE).strip()
        if scope not in VALID_SCOPES:
            raise ValueError(f"shared_scope 必须是 {sorted(VALID_SCOPES)} 之一")
        return scope

    def _require_principal(self):
        if self.principal is None:
            raise PermissionError("同袍写入必须提供明确 Principal")
        if not self._is_admin(self.principal) and not all(
            self._principal_value(name)
            for name in ("tenant_id", "user_id", "agent_id", "federation_id")
        ):
            raise PermissionError("同袍操作需要 tenant_id、user_id、agent_id 与 federation_id")
        return self.principal

    def _principal_value(self, name: str) -> str | None:
        return getattr(self.principal, name, None) if self.principal is not None else None

    @staticmethod
    def _is_admin(principal) -> bool:
        return bool(getattr(principal, "is_admin", False))

    @staticmethod
    def _row_federation(row) -> str | None:
        return getattr(row, "federation_id", None)

    def _tenant_matches(self, row) -> bool:
        principal = self.principal
        if self._is_admin(principal):
            return True
        tenant_id = self._principal_value("tenant_id")
        user_id = self._principal_value("user_id")
        federation_id = self._principal_value("federation_id")
        return bool(
            tenant_id
            and user_id
            and federation_id
            and self._row_value(row, "tenant_id") == tenant_id
            and self._row_value(row, "user_id") == user_id
        )

    @staticmethod
    def _row_value(row, name: str):
        return getattr(row, name, None)

    def _memory_filter(self) -> ColumnElement[bool] | None:
        if self._is_admin(self.principal):
            return None
        tenant_id = self._principal_value("tenant_id")
        user_id = self._principal_value("user_id")
        federation_id = self._principal_value("federation_id")
        agent_id = self._principal_value("agent_id")
        if not all((tenant_id, user_id, federation_id, agent_id)):
            return false()
        private_clause = and_(
            MemoryItem.shared_scope == PRIVATE_SCOPE,
            MemoryItem.tenant_id == tenant_id,
            MemoryItem.user_id == user_id,
            MemoryItem.agent_id == agent_id,
            MemoryItem.federation_id == federation_id,
        )
        shared_clause = and_(
            MemoryItem.shared_scope == SHARED_SCOPE,
            MemoryItem.tenant_id == tenant_id,
            MemoryItem.user_id == user_id,
            or_(MemoryItem.federation_id == federation_id, MemoryItem.federation_id.is_(None)),
        )
        return or_(private_clause, shared_clause)

    def _memory_visible(self, memory: MemoryItem) -> bool:
        if self._is_admin(self.principal):
            return True
        if not self._tenant_matches(memory):
            return False
        if self._row_federation(memory) not in (None, self._principal_value("federation_id")):
            return False
        if getattr(memory, "shared_scope", PRIVATE_SCOPE) == SHARED_SCOPE:
            return True
        return memory.agent_id == self._principal_value("agent_id")

    def _crystal_visible(self, crystal: SkillCrystal) -> bool:
        if self._is_admin(self.principal):
            return True
        if not self._tenant_matches(crystal):
            return False
        if self._row_federation(crystal) != self._principal_value("federation_id"):
            return False
        if getattr(crystal, "shared_scope", PRIVATE_SCOPE) == SHARED_SCOPE:
            return True
        return crystal.agent_id == self._principal_value("agent_id")

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        if self.session is not None:
            yield self.session
        else:
            with db.get_session() as session:
                yield session

    def store_memory(
        self,
        content: str,
        *,
        shared_scope: str = PRIVATE_SCOPE,
        memory_type: str = "text",
        lane: str = "general",
        domain: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """写入同袍记忆，并在写前完成身份、作用域和注入校验。"""
        principal = self._require_principal()
        content = (content or "").strip()
        if not content:
            raise ValueError("content 不能为空")
        scope = self._valid_scope(shared_scope)
        assert_no_prompt_injection(content)
        memory = MemoryItem(
            id=new_id("mem"),
            tenant_id=getattr(principal, "tenant_id", None),
            federation_id=getattr(principal, "federation_id", None),
            user_id=getattr(principal, "user_id", None),
            agent_id=getattr(principal, "agent_id", None),
            content=content,
            memory_type=memory_type,
            lane=lane,
            domain=domain or ("federated" if scope == SHARED_SCOPE else "user"),
            shared_scope=scope,
            structure=metadata or {},
            valid_from=utcnow(),
            decay_class="procedural" if memory_type == "skill" else "episodic",
        )
        with self._session_scope() as session:
            session.add(memory)
            session.commit()
            session.refresh(memory)
        return memory.id

    def list_visible_memories(self, *, limit: int = 200) -> list[MemoryItem]:
        """列出当前主体可见的 active 记忆，执行最终 ACL 终闸。"""
        if limit < 1:
            raise ValueError("limit 必须为正整数")
        with self._session_scope() as session:
            statement = select(MemoryItem).where(MemoryItem.status == "active")
            memory_filter = self._memory_filter()
            if memory_filter is not None:
                statement = statement.where(memory_filter)
            rows = session.exec(statement.order_by(MemoryItem.created_at.desc()).limit(limit)).all()
            return [row for row in rows if self._memory_visible(row)]

    def register_crystal(self, draft: CrystalDraft) -> str:
        """把提取草稿登记为 observing 状态的私有结晶。"""
        principal = self._require_principal()
        if not draft.skill_name.strip() or not draft.steps:
            raise ValueError("结晶必须包含 skill_name 与非空 steps")
        assert_no_prompt_injection(draft.skill_name)
        assert_no_prompt_injection(draft.trigger_rule)
        for step in draft.steps:
            assert_no_prompt_injection(step)
        with self._session_scope() as session:
            existing = session.exec(
                select(SkillCrystal).where(
                    SkillCrystal.skill_name == draft.skill_name,
                    SkillCrystal.tenant_id == self._principal_value("tenant_id"),
                    SkillCrystal.federation_id == self._principal_value("federation_id"),
                    SkillCrystal.user_id == self._principal_value("user_id"),
                    SkillCrystal.agent_id == self._principal_value("agent_id"),
                    SkillCrystal.shared_scope == PRIVATE_SCOPE,
                )
            ).first()
            if existing is not None:
                return existing.id
            crystal = SkillCrystal(
                id=new_id("crystal"),
                tenant_id=getattr(principal, "tenant_id", None),
                federation_id=getattr(principal, "federation_id", None),
                user_id=getattr(principal, "user_id", None),
                agent_id=getattr(principal, "agent_id", None),
                source_agent_id=getattr(principal, "agent_id", None),
                skill_name=draft.skill_name.strip(),
                trigger_rule=draft.trigger_rule.strip(),
                procedure=draft.procedure,
                sample_keys=[],
                hit_count=0,
                candidate_count=draft.hit_count,
                success_count=0,
                replica_agent_ids=[],
                failure_count=0,
                shadow_state="observing",
                shared_scope=PRIVATE_SCOPE,
                status="observing",
            )
            session.add(crystal)
            session.commit()
            return crystal.id

    @staticmethod
    def _replica_ids(crystal: SkillCrystal) -> list[str]:
        values = getattr(crystal, "replica_agent_ids", []) or []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError:
                values = []
        return sorted({str(value).strip() for value in values if str(value).strip()})

    def _get_crystal(self, session: Session, crystal_id: str) -> SkillCrystal:
        crystal = session.get(SkillCrystal, crystal_id)
        if crystal is None:
            raise ValueError(f"结晶 {crystal_id} 未找到")
        if not self._crystal_visible(crystal):
            raise PermissionError("无权访问该结晶")
        return crystal

    def _get_crystal_for_shadow(self, session: Session, crystal_id: str) -> SkillCrystal:
        """Allow same-federation peers to report results without granting private reads."""
        crystal = session.get(SkillCrystal, crystal_id)
        if crystal is None:
            raise ValueError(f"结晶 {crystal_id} 未找到")
        if self._is_admin(self.principal):
            return crystal
        same_federation = self._tenant_matches(crystal) and self._row_federation(
            crystal
        ) == self._principal_value("federation_id")
        if not same_federation:
            raise PermissionError("无权参与该联邦结晶的影子评考")
        return crystal

    def record_shadow_result(
        self, crystal_id: str, *, agent_id: str, success: bool
    ) -> dict[str, Any]:
        """登记一次影子执行结果，累计独立复现 Agent 数。"""
        agent_id = (agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent_id 不能为空")
        if not isinstance(success, bool):
            raise ValueError("success 必须是布尔值")
        self._require_principal()
        if not self._is_admin(self.principal) and agent_id != self._principal_value("agent_id"):
            raise PermissionError("只能登记当前 Principal 的 Agent")
        with self._session_scope() as session:
            crystal = self._get_crystal_for_shadow(session, crystal_id)
            if crystal.shadow_state in {"promoted", "rejected"}:
                raise ValueError(f"结晶已处于终态 {crystal.shadow_state}")
            crystal.hit_count += 1
            if success:
                crystal.success_count += 1
            else:
                crystal.failure_count += 1
            crystal.replica_agent_ids = sorted({*self._replica_ids(crystal), agent_id})
            crystal.shadow_state = "observing"
            crystal.status = "observing"
            crystal.updated_at = utcnow()
            session.add(crystal)
            session.commit()
            return {
                "crystal_id": crystal_id,
                "state": crystal.shadow_state,
                "hit_count": crystal.hit_count,
                "success_count": crystal.success_count,
                "cross_agent_count": len(crystal.replica_agent_ids),
            }

    def evaluate_crystal(
        self,
        crystal_id: str,
        *,
        min_success_count: int = 3,
        min_agents: int = 2,
    ) -> dict[str, Any]:
        """执行影子考评状态机并写入 SkillPromotionAudit。"""
        if not isinstance(min_success_count, int) or min_success_count < 1:
            raise ValueError("min_success_count 必须是正整数")
        if not isinstance(min_agents, int) or min_agents < 1:
            raise ValueError("min_agents 必须是正整数")
        with self._session_scope() as session:
            crystal = self._get_crystal(session, crystal_id)
            resolved_steps = self._steps_from_procedure(crystal.procedure)
            for value in (crystal.skill_name, crystal.trigger_rule, *resolved_steps):
                assert_no_prompt_injection(value or "")
            replica_ids = self._replica_ids(crystal)
            verification_hash = hashlib.sha256(
                json.dumps(resolved_steps, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            if not resolved_steps:
                decision = "rejected"
                reason = "步骤为空，拒绝进入公共典籍"
                crystal.shadow_state = "rejected"
                crystal.status = "rejected"
            elif crystal.success_count >= min_success_count and len(replica_ids) >= min_agents:
                decision = "approved"
                reason = "成功次数与跨 Agent 独立复现数均达标"
                crystal.shadow_state = "approved"
                crystal.status = "approved"
            else:
                decision = "deferred"
                reason = (
                    f"观察期未达标：success_count={crystal.success_count}/"
                    f"{min_success_count}, cross_agent_count={len(replica_ids)}/{min_agents}"
                )
                crystal.shadow_state = "observing"
                crystal.status = "observing"
            now = utcnow()
            crystal.last_evaluated_at = now
            crystal.updated_at = now
            session.add(
                SkillPromotionAudit(
                    id=new_id("audit"),
                    crystal_id=crystal.id,
                    tenant_id=crystal.tenant_id,
                    federation_id=crystal.federation_id,
                    success_count_at_eval=crystal.success_count,
                    cross_agent_count=len(replica_ids),
                    verification_hash=verification_hash,
                    decision=decision,
                    decision_reason=reason,
                    created_at=now,
                )
            )
            session.add(crystal)
            session.commit()
            return {
                "crystal_id": crystal.id,
                "decision": decision,
                "reason": reason,
                "success_count": crystal.success_count,
                "cross_agent_count": len(replica_ids),
                "verification_hash": verification_hash,
                "steps": resolved_steps,
            }

    @staticmethod
    def _steps_from_procedure(procedure: str) -> list[str]:
        steps: list[str] = []
        for line in (procedure or "").splitlines():
            clean = line.strip().lstrip("-*• ")
            clean = clean.lstrip("0123456789.、) ").strip()
            if clean:
                steps.append(clean)
        return steps

    def promote_crystal(
        self,
        crystal_id: str,
        *,
        min_success_count: int = 3,
        min_agents: int = 2,
    ) -> dict[str, Any]:
        """将已达标结晶发布到公共典籍，并广播可增量同步的版本。"""
        with self._session_scope() as session:
            crystal = self._get_crystal(session, crystal_id)
            if crystal.shadow_state == "promoted":
                promotion = session.exec(
                    select(SkillPromotionAudit)
                    .where(
                        SkillPromotionAudit.crystal_id == crystal.id,
                        SkillPromotionAudit.decision == "promoted",
                        SkillPromotionAudit.promoted_memory_id.is_not(None),
                    )
                    .order_by(SkillPromotionAudit.created_at.desc())
                ).first()
                memory = (
                    session.get(MemoryItem, promotion.promoted_memory_id)
                    if promotion is not None
                    else None
                )
                event = (
                    session.exec(
                        select(SwarmKnowledgeEvent)
                        .where(
                            SwarmKnowledgeEvent.memory_id == memory.id,
                            SwarmKnowledgeEvent.federation_id == (crystal.federation_id or ""),
                            SwarmKnowledgeEvent.event_type == "published",
                        )
                        .order_by(SwarmKnowledgeEvent.id)
                    ).first()
                    if memory is not None
                    else None
                )
                if promotion is None or memory is None or event is None:
                    raise RuntimeError("已晋升结晶缺少公共记忆或发布事件流痕")
                return {
                    "ok": True,
                    "crystal_id": crystal.id,
                    "memory_id": memory.id,
                    "status": "promoted",
                    "shared_scope": SHARED_SCOPE,
                    "swarm_version": event.id,
                    "reason": promotion.decision_reason,
                    "steps": (memory.structure or {}).get("steps")
                    or self._steps_from_procedure(crystal.procedure),
                }

        evaluation = self.evaluate_crystal(
            crystal_id, min_success_count=min_success_count, min_agents=min_agents
        )
        if evaluation["decision"] != "approved":
            return {
                "ok": False,
                "crystal_id": crystal_id,
                "status": "observing" if evaluation["decision"] == "deferred" else "rejected",
                "reason": evaluation["reason"],
            }

        with self._session_scope() as session:
            crystal = self._get_crystal(session, crystal_id)
            principal = self._require_principal()
            effective_principal = principal
            if self._is_admin(principal):
                from lantai.core.auth import Principal

                effective_principal = Principal(
                    tenant_id=crystal.tenant_id,
                    user_id=crystal.user_id,
                    agent_id=crystal.source_agent_id or crystal.agent_id,
                    federation_id=crystal.federation_id,
                )
            resolved_steps = evaluation["steps"]
            skill_name = crystal.skill_name
            trigger_rule = crystal.trigger_rule
        from lantai.services.mem_command import create_skill

        skill_result = create_skill(
            name=skill_name,
            description=trigger_rule,
            steps=resolved_steps,
            tags=["crystal_promoted", "tongpao_shared"],
            principal=effective_principal,
            shared_scope=SHARED_SCOPE,
        )
        if not skill_result.get("ok"):
            raise ValueError(skill_result.get("error", "公共技能落库失败"))
        memory_id = skill_result["memory_id"]

        with self._session_scope() as session:
            crystal = self._get_crystal(session, crystal_id)
            memory = session.get(MemoryItem, memory_id)
            if memory is None:
                raise ValueError(f"公共技能记忆 {memory_id} 未找到")
            memory.federation_id = crystal.federation_id
            memory.shared_scope = SHARED_SCOPE
            memory.domain = "federated"
            crystal.shared_scope = SHARED_SCOPE
            crystal.shadow_state = "promoted"
            crystal.status = "promoted"
            crystal.updated_at = utcnow()
            session.add(
                MemoryEdge(
                    id=new_id("edge"),
                    tenant_id=crystal.tenant_id,
                    user_id=crystal.user_id,
                    agent_id=crystal.source_agent_id,
                    source_memory_id=crystal.id,
                    target_memory_id=memory.id,
                    relation="leads_to",
                    confidence=1.0,
                    reason=f"同袍影子考评达标后晋升公共典籍：{crystal.skill_name}",
                )
            )
            event = SwarmKnowledgeEvent(
                memory_id=memory.id,
                tenant_id=crystal.tenant_id,
                federation_id=crystal.federation_id or "",
                event_type="published",
            )
            session.add(event)
            session.add(
                SkillPromotionAudit(
                    id=new_id("audit"),
                    crystal_id=crystal.id,
                    tenant_id=crystal.tenant_id,
                    federation_id=crystal.federation_id,
                    success_count_at_eval=crystal.success_count,
                    cross_agent_count=len(self._replica_ids(crystal)),
                    verification_hash=evaluation["verification_hash"],
                    decision="promoted",
                    decision_reason=evaluation["reason"],
                    promoted_memory_id=memory.id,
                )
            )
            session.add_all([memory, crystal])
            session.flush()
            swarm_version = event.id
            session.commit()
        return {
            "ok": True,
            "crystal_id": crystal_id,
            "memory_id": memory_id,
            "status": "promoted",
            "shared_scope": SHARED_SCOPE,
            "swarm_version": swarm_version,
            "steps": resolved_steps,
        }

    def sync_diff(self, *, since_version: int = 0, limit: int = 100) -> dict[str, Any]:
        """按事件游标向同联邦 Agent 下发公共典籍增量。"""
        principal = self._require_principal()
        if not isinstance(since_version, int) or since_version < 0:
            raise ValueError("since_version 必须是非负整数")
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1..1000 之间")
        with self._session_scope() as session:
            statement = select(SwarmKnowledgeEvent).where(SwarmKnowledgeEvent.id > since_version)
            if not self._is_admin(principal):
                statement = statement.where(
                    SwarmKnowledgeEvent.tenant_id == self._principal_value("tenant_id"),
                    SwarmKnowledgeEvent.federation_id == self._principal_value("federation_id"),
                )
            events = session.exec(statement.order_by(SwarmKnowledgeEvent.id).limit(limit)).all()
            changes: list[dict[str, Any]] = []
            for event in events:
                memory = session.get(MemoryItem, event.memory_id)
                if (
                    memory is None
                    or memory.status != "active"
                    or not self._memory_visible(memory)
                    or memory.shared_scope != SHARED_SCOPE
                ):
                    continue
                changes.append(
                    {
                        "memory_id": memory.id,
                        "version": event.id,
                        "content": wrap_as_data(
                            memory.content, item_id=memory.id, score=memory.confidence
                        ),
                        "shared_scope": memory.shared_scope,
                        "federation_id": memory.federation_id,
                    }
                )
            next_version = max(
                (event.id for event in events if event.id is not None), default=since_version
            )
        return {
            "since_version": since_version,
            "next_version": next_version,
            "changes": changes,
        }


__all__ = ["CrystalDraft", "PRIVATE_SCOPE", "SHARED_SCOPE", "TongpaoEngine"]
