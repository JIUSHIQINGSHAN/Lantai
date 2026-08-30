r"""沉潜（ADR-0036）：闲时夜梦沉淀与记忆折叠压缩测试。

验证：
1. find_consolidation_clusters 能够按 domain/lane 与主题聚类出 $\ge 3$ 条的碎片记忆集；
2. consolidate_cluster 概念提纯并折叠碎片子记忆（status="consolidated"）；
3. prune_decayed_synapses 自动修剪极度衰减的边缘噪音（status="archived"）；
4. REST POST /evolution/consolidate 与 MCP memory_consolidate 工具。
"""
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import MemoryItem
from lantai.services.consolidation_service import (
    find_consolidation_clusters,
    consolidate_cluster,
    prune_decayed_synapses,
    run_consolidation_cycle,
)


class TestConsolidationDB:
    """真实 SQLite 数据库不 mock 冒烟单测。"""

    def test_find_and_consolidate_cluster(self, param_env):
        session_factory, _ = param_env

        with session_factory() as s:
            # 插入 3 条同主题偏好碎片记忆
            m1 = MemoryItem(
                id="mem_frag_01",
                content="大哥今天早上泡了明前大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.9,
                status="active",
            )
            m2 = MemoryItem(
                id="mem_frag_02",
                content="大哥喜欢喝大佛龙井茶，水温要求85度",
                lane="preference",
                domain="user",
                decay_score=0.88,
                status="active",
            )
            m3 = MemoryItem(
                id="mem_frag_03",
                content="大哥日常饮品偏好为浙江新昌大佛龙井茶",
                lane="preference",
                domain="user",
                decay_score=0.85,
                status="active",
            )
            s.add_all([m1, m2, m3])
            s.commit()

            # 1. 聚类发现
            clusters = find_consolidation_clusters(s, min_cluster_size=3)
            assert len(clusters) >= 1
            cluster_ids = {m.id for m in clusters[0]}
            assert "mem_frag_01" in cluster_ids
            assert "mem_frag_02" in cluster_ids
            assert "mem_frag_03" in cluster_ids

            # 2. 概念折叠与提纯（mock LLM 输出）
            with patch("lantai.services.consolidation_service.chat_json", return_value={
                "consolidated_content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度",
                "importance": 0.9,
                "confidence": 0.95,
            }):
                master = consolidate_cluster(clusters[0], session=s)
                assert master is not None
                assert master.status == "active"
                assert "mem_frag_01" in master.source_ids
                assert "mem_frag_02" in master.source_ids
                assert "mem_frag_03" in master.source_ids

            # 验证原碎片状态更新为 consolidated
            s.refresh(m1)
            s.refresh(m2)
            s.refresh(m3)
            assert m1.status == "consolidated"
            assert m2.status == "consolidated"
            assert m3.status == "consolidated"

    def test_prune_decayed_synapses(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            # 插入 1 条极低衰减且无用的记忆
            m_decayed = MemoryItem(
                id="mem_decayed_01",
                content="临时去了一趟超市买餐巾纸",
                lane="general",
                decay_score=0.02,
                helpful_count=0,
                status="active",
            )
            m_healthy = MemoryItem(
                id="mem_healthy_01",
                content="华硕天选三搭载 RTX 3050 显卡",
                lane="fact",
                decay_score=0.9,
                helpful_count=5,
                status="active",
            )
            s.add_all([m_decayed, m_healthy])
            s.commit()

            pruned = prune_decayed_synapses(threshold=0.05, session=s)
            assert pruned == 1

            s.refresh(m_decayed)
            s.refresh(m_healthy)
            assert m_decayed.status == "archived"
            assert m_healthy.status == "active"


class TestConsolidationEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_evolution_consolidate(self, param_env):
        client = TestClient(app)
        with patch("lantai.services.consolidation_service.run_consolidation_cycle", return_value={
            "consolidated_groups": 1,
            "new_memories": 1,
            "pruned_count": 2,
            "status": "success",
        }):
            resp = client.post("/evolution/consolidate")
            assert resp.status_code == 200
            data = resp.json()
            assert data["consolidated_groups"] == 1
            assert data["pruned_count"] == 2

    def test_mcp_consolidation_tools(self, param_env):
        from scripts.mcp_server import handle_memory_consolidate, handle_consolidation_report
        with patch("lantai.services.consolidation_service.run_consolidation_cycle", return_value={
            "consolidated_groups": 0,
            "new_memories": 0,
            "pruned_count": 0,
            "status": "idle",
        }):
            res = handle_memory_consolidate({})
            assert res["status"] == "idle"

        rep = handle_consolidation_report({})
        assert "last_run" in rep or "status" in rep
