"""轨迹级奖励信用分配（episode credit，v022 吸收票据 06，借鉴上游 aiduMEI v21.2 M1）。

一次会话内连续的多步检索-使用构成一条轨迹；任务成败时按**位置**回传
奖励，权重 `w_i = λ·(1/n) + (1−λ)·归一化(γ^(n−i))`——和恒为 1，越靠近
结果越重（收尾的几步对成败贡献更大）。

**最小切片纪律（票据 06）**：本模块只做「登记」——episode / step 落表、
纯函数权重计算、聚合视图。**不接检索权重**（credit 维度默认权重 0）：
上游参数不盲信，等察窗（ADR-0027 语义）攒够本仓真实数据再决定是否
进入打分；装上即生效而无人验证，就是「开关一开就不对」的空转面。
"""

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import EpisodeRecord, EpisodeStep
from lantai.storage import db


def episode_credit_weights(n: int, *, lam: float = 0.5, gamma: float = 0.9) -> list[float]:
    """按位置计算轨迹信用权重（纯函数）。

    w_i = λ·(1/n) + (1−λ)·归一化(γ^(n−i))；i 从 1（最早）到 n（收尾）。
    权重和恒为 1；n=0 返回空列表。"""
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    lam = min(max(float(lam), 0.0), 1.0)
    raw = [gamma ** (n - i) for i in range(1, n + 1)]
    total = sum(raw)
    uniform = 1.0 / n
    weights = [lam * uniform + (1.0 - lam) * (r / total) for r in raw]
    s = sum(weights)
    return [w / s for w in weights] if s else [uniform] * n


def record_episode(
    *,
    session_id: str,
    outcome: str,
    steps: list[dict],
    lam: float = 0.5,
    gamma: float = 0.9,
    user_id: str | None = None,
) -> dict:
    """登记一条轨迹及其各步（只写 episode 侧，不碰记忆正文）。

    steps 按时间序：[{"memory_id": str, "rank": int|None}, ...]，最早在前。
    无 session 的写入（cron 类）不产生 episode（上游纪律）。"""
    if not (session_id or "").strip():
        raise ValueError("episode requires session_id (cron-like writes produce no episode)")
    if outcome not in ("success", "failure", "neutral"):
        raise ValueError("outcome must be one of success/failure/neutral")
    if not steps:
        raise ValueError("episode requires at least one step")
    weights = episode_credit_weights(len(steps), lam=lam, gamma=gamma)
    now = utcnow()
    with db.get_session() as s:
        ep = EpisodeRecord(
            id=new_id("ep"),
            user_id=user_id,
            session_id=session_id.strip(),
            outcome=outcome,
            step_count=len(steps),
            created_at=now,
        )
        s.add(ep)
        s.flush()
        for i, step in enumerate(steps):
            s.add(
                EpisodeStep(
                    id=new_id("epstep"),
                    episode_id=ep.id,
                    position=i + 1,
                    memory_id=str(step.get("memory_id") or ""),
                    rank=step.get("rank"),
                    credit=round(weights[i], 6),
                    created_at=now,
                )
            )
        s.commit()
        return {"episode_id": ep.id, "step_count": len(steps), "outcome": outcome}


def episode_credit_map(memory_ids: list[str] | None = None) -> dict:
    """聚合各记忆累计信用（success 加正、failure 减负；neutral 不计）。

    只读视图，供反思/报告消费；检索打分**不读**它（权重未接，票据 06）。"""
    query = select(EpisodeStep, EpisodeRecord).join(
        EpisodeRecord, EpisodeStep.episode_id == EpisodeRecord.id
    )
    with db.get_session() as s:
        rows = s.exec(query).all()
    credit: dict[str, float] = {}
    for step, ep in rows:
        if not step.memory_id:
            continue
        if memory_ids is not None and step.memory_id not in memory_ids:
            continue
        sign = 1.0 if ep.outcome == "success" else (-1.0 if ep.outcome == "failure" else 0.0)
        if sign == 0.0:
            continue
        credit[step.memory_id] = credit.get(step.memory_id, 0.0) + sign * step.credit
    return {k: round(v, 6) for k, v in credit.items()}
