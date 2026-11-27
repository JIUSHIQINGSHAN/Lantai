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

from lantai.api.app import app
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

    def test_submit_worker_passes_federation_to_dialogue_ingest(self, param_env, monkeypatch):
        import lantai.ingestion.dialogue as dialogue
        import lantai.services.async_ingest_service as async_service

        clear_tasks()
        captured = {}

        def ingest(**kwargs):
            captured.update(kwargs)
            return {"status": "candidate"}

        class ImmediateExecutor:
            def submit(self, fn, *args, **kwargs):
                fn(*args, **kwargs)

        monkeypatch.setattr(async_service, "claim", lambda *args, **kwargs: {"action": "claimed"})
        monkeypatch.setattr(async_service, "settle", lambda *args, **kwargs: None)
        monkeypatch.setattr(async_service, "_start_ingest_event_selfheal", lambda **_: "event-1")
        monkeypatch.setattr(async_service, "_EXECUTOR", ImmediateExecutor())
        monkeypatch.setattr(dialogue, "ingest_dialogue", ingest)

        task = submit_async_dialogue(
            text="记住：异步 worker 应收到联邦身份",
            user_id="user-a",
            tenant_id="tenant-a",
            agent_id="agent-a",
            federation_id="federation-a",
        )

        assert task["status"] == "queued"
        assert get_task_status(task["task_id"])["status"] == "completed"
        assert captured["federation_id"] == "federation-a"
        clear_tasks()


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

    def test_rest_async_dialogue_passes_authenticated_federation(self, param_env, monkeypatch):
        from lantai.core.auth import Principal, get_current_user

        captured = {}

        def submit(*args, **kwargs):
            if args:
                captured["text"] = args[0]
            captured.update(kwargs)
            return {"task_id": "task_test", "status": "queued"}

        monkeypatch.setitem(
            app.dependency_overrides,
            get_current_user,
            lambda: Principal(
                user_id="dialogue-user",
                tenant_id="dialogue-tenant",
                agent_id="dialogue-agent",
                federation_id="trusted-federation",
            ),
        )
        monkeypatch.setattr("lantai.services.async_ingest_service.submit_async_dialogue", submit)

        response = TestClient(app).post(
            "/dialogue/async", json={"text": "记住：异步对话也保留联邦身份"}
        )

        assert response.status_code == 200
        assert captured["tenant_id"] == "dialogue-tenant"
        assert captured["agent_id"] == "dialogue-agent"
        assert captured["federation_id"] == "trusted-federation"

    def test_mcp_async_dialogue(self, param_env):
        from lantai.cli.mcp import handle_dialogue_add_async, handle_dialogue_task_status

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
