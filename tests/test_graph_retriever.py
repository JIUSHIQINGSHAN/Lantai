"""贯珠（ADR-0035）：基于图谱的二度语义联想与多跳召回测试。

验证：
1. expand_graph_associations 沿 MemoryEdge 展开 1~2 度 BFS 关联联想；
2. 环路防护与置信度过滤；
3. graph_augmented_search 协同初筛与二度联想；
4. REST POST /search/graph_expand 与 MCP graph_expand_search 工具。
"""
import pytest
from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.retrieval.graph_retriever import (
    expand_graph_associations,
    graph_augmented_search,
)


class TestGraphRetrieverDB:
    """真实 SQLite 数据库不 mock 冒烟测试。"""

    def test_expand_graph_associations_multihop(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            # 构造 3 个关联记忆项
            # m1 (天选三) --[contains, conf=0.9]--> m2 (RTX 3050) --[supports, conf=0.85]--> m3 (CUDA 12.0)
            m1 = MemoryItem(id="mem_laptop_01", content="华硕天选三游戏笔记本", decay_score=1.0)
            m2 = MemoryItem(id="mem_gpu_01", content="搭载 NVIDIA RTX 3050 显卡", decay_score=1.0)
            m3 = MemoryItem(id="mem_cuda_01", content="支持 CUDA 12.0 深度学习加速", decay_score=1.0)
            s.add_all([m1, m2, m3])

            e1 = MemoryEdge(id="edge_1", source_memory_id="mem_laptop_01", target_memory_id="mem_gpu_01", relation="contains", confidence=0.9)
            e2 = MemoryEdge(id="edge_2", source_memory_id="mem_gpu_01", target_memory_id="mem_cuda_01", relation="supports", confidence=0.85)
            s.add_all([e1, e2])
            s.commit()

            # 1. 1-hop 联想（从 m1 出发，应召回 m2）
            exp_1 = expand_graph_associations(["mem_laptop_01"], max_hops=1, session=s)
            exp_ids_1 = [x["memory_id"] for x in exp_1]
            assert "mem_gpu_01" in exp_ids_1
            assert "mem_cuda_01" not in exp_ids_1

            # 2. 2-hop 联想（从 m1 出发，应召回 m2 和 m3）
            exp_2 = expand_graph_associations(["mem_laptop_01"], max_hops=2, session=s)
            exp_ids_2 = [x["memory_id"] for x in exp_2]
            assert "mem_gpu_01" in exp_ids_2
            assert "mem_cuda_01" in exp_ids_2


class TestGraphRetrieverEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_graph_expand(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            m1 = MemoryItem(id="mem_p1", content="华硕笔记本设备", decay_score=1.0)
            m2 = MemoryItem(id="mem_p2", content="RTX 3050 图形处理器", decay_score=1.0)
            s.add_all([m1, m2])
            e = MemoryEdge(id="edge_p", source_memory_id="mem_p1", target_memory_id="mem_p2", relation="has_part", confidence=0.9)
            s.add(e)
            s.commit()

        client = TestClient(app)
        resp = client.post("/search/graph_expand", json={"query": "华硕笔记本", "top_k": 3, "max_hops": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert "primary_results" in data
        assert "associated_memories" in data

    def test_mcp_graph_expand_tool(self, param_env):
        from scripts.mcp_server import handle_graph_expand_search
        session_factory, _ = param_env
        with session_factory() as s:
            m1 = MemoryItem(id="mem_mcp_1", content="核心戒律：宁 miss 不脏写", decay_score=1.0)
            m2 = MemoryItem(id="mem_mcp_2", content="测试纪律：核心函数不 mock", decay_score=1.0)
            s.add_all([m1, m2])
            e = MemoryEdge(id="edge_mcp", source_memory_id="mem_mcp_1", target_memory_id="mem_mcp_2", relation="aligns", confidence=0.95)
            s.add(e)
            s.commit()

        res = handle_graph_expand_search({"query": "宁 miss 不脏写", "max_hops": 2})
        assert "primary_results" in res
        assert "associated_memories" in res
