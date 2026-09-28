"""闸门 API——薄 handler，逻辑下沉 service 层。

归属（票 `.scratch/readside-gaps/15`）：此前本端点一个身份都不取，
`decide` 的冲突比对候选集是**全库**——A 构造一条与 B 的记忆关键词相撞的
候选，就能让 B 的正文进矛盾检测 LLM、按 salience 降 B 的 importance、
并往 B 的记忆上写 ConflictEvent。现按 `ctx` 收敛。
"""

from fastapi import APIRouter, Depends

from lantai.core.auth import Principal, get_current_user
from lantai.gate.decision import decide
from lantai.models.schemas import GateReq

router = APIRouter()


@router.post("/gate")
def gate(req: GateReq, ctx: Principal = Depends(get_current_user)):
    return decide(req.candidate_id, principal=ctx)
