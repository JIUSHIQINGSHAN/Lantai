"""记忆分类树服务（v0.7，借鉴 aiduMEI TreeMemory 窄版）。

显式父子层级 + node_path 唯一路径（/projects/release）+ depth 前缀查询；
记忆经 memoryitem.tree_path 显式挂载（assign，不靠名字匹配——避开作者
v17 之前 category LIKE 误匹配的坑）。宁 miss 不脏写：父缺失/重名/非法名
一律 ValueError 不落库。
"""

from sqlmodel import func, select

from lantai.core.ids import new_id
from lantai.models.tables import MemoryItem, MemoryNode
from lantai.storage import db


def validate_node_name(name: str) -> str:
    """节点名校验（纯函数）：非空、不含路径分隔符。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("name must be a non-empty string")
    if "/" in name or "\\" in name:
        raise ValueError("name must not contain path separators")
    return name


def normalize_path(path: str) -> str:
    """规范化节点路径（纯函数）：/a/b/ -> /a/b；空 -> /。"""
    path = (path or "").strip()
    if not path or path == "/":
        return "/"
    return "/" + "/".join(seg for seg in path.split("/") if seg)


def build_node_path(parent_path: str, name: str) -> tuple[str, int]:
    """拼接子节点路径（纯函数）：返回 (node_path, depth)。顶级节点 depth=1。"""
    name = validate_node_name(name)
    parent = normalize_path(parent_path)
    if parent == "/":
        return f"/{name}", 1
    parent_depth = len([seg for seg in parent.split("/") if seg])
    return f"{parent}/{name}", parent_depth + 1


def compute_attachments(rows: list[tuple[str | None, int]], nodes: list) -> dict[str, dict]:
    """挂载统计（纯函数）：rows=(tree_path, count) 聚合行。

    返回 {node_id: {"direct": 直接挂载数, "subtree": 含子树挂载数}}；
    前缀按 node_path + "/" 匹配，避免 /a 误匹配 /ab。
    """
    result: dict[str, dict] = {}
    for node in nodes:
        path = node.node_path
        direct = sum(c for tp, c in rows if tp == path)
        subtree = sum(c for tp, c in rows if tp == path or (tp or "").startswith(path + "/"))
        result[node.id] = {"direct": direct, "subtree": subtree}
    return result


def get_subtree(session, root_path: str = "/") -> dict:
    """取子树（含根）节点 + 每节点挂载计数；根不存在返回空。"""
    root = normalize_path(root_path)
    prefix = "/%" if root == "/" else root + "/%"
    nodes = list(
        session.exec(
            select(MemoryNode)
            .where((MemoryNode.node_path == root) | (MemoryNode.node_path.like(prefix)))
            .order_by(MemoryNode.depth, MemoryNode.name)
        ).all()
    )
    rows = session.exec(
        select(MemoryItem.tree_path, func.count())
        .where(
            MemoryItem.status == "active",
            MemoryItem.tree_path.is_not(None),
        )
        .group_by(MemoryItem.tree_path)
    ).all()
    counts = compute_attachments([(r[0], r[1]) for r in rows], nodes)
    return {
        "root": None if not nodes else nodes[0].node_path,
        "nodes": [
            {
                "id": n.id,
                "parent_id": n.parent_id,
                "name": n.name,
                "node_path": n.node_path,
                "depth": n.depth,
                "description": n.description,
                "attachments": counts.get(n.id, {"direct": 0, "subtree": 0}),
            }
            for n in nodes
        ],
    }


def add_node(
    session, name: str, parent_path: str = "/", description: str = "", namespace: str = "default"
) -> dict:
    """新增节点（宁 miss 不脏写）：父缺失/同级重名/非法名 -> ValueError。"""
    node_path, depth = build_node_path(parent_path, name)
    if session.exec(select(MemoryNode).where(MemoryNode.node_path == node_path)).first():
        raise ValueError(f"node already exists: {node_path}")
    parent_id = None
    if normalize_path(parent_path) != "/":
        parent = session.exec(
            select(MemoryNode).where(MemoryNode.node_path == normalize_path(parent_path))
        ).first()
        if not parent:
            raise ValueError(f"parent node not found: {parent_path}")
        parent_id = parent.id
    node = MemoryNode(
        id=new_id("node"),
        parent_id=parent_id,
        name=name,
        node_path=node_path,
        depth=depth,
        description=(description or "").strip(),
        namespace=namespace,
    )
    session.add(node)
    session.commit()
    session.refresh(node)
    return {"node": node.model_dump(mode="json")}


def assign_memory(session, memory_id: str, node_path: str, *, principal=None) -> dict:
    """把记忆挂到节点（校验节点/记忆均存在 + 归属校验；宁 miss 不脏写）。

    归属校验（票 .scratch/ownership-gaps/05）：改他人记忆的 tree_path 视为
    破坏性操作，口径与 `PUT/DELETE /terminal/memory/{id}` 同一真源
    （`acl.ensure_can_delete`）。此前三个端点都不查归属，A 能把 B 的记忆
    挂到自己的节点也能摘下来，`batch/organize` 更是批量版。
    principal=None 仅限内部调用（MCP/脚本），不校验。
    """
    path = normalize_path(node_path)
    if not session.exec(select(MemoryNode).where(MemoryNode.node_path == path)).first():
        raise ValueError(f"node not found: {path}")
    mem = session.get(MemoryItem, memory_id)
    if not mem:
        raise ValueError(f"memory not found: {memory_id}")
    if principal is not None:
        from lantai.core.acl import ensure_can_delete

        ensure_can_delete(
            principal,
            resource_user_id=mem.user_id,
            resource_tenant_id=mem.tenant_id,
            lane=mem.lane,
        )
    mem.tree_path = path
    session.add(mem)
    session.commit()
    return {"ok": True, "memory_id": memory_id, "node_path": path}


def unassign_memory(session, memory_id: str, *, principal=None) -> dict:
    """解除记忆挂载（归属校验同 assign，票 ownership-gaps/05）。"""
    mem = session.get(MemoryItem, memory_id)
    if not mem:
        raise ValueError(f"memory not found: {memory_id}")
    if principal is not None:
        from lantai.core.acl import ensure_can_delete

        ensure_can_delete(
            principal,
            resource_user_id=mem.user_id,
            resource_tenant_id=mem.tenant_id,
            lane=mem.lane,
        )
    mem.tree_path = None
    session.add(mem)
    session.commit()
    return {"ok": True, "memory_id": memory_id}


# ── 默认会话包装（供 REST/MCP 调用）────────────────────────


def view_tree() -> dict:
    with db.get_session() as s:
        return get_subtree(s, "/")


def add_tree_node(name: str, parent_path: str = "/", description: str = "") -> dict:
    with db.get_session() as s:
        return add_node(s, name, parent_path, description)


def assign_memory_to_node(memory_id: str, node_path: str, *, principal=None) -> dict:
    with db.get_session() as s:
        return assign_memory(s, memory_id, node_path, principal=principal)


def unassign_memory_from_node(memory_id: str, *, principal=None) -> dict:
    with db.get_session() as s:
        return unassign_memory(s, memory_id, principal=principal)
