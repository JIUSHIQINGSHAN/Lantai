"""分类树路由（v0.7，借鉴 aiduMEI TreeMemory 窄版）。"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import TreeAddNodeReq, TreeAssignReq, TreeUnassignReq
from lantai.services import tree_service
from lantai.storage import db

router = APIRouter()


@router.get("/tree")
def tree_view_route():
    """整树视图：节点 + 每节点挂载计数（只读）。"""
    return tree_service.view_tree()


@router.post("/tree/nodes")
def tree_add_node_route(req: TreeAddNodeReq):
    """新增节点（父缺失/重名/非法名 -> 422，宁 miss 不脏写）。"""
    try:
        return tree_service.add_tree_node(
            req.name, req.parent_path, req.description)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/tree/subtree")
def tree_subtree_route(path: str = "/"):
    """子树视图（含根）+ 挂载计数。"""
    with db.get_session() as s:
        return tree_service.get_subtree(s, path)


@router.post("/tree/assign")
def tree_assign_route(req: TreeAssignReq):
    """把记忆挂到节点（节点/记忆必须存在）。"""
    try:
        return tree_service.assign_memory_to_node(req.memory_id, req.node_path)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/tree/unassign")
def tree_unassign_route(req: TreeUnassignReq):
    """解除记忆挂载。"""
    try:
        return tree_service.unassign_memory_from_node(req.memory_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
