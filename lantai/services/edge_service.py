"""记忆关系（边）service 层——从路由 handler 下沉"""
from lantai.storage.edges import create_edge, delete_edge, get_edges, get_supersed_chain


def add_edge(source_id: str, target_id: str, relation: str, confidence: float = 0.5) -> dict:
    """创建记忆关系。"""
    edge = create_edge(source_id, target_id, relation, confidence)
    return {"edge_id": edge.id, "relation": edge.relation}


def list_edges(memory_id: str, relation: str | None = None) -> dict:
    """查询记忆关系。"""
    edges = get_edges(memory_id, relation=relation)
    return {"edges": [
        {
            "id": e.id,
            "source": e.source_memory_id,
            "target": e.target_memory_id,
            "relation": e.relation,
            "confidence": e.confidence,
        }
        for e in edges
    ]}


def get_chain(memory_id: str) -> dict:
    """获取 supersedes 链。"""
    chain = get_supersed_chain(memory_id)
    return {"chain": chain}


def remove_edge(edge_id: str) -> bool:
    """删除记忆关系。返回 False 表示未找到。"""
    return delete_edge(edge_id)
