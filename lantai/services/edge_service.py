"""记忆关系（边）service 层——从路由 handler 下沉"""

from lantai.core.acl import viewer_of
from lantai.storage.edges import create_edge, delete_edge, get_edges, get_supersed_chain


def add_edge(
    source_id: str,
    target_id: str,
    relation: str,
    confidence: float = 0.5,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    principal=None,
) -> dict:
    """创建记忆关系。

    principal 用于事前归属校验（票 ownership-gaps/02）：边连的两条记忆
    都要属于调用者，否则 A 能在 B 的记忆上建 supersedes 边改写其检索排序。
    校验在 storage/edges.py 的 create_edge 内做，这里只透传。
    """
    edge = create_edge(
        source_id,
        target_id,
        relation,
        confidence,
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        principal=principal,
    )
    return {"edge_id": edge.id, "relation": edge.relation}


def _edge_visible(e, principal, viewer: str) -> bool:
    """单条边的行级归属判定（票 `.scratch/readside-gaps/22`）。

    **按端点记忆判，不按边自身判**：实测真实库 76/76 条边 `user_id` 全 NULL
    （`.scratch/readside-gaps/probe_22_edge_owner_dist.py`），按边自身过滤
    对全库一条都不生效——修了等于没修。归属信息只在端点记忆上。

    口径同票 17 的 `graph_retriever._owns`：端点属主 `NULL`（历史行）或
    等于 viewer 才放行。两端**有一个**不可见就整条隐藏——边是两端共有的
    关系，露出一半等于告诉 A「B 有一条边连到某个 id」。

    端点记忆不存在（真实库 74/76 条边的 source 是 `doc_*`，`memoryitem`
    里查不到）→ 判不可见。那些边指向幽灵，露出 id 没有意义，而 id 本身
    是票 19 记录过的攻击材料。
    """
    from lantai.models.tables import MemoryItem
    from lantai.storage.db import get_session

    with get_session() as s:
        for mid in (e.source_memory_id, e.target_memory_id):
            mem = s.get(MemoryItem, mid)
            if mem is None:
                return False
            if (mem.user_id or None) not in (None, viewer):
                return False
    return True


def list_edges(memory_id: str, relation: str | None = None, principal=None) -> dict:
    """查询记忆关系。

    归属（票 `.scratch/readside-gaps/22`）：此前一个身份都不取，`get_edges`
    按 `source == mid OR target == mid` 直查全表——A 拿 B 的 memory_id 就能
    列出 B 这条记忆连出去的每一条边的 id/source/target/relation/confidence，
    supersedes 链还把整条取代路径摊开。同票 19/20 的形状：同一个文件里
    `POST /edges` 与 `DELETE /edges/{id}` 都校验了归属，只有这两条读路由没有。

    admin / `principal=None`（worker/MCP/CLI）不过滤，否则内部路径空转。
    """
    edges = get_edges(memory_id, relation=relation)
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        viewer = viewer_of(principal)
        edges = [e for e in edges if _edge_visible(e, principal, viewer)]
    return {
        "edges": [
            {
                "id": e.id,
                "source": e.source_memory_id,
                "target": e.target_memory_id,
                "relation": e.relation,
                "confidence": e.confidence,
            }
            for e in edges
        ]
    }


def get_chain(memory_id: str, principal=None) -> dict:
    """获取 supersedes 链。

    归属（票 `.scratch/readside-gaps/22`）：同 `list_edges`——链上每一跳的
    两端点都要属于调用者。链比边更值钱：它直接告诉 A「B 的这条记忆被谁
    取代了」，`superseded_by` 指向的目标 id 就是下一步该读哪条。
    """
    chain = get_supersed_chain(memory_id)
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        from lantai.models.tables import MemoryItem
        from lantai.storage.db import get_session

        viewer = viewer_of(principal)
        visible = []
        with get_session() as s:
            for hop in chain:
                ok = True
                for mid in (hop["memory_id"], hop["superseded_by"]):
                    mem = s.get(MemoryItem, mid)
                    if mem is None or (mem.user_id or None) not in (None, viewer):
                        ok = False
                        break
                if ok:
                    visible.append(hop)
        chain = visible
    return {"chain": chain}


def remove_edge(edge_id: str) -> bool:
    """删除记忆关系。返回 False 表示未找到。"""
    return delete_edge(edge_id)
