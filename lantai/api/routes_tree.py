"""分类树路由（v0.7，借鉴 aiduMEI TreeMemory 窄版）。

归属（票 .scratch/ownership-gaps/05）：`/tree/assign` 与 `/tree/unassign`
改的是**别人记忆的 tree_path**，必须带身份并校验归属——此前两个端点都不取
principal，A 能把 B 的记忆挂到自己的节点也能摘下来，`batch/organize`
更是批量版。校验复用 `acl.ensure_can_delete` 单一真源（同
`PUT/DELETE /terminal/memory/{id}` 口径）。
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
from lantai.models.schemas import TreeAddNodeReq, TreeAssignReq, TreeUnassignReq
from lantai.services import tree_service
from lantai.storage import db

router = APIRouter()


@router.get("/tree")
def tree_view_route(ctx=Depends(get_current_user)):
    """整树视图：节点 + 每节点挂载计数（只读）。

    归属（票 .scratch/readside-gaps/08）：此前一个身份都不取，返回整棵树的
    节点描述（自由文本）与每节点挂载计数。按 `ctx` 收窄。
    """
    return tree_service.view_tree(principal=ctx)


@router.post("/tree/nodes")
def tree_add_node_route(req: TreeAddNodeReq, ctx=Depends(get_current_user)):
    """新增节点（父缺失/重名/非法名 -> 422，宁 miss 不脏写）。

    归属（票 .scratch/readside-gaps/08）：新节点落 `ctx` 的 user_id。
    """
    try:
        return tree_service.add_tree_node(req.name, req.parent_path, req.description, principal=ctx)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/tree/subtree")
def tree_subtree_route(path: str = "/", ctx=Depends(get_current_user)):
    """子树视图（含根）+ 挂载计数（归属收窄同 /tree，票 readside-gaps/08）。"""
    with db.get_session() as s:
        return tree_service.get_subtree(s, path, principal=ctx)


@router.post("/tree/assign")
def tree_assign_route(req: TreeAssignReq, ctx=Depends(get_current_user)):
    """把记忆挂到节点（节点/记忆必须存在 + 归属校验，票 ownership-gaps/05）。"""
    try:
        return tree_service.assign_memory_to_node(req.memory_id, req.node_path, principal=ctx)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/tree/unassign")
def tree_unassign_route(req: TreeUnassignReq, ctx=Depends(get_current_user)):
    """解除记忆挂载（归属校验同 assign，票 ownership-gaps/05）。"""
    try:
        return tree_service.unassign_memory_from_node(req.memory_id, principal=ctx)
    except ValueError as e:
        raise HTTPException(422, str(e))
