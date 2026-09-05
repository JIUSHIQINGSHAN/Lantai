"""披沙（ADR-0030）：候选记忆递归精炼（Refine）测试。

验证：
1. refine_service 纯函数解析与指代消解；
2. 异常与失败优雅降级（宁 miss 不脏写：LLM 报错时保持原候选不变）；
3. 真实 SQLite 数据库候选精炼落库（不 mock 冒烟）；
4. REST 路由 POST /candidates/{id}/refine 与 MCP candidate_refine 工具。
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import MemoryCandidate
from lantai.services.refine_service import (
    batch_refine_candidates,
    refine_candidate_record,
    refine_memory_text,
)


class TestRefinePureLogic:
    """测试精炼纯函数与边界防护。"""

    def test_refine_fallback_on_llm_failure(self):
        """测试 LLM 抛异常时降级保持原状（宁 miss 不脏写）。"""
        with patch("lantai.llm.client.chat_json", side_effect=RuntimeError("LLM offline")):
            res = refine_memory_text("大哥喜欢喝绿茶", context="会话片段")
            assert res["refined_text"] == "大哥喜欢喝绿茶"
            assert res["confidence"] == 0.5
            assert res["is_valid"] is True
            assert "LLM offline" in res["reason"] or "降级" in res["reason"]

    def test_refine_success(self):
        """测试正常 LLM 精炼解析。"""
        mock_output = {
            "refined_text": "大哥在日常交流中偏好引用古诗词以增加思考深度。",
            "confidence": 0.88,
            "is_valid": True,
            "lane": "preference",
            "tags": ["审美", "表达习惯"],
            "reason": "消除了口语废话并明确了主体",
        }
        with patch("lantai.llm.client.chat_json", return_value=mock_output):
            res = refine_memory_text("他说话挺喜欢带点诗词什么的", context="会话讨论")
            assert res["refined_text"] == "大哥在日常交流中偏好引用古诗词以增加思考深度。"
            assert res["confidence"] == 0.88
            assert res["lane"] == "preference"
            assert "审美" in res["tags"]


class TestRefineServiceDB:
    """真实 SQLite 数据库不 mock 冒烟测试。"""

    def test_refine_candidate_record_in_db(self, param_env):
        session_factory, _ = param_env
        cand_id = "cand_test_refine_01"
        with session_factory() as s:
            cand = MemoryCandidate(
                id=cand_id,
                document_id="doc_test_01",
                summary="他用的是那个天选电脑",
                claims=["他用的是那个天选电脑"],
                status="pending_review",
                extractor_confidence=0.35,
                lane="general",
                provenance={"source": "chat"},
            )
            s.add(cand)
            s.commit()

        mock_output = {
            "refined_text": "大哥的电脑设备为华硕天选三游戏本。",
            "confidence": 0.85,
            "is_valid": True,
            "lane": "profile",
            "tags": ["硬件", "设备"],
            "reason": "消解代词并提纯核心事实",
        }
        with patch("lantai.llm.client.chat_json", return_value=mock_output):
            with session_factory() as s:
                updated = refine_candidate_record(cand_id, session=s)
                assert updated["summary"] == "大哥的电脑设备为华硕天选三游戏本。"
                assert updated["extractor_confidence"] == 0.85
                assert updated["lane"] == "profile"

                # 再次查库校验
                db_cand = s.get(MemoryCandidate, cand_id)
                assert db_cand.summary == "大哥的电脑设备为华硕天选三游戏本。"
                assert db_cand.extractor_confidence == 0.85


class TestRefineEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_candidate_refine(self, param_env):
        session_factory, _ = param_env
        client = TestClient(app)
        cand_id = "cand_test_rest_refine"
        with session_factory() as s:
            cand = MemoryCandidate(
                id=cand_id,
                document_id="doc_test_02",
                summary="以后把接口超时改成30秒",
                claims=["以后把接口超时改成30秒"],
                status="pending_review",
                extractor_confidence=0.4,
                lane="general",
            )
            s.add(cand)
            s.commit()

        mock_output = {
            "refined_text": "系统接口调用超时时间统一配置为30秒。",
            "confidence": 0.9,
            "is_valid": True,
            "lane": "rule",
            "tags": ["配置", "超时"],
            "reason": "提纯为系统配置规则",
        }
        with patch("lantai.llm.client.chat_json", return_value=mock_output):
            resp = client.post(f"/candidates/{cand_id}/refine")
            assert resp.status_code == 200
            data = resp.json()
            assert data["summary"] == "系统接口调用超时时间统一配置为30秒。"
            assert data["extractor_confidence"] == 0.9

    def test_mcp_candidate_refine(self, param_env):
        session_factory, _ = param_env
        from scripts.mcp_server import handle_candidate_refine
        cand_id = "cand_test_mcp_refine"
        with session_factory() as s:
            cand = MemoryCandidate(
                id=cand_id,
                document_id="doc_test_03",
                summary="测试MCP精炼",
                claims=["测试MCP精炼"],
                status="pending_review",
                extractor_confidence=0.3,
                lane="general",
            )
            s.add(cand)
            s.commit()

        mock_output = {
            "refined_text": "MCP 披沙精炼测试样本通过验证。",
            "confidence": 0.8,
            "is_valid": True,
            "lane": "general",
            "tags": ["测试"],
            "reason": "精炼成功",
        }
        with patch("lantai.llm.client.chat_json", return_value=mock_output):
            res = handle_candidate_refine({"candidate_id": cand_id})
            assert res["summary"] == "MCP 披沙精炼测试样本通过验证。"
            assert res["extractor_confidence"] == 0.8

    def test_batch_refine_candidates(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            # 插入 2 条模糊区间的候选
            c1 = MemoryCandidate(
                id="cand_batch_01",
                document_id="doc_batch",
                summary="模糊候选一",
                claims=["模糊候选一"],
                status="pending_review",
                extractor_confidence=0.3,
            )
            c2 = MemoryCandidate(
                id="cand_batch_02",
                document_id="doc_batch",
                summary="无意义客套废话",
                claims=["无意义客套废话"],
                status="pending_review",
                extractor_confidence=0.25,
            )
            s.add(c1)
            s.add(c2)
            s.commit()

        # 模拟 c1 成功精炼，c2 判定为无效闲聊
        def fake_chat(system, user):
            if "模糊候选一" in user:
                return {"refined_text": "提纯后的高价值事实", "confidence": 0.82, "is_valid": True, "lane": "fact"}
            return {"refined_text": "", "confidence": 0.0, "is_valid": False, "reason": "闲聊废话"}

        with patch("lantai.llm.client.chat_json", side_effect=fake_chat):
            with session_factory() as s:
                summary = batch_refine_candidates(min_conf=0.2, max_conf=0.5, session=s)
                assert summary["total_scanned"] >= 2
                assert summary["refined"] >= 1
                assert summary["rejected"] >= 1

                # 验证 DB 状态
                cand1 = s.get(MemoryCandidate, "cand_batch_01")
                assert cand1.status == "pending_review"
                assert cand1.extractor_confidence == 0.82

                cand2 = s.get(MemoryCandidate, "cand_batch_02")
                assert cand2.status == "rejected"

