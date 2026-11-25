"""同袍（ADR-0058 · v0.32 Ticket 02）：SkillCrystal 技能结晶晋升评考流水线。

核心原则：
1. 评考规则：检验贡献者、成功指标（success_count >= min_success_count）、procedure 非空、steps 结构化；
2. 宁 miss 不脏写：校验失败拒绝晋升，不产生垃圾资产；
3. 自动晋升：将合格结晶标记为 promoted 并更新 shared_scope='swarm_shared'；
4. 公共殿堂落库：同步调用 create_skill 产出 shared_scope='swarm_shared' 的 MemoryItem 资产；
5. 事件溯源与图谱记录：构建 MemoryEdge(relation='leads_to') 关联 crystal_id 与 memory_id。
"""

from fastapi import HTTPException

from lantai.core.logger import logger
from lantai.models.tables import SkillCrystal


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
    min_success_count: int = 3,
    principal=None,
) -> dict:
    """兼容入口：使用同袍影子考评状态机后再发布结晶。

    ``steps`` 保留为 API 兼容参数，但晋升内容只取结晶已观察的 procedure，
    避免调用方在考评后替换即将发布的执行步骤。
    """
    if principal is None:
        raise HTTPException(status_code=403, detail="crystal promotion requires a principal")
    try:
        from lantai.cognition.tongpao import TongpaoEngine

        return TongpaoEngine(principal=principal).promote_crystal(
            crystal_id,
            min_success_count=min_success_count,
            min_agents=2,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        if "injection guard" not in str(exc):
            raise
        logger.warning("同袍结晶 %s 晋升撞獬豸闸，技能未落库（skip）：%s", crystal_id, exc)
        return {
            "ok": False,
            "crystal_id": crystal_id,
            "reason": str(exc),
            "xiezhi_blocked": True,
        }
