"""
tests/cognitive/test_cognitive_routes.py
认知 API 端点集成测试（不 Mock 业务逻辑）
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from lantai.models.tables import CognitiveRole, FailureRecord, MemoryItem


# ──────────────────────────────────────────────
# Test App Setup
# ──────────────────────────────────────────────
@pytest.fixture(scope="module")
def client():
    """构造带有内存 DB 的轻量 FastAPI 测试客户端。"""
    from fastapi import FastAPI

    from lantai.api.routes_cognitive import router

    app = FastAPI()
    app.include_router(router)

    # 替换 DB 依赖为内存 SQLite
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override_session():
        with Session(engine) as session:
            # 预置测试数据
            if not session.get(MemoryItem, "rule_t1"):
                session.add(
                    MemoryItem(
                        id="rule_t1",
                        content="Use single SQLite process in production.",
                        role=CognitiveRole.RULE,
                        confidence=0.9,
                    )
                )
                session.add(
                    MemoryItem(
                        id="obs_t1",
                        content="SQLite WAL mode improves read speed.",
                        role=CognitiveRole.OBSERVATION,
                        confidence=0.7,
                    )
                )
                session.add(
                    FailureRecord(
                        id="fail_t1",
                        task="deploy",
                        action="4_workers",
                        expected="stable",
                        actual="crash",
                        severity=0.8,
                        recurrence_count=1,
                        source_ids=[],
                    )
                )
                session.commit()
            yield session

    from lantai.storage.db import get_session

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as c:
        yield c


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────
def test_cognitive_context_endpoint(client):
    """GET /cognitive/context 返回 7 个切面。"""
    resp = client.get("/cognitive/context", params={"task": "deploy lantai", "top_k": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert "facts" in data
    assert "rules" in data
    assert "failures" in data
    assert "beliefs" in data
    assert "principles" in data
    assert "experience" in data
    assert "conflicts" in data
    # 预置的 rule 应出现在 rules 切面
    rule_ids = [m["id"] for m in data["rules"]]
    assert "rule_t1" in rule_ids
    # 预置的 failure 应出现在 failures 切面
    failure_tasks = [f["task"] for f in data["failures"]]
    assert "deploy" in failure_tasks


def test_cognitive_context_prompt_endpoint(client):
    """GET /cognitive/context/prompt 返回 Markdown 字符串。"""
    resp = client.get("/cognitive/context/prompt", params={"task": "database setup"})
    assert resp.status_code == 200
    data = resp.json()
    assert "prompt" in data
    assert "## Governing Rules" in data["prompt"]
    assert "## Relevant Facts" in data["prompt"]


def test_cognitive_reflect_endpoint(client):
    """POST /cognitive/reflect 返回包含 failures 数量的 ReflectionReport。"""
    resp = client.post("/cognitive/reflect")
    assert resp.status_code == 200
    data = resp.json()
    assert "failures" in data
    assert data["failures"] >= 1  # 预置了 1 条 FailureRecord
    assert "new_patterns" in data
    assert "summary" in data


def test_cognitive_observe_endpoint(client):
    """POST /cognitive/observe 写入新的 Observation。"""
    resp = client.post(
        "/cognitive/observe",
        json={
            "content": "Python asyncio improves IO throughput",
            "evidence_type": "observation",
            "reliability": 0.8,
            "independence": 1.0,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "memory_id" in data
    assert "evidence_id" in data
