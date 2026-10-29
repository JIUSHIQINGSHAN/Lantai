"""同袍（ADR-0058 · v0.32 Ticket 02）：SkillCrystal 技能结晶晋升评考流水线。

核心原则：
1. 评考规则：检验贡献者、成功指标（success_count >= min_success_count）、procedure 非空、steps 结构化；
2. 宁 miss 不脏写：校验失败拒绝晋升，不产生垃圾资产；
3. 自动晋升：将合格结晶标记为 promoted 并更新 shared_scope='swarm_shared'；
4. 公共殿堂落库：同步调用 create_skill 产出 shared_scope='swarm_shared' 的 MemoryItem 资产；
5. 事件溯源与图谱记录：构建 MemoryEdge(relation='leads_to') 关联 crystal_id 与 memory_id。
"""

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, SkillCrystal
from lantai.services.mem_command import create_skill
from lantai.storage import db


def evaluate_crystal_for_promotion(
    crystal: SkillCrystal,
    min_success_count: int = 1,
    steps: list[str] | None = None,
) -> tuple[bool, str, list[str]]:
    """评考候选结晶是否具备晋升公共殿堂资格。

    返回：(eligible, reason, resolved_steps)
    """
    if not crystal:
        return False, "结晶不存在", []

    # 1. 检查操作规程或步骤非空
    procedure = (crystal.procedure or "").strip()
    clean_steps = [str(x).strip() for x in (steps or []) if str(x).strip()]

    if not clean_steps:
        # 尝试从 procedure 解析步骤列表
        if procedure:
            lines = [line.strip() for line in procedure.splitlines() if line.strip()]
            for line in lines:
                # 剥离 markdown 列表符或数字序号
                l_clean = line.lstrip("-*•1234567890.、) ").strip()
                if l_clean:
                    clean_steps.append(l_clean)

    if not clean_steps:
        return False, "结晶规程与 steps 步骤内容为空，无法评考晋升（宁 miss 不脏写）", []

    # 2. 检查成功指标与有效性
    success_count = getattr(crystal, "success_count", 0) or 0
    hit_count = getattr(crystal, "hit_count", 0) or 0
    if success_count < min_success_count and hit_count < min_success_count:
        return (
            False,
            f"验证成功次数不足（当前 success_count={success_count}, hit_count={hit_count}，需 ≥ {min_success_count}）",
            clean_steps,
        )

    return True, "评考通过，符合同袍公共殿堂晋升条件", clean_steps


def promote_crystal(
    crystal_id: str,
    steps: list[str] | None = None,
    min_success_count: int = 1,
    principal=None,
) -> dict:
    """执行技能结晶晋升流水线。

    1. 读取结晶并执行评考；
    2. 创建公共典籍 MemoryItem (shared_scope='swarm_shared')；
    3. 结晶状态置为 promoted，shared_scope='swarm_shared'；
    4. 建立格物因果拓扑 MemoryEdge(relation='leads_to')。
    """
    with db.get_session() as s:
        crystal = s.get(SkillCrystal, crystal_id)
        if not crystal:
            raise ValueError(f"结晶 {crystal_id} 未找到")

        eligible, reason, resolved_steps = evaluate_crystal_for_promotion(
            crystal, min_success_count=min_success_count, steps=steps
        )
        if not eligible:
            raise ValueError(f"结晶评考未通过: {reason}")

        skill_name = crystal.skill_name
        trigger_rule = crystal.trigger_rule or "多智能体联邦共享技能"
        tenant_id = getattr(crystal, "tenant_id", None) or getattr(principal, "tenant_id", None)
        user_id = getattr(crystal, "user_id", None) or getattr(principal, "user_id", None)
        source_agent = (
            getattr(crystal, "source_agent_id", None)
            or getattr(crystal, "agent_id", None)
            or getattr(principal, "agent_id", None)
        )

    # 2. 沉淀至公共知识殿堂（shared_scope="swarm_shared"）
    from lantai.core.auth import Principal

    effective_principal = Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=source_agent,
    )

    skill_res = create_skill(
        name=skill_name,
        description=trigger_rule,
        steps=resolved_steps,
        tags=["crystal_promoted", "tongpao_shared"],
        principal=effective_principal,
        shared_scope="swarm_shared",
    )
    if not skill_res.get("ok"):
        raise ValueError(f"技能落库失败: {skill_res.get('error')}")

    memory_id = skill_res["memory_id"]

    # 3. 更新结晶本身状态 + 记录 MemoryEdge
    with db.get_session() as s:
        c_to_update = s.get(SkillCrystal, crystal_id)
        if c_to_update:
            c_to_update.status = "promoted"
            c_to_update.shared_scope = "swarm_shared"
            c_to_update.decision_reason = reason
            c_to_update.updated_at = utcnow()
            s.add(c_to_update)

        # 4. 建立因果/溯源边
        edge = MemoryEdge(
            id=new_id("edge"),
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=source_agent,
            source_memory_id=crystal_id,
            target_memory_id=memory_id,
            relation="leads_to",
            confidence=1.0,
            reason=f"SkillCrystal 经评考晋升至公共殿堂: {skill_name}",
        )
        s.add(edge)
        s.commit()

    logger.info(
        "同袍结晶晋升成功: crystal=%s -> memory=%s, skill_name=%s",
        crystal_id,
        memory_id,
        skill_name,
    )

    return {
        "ok": True,
        "crystal_id": crystal_id,
        "memory_id": memory_id,
        "status": "promoted",
        "shared_scope": "swarm_shared",
        "reason": reason,
        "steps": resolved_steps,
    }
