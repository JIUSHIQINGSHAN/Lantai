"""潜移（ADR-0033）：异步零延迟记忆摄取管道测试。

验证：
1. submit_async_dialogue 毫秒级生成 task_id 并入队；
2. 任务执行状态流转（queued -> completed）；
3. REST 端点 POST /dialogue/async 与 GET /dialogue/tasks/{task_id}；
4. MCP 工具 dialogue_add_async 与 dialogue_task_status。
"""
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_server import app
from lantai.services.async_ingest_service import (
    clear_tasks,
    get_task_status,
    submit_async_dialogue,
)


class TestAsyncIngestService:
    """测试异步任务调度器。"""

    def test_submit_and_complete_task(self, param_env):
        clear_tasks()

        mock_res = {
            "status": "extracted",
            "candidates": [{"id": "c1", "content": "大哥喜欢喝茶", "confidence": 0.9}],
            "memories_created": 1,
        }

        with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value=mock_res):
            task_info = submit_async_dialogue(
                text="用户：我平时挺喜欢喝茶的",
                user_id="u1",
                source="chat",
            )
            assert task_info["status"] == "queued"
            task_id = task_info["task_id"]
            assert task_id.startswith("task_")

            # 等待后台线程执行完成（最多 2 秒）
            for _ in range(20):
                st = get_task_status(task_id)
                if st["status"] in ("completed", "failed"):
                    break
                time.sleep(0.1)

            st = get_task_status(task_id)
            assert st["status"] == "completed"
            assert st["result"]["memories_created"] == 1


class TestAsyncIngestEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_async_dialogue(self, param_env):
        client = TestClient(app)
        clear_tasks()

        mock_res = {"status": "extracted", "memories_created": 1}
        with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value=mock_res):
            # 1. 异步提交
            resp = client.post(
                "/dialogue/async",
                json={"text": "测试异步接口", "user_id": "test_u"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "queued"
            task_id = data["task_id"]

            # 2. 查询状态
            for _ in range(20):
                r2 = client.get(f"/dialogue/tasks/{task_id}")
                if r2.json()["status"] == "completed":
                    break
                time.sleep(0.1)

            r3 = client.get(f"/dialogue/tasks/{task_id}")
            assert r3.status_code == 200
            assert r3.json()["status"] == "completed"

    def test_mcp_async_dialogue(self, param_env):
        from scripts.mcp_server import handle_dialogue_add_async, handle_dialogue_task_status
        clear_tasks()

        mock_res = {"status": "extracted", "memories_created": 1}
        with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value=mock_res):
            res1 = handle_dialogue_add_async({"text": "测试 MCP 异步对话"})
            assert res1["status"] == "queued"
            task_id = res1["task_id"]

            for _ in range(20):
                res2 = handle_dialogue_task_status({"task_id": task_id})
                if res2["status"] == "completed":
                    break
                time.sleep(0.1)

            res3 = handle_dialogue_task_status({"task_id": task_id})
            assert res3["status"] == "completed"
