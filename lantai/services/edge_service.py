"""记忆关系（边）service 层——从路由 handler 下沉"""

from lantai.core.acl import viewer_of
from lantai.storage.edges import (
    create_edge,
    delete_edge,
    get_edges,
    get_edges_for_memories,
    get_supersed_chain,
)
from lantai.storage.edges import (
    get_causal_chain as storage_get_causal_chain,
)


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
            # 票 40：端点判活——撤回契约「全检索面禁用」覆盖边读面（软态
            # 端点的边对非 admin 不再返回；admin/principal=None 面不受此滤）
            from lantai.services.record_ops_service import STATUS_ACTIVE

            if mem.status != STATUS_ACTIVE:
                return False
            if (mem.user_id or None) not in (None, viewer):
                return False
    return True


def _edge_to_dict(e) -> dict:
    return {
        "id": e.id,
        "source": e.source_memory_id,
        "target": e.target_memory_id,
        "relation": e.relation,
        "confidence": e.confidence,
    }


def _filter_visible_batch(edges, viewer: str) -> list:
    """`_edge_visible` 的批量版：端点记忆一次查回，判定口径逐字相同。

    端点属主 `NULL` 或等于 viewer 才放行；两端有一个不可见（含端点记忆
    不存在）就整条隐藏。只是把逐边 `s.get` 换成一条 `IN` 查询。
    """
    from sqlmodel import select

    from lantai.models.tables import MemoryItem
    from lantai.storage.db import get_session

    if not edges:
        return []
    endpoint_ids = list({mid for e in edges for mid in (e.source_memory_id, e.target_memory_id)})
    owners: dict[str, tuple[str | None, str]] = {}
    with get_session() as s:
        for i in range(0, len(endpoint_ids), 400):
            chunk = endpoint_ids[i : i + 400]
            rows = s.exec(
                select(MemoryItem.id, MemoryItem.user_id, MemoryItem.status).where(
                    MemoryItem.id.in_(chunk)
                )
            ).all()
            for mid, uid, st in rows:
                owners[mid] = (uid, st)

    def _ok(mid: str) -> bool:
        if mid not in owners:
            return False
        uid, st = owners[mid]
        # 票 40：端点判活（同 _edge_visible——软态端点的边对非 admin 隐藏）
        from lantai.services.record_ops_service import STATUS_ACTIVE

        if st != STATUS_ACTIVE:
            return False
        return (uid or None) in (None, viewer)

    return [e for e in edges if _ok(e.source_memory_id) and _ok(e.target_memory_id)]


def list_edges_batch(
    memory_ids, relation: str | None = None, principal=None
) -> dict[str, list[dict]]:
    """批量查询多条记忆的关系边——`list_edges` 的批量等价物。

    返回 `{memory_id: [edge_dict, ...]}`，每个 id 对应的列表与单独调
    `list_edges(memory_id, relation, principal=principal)["edges"]` 内容相同
    （同一归属口径、同一字段形状）；查不到边的 id 映射到空列表。
    一条边连着两个输入 id 时，两边各出现一次——与逐个调用一致，
    去重留给调用方（如 `terminal_graph`）。

    归属：admin / `principal=None` 不过滤，其余按端点记忆判（同 `list_edges`）。
    """
    ids = list(dict.fromkeys(m for m in memory_ids if m))
    result: dict[str, list[dict]] = {mid: [] for mid in ids}
    if not ids:
        return result
    edges = get_edges_for_memories(ids, relation=relation)
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        edges = _filter_visible_batch(edges, viewer_of(principal))
    for e in edges:
        d = _edge_to_dict(e)
        if e.source_memory_id in result:
            result[e.source_memory_id].append(d)
        if e.target_memory_id in result and e.target_memory_id != e.source_memory_id:
            result[e.target_memory_id].append(dict(d))
    return result


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


def get_causal_chain(
    memory_id: str,
    direction: str = "forward",
    max_depth: int = 10,
    principal=None,
) -> dict:
    """格物（v0.31）：获取因果时序溯源链，带租户行级 ACL 校验。"""
    # 根节点可见性前置校验（宁 miss 不脏写）：
    # 若起点 memory_id 对调用者不可见，链的开端就非法，直接返回空
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        from lantai.models.tables import MemoryItem
        from lantai.storage.db import get_session

        viewer = viewer_of(principal)
        with get_session() as s:
            root_mem = s.get(MemoryItem, memory_id)
            if root_mem is None or (root_mem.user_id or None) not in (None, viewer):
                return {"chain": []}

    chain = storage_get_causal_chain(memory_id, direction=direction, max_depth=max_depth)
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        from lantai.models.tables import MemoryItem
        from lantai.storage.db import get_session

        viewer = viewer_of(principal)
        visible = []
        with get_session() as s:
            for hop in chain:
                ok = True
                for mid in (hop["from_id"], hop["to_id"]):
                    mem = s.get(MemoryItem, mid)
                    if mem is None or (mem.user_id or None) not in (None, viewer):
                        ok = False
                        break
                if not ok:
                    # 链路上遇到不可见节点，后续链路立即截断保护
                    break
                visible.append(hop)
        chain = visible
    return {"chain": chain}


def remove_edge(edge_id: str) -> bool:
    """删除记忆关系。返回 False 表示未找到。"""
    return delete_edge(edge_id)
