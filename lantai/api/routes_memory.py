from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import Principal, get_current_user
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.services.memory_service import (
    add_memory,
    add_memory_async,
    add_raw_memory,
    get_core_memory,
    list_memories,
    put_core_memory,
)

router = APIRouter()


@router.post("/add")
def add_memory_route(
    req: AddMemoryReq, async_mode: bool = False, ctx: Principal = Depends(get_current_user)
):
    if req.lane not in ctx.allowed_lanes:
        raise HTTPException(status_code=403, detail=f"Lane {req.lane} not allowed for agent")
    # principal=ctx（票 .scratch/readside-gaps/15）：去重此前在全库范围找
    # 最近邻，A 构造相似内容即可命中 B 的记忆并把它送进外部 LLM / 改它的
    # importance。带身份后按属主收敛。
    if async_mode:
        return add_memory_async(
            req, user_id=ctx.user_id or "default", tenant_id=ctx.tenant_id, principal=ctx
        )
    try:
        return add_memory(
            req, user_id=ctx.user_id or "default", tenant_id=ctx.tenant_id, principal=ctx
        )
    except ValueError as e:
        # 獬豸注入拒绝走 ValueError（同 dialogue 路由 422 范式）
        raise HTTPException(422, str(e))


@router.get("/core-memory")
def get_core_memory_route(namespace: str = "default", ctx: Principal = Depends(get_current_user)):
    # 必须把 principal 传下去（票 .scratch/ownership-gaps/04）：此前两个
    # /core-memory 端点都不带身份，A 写的 policy 块任意登录用户一读就到。
    return get_core_memory(namespace, principal=ctx)


@router.put("/core-memory")
def put_core_memory_route(
    block: str, content: str, namespace: str = "default", ctx: Principal = Depends(get_current_user)
):
    try:
        return put_core_memory(block, content, namespace, principal=ctx)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/add/raw")
def add_raw_memory_route(req: RawMemoryReq, ctx: Principal = Depends(get_current_user)):
    """原文直存（verbatim）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""
    if req.lane not in ctx.allowed_lanes:
        raise HTTPException(status_code=403, detail=f"Lane {req.lane} not allowed for agent")
    # 归属四元组必须随 principal 落列（票 .scratch/ownership-gaps/03）：
    # verbatim 是库里条数最多的一类，属主恒 NULL 会让按属主过滤的检索
    # （fts.py:160 的 `AND m.user_id = ?`）把它整类滤光。
    try:
        return add_raw_memory(
            req,
            user_id=ctx.user_id or "default",
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
        )
    except ValueError as e:
        # 獬豸注入拒绝走 ValueError（同 dialogue 路由 422 范式）
        raise HTTPException(422, str(e))


@router.get("/memories")
def list_memories_route(
    lane: str = "",
    status: str = "",
    decay_class: str = "",
    memory_type: str = "",
    limit: int = 50,
    offset: int = 0,
    ctx: Principal = Depends(get_current_user),
):
    """档案浏览（VAULT）：只读分页 + 过滤，受保护。

    必须传 principal（票 .scratch/ownership-gaps/01）：`build_memories_page`
    本就按 tenant/user/session/agent/allowed_lanes 建过滤条件，但不传就
    一个条件都不建——**任意登录用户可读全库记忆全文并拿到 ULID id**，
    而那个 id 正是 supersedes 边注入需要的目标预言机。
    同文件的 /add、/add/raw 与 routes_terminal.py:165 的 /terminal/graph
    都传了，只有这里漏了。
    """
    try:
        return list_memories(
            lane=lane,
            status=status,
            decay_class=decay_class,
            memory_type=memory_type,
            limit=limit,
            offset=offset,
            principal=ctx,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
