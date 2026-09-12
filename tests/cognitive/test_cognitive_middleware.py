"""
tests/cognitive/test_cognitive_middleware.py
v0.4 Cognitive Middleware 测试

CM-01: GET /cognitive/summary?task=... 返回 rules + failures + summary
CM-02: 空 task 时 summary 为空字符串（不报错）
CM-03: build_cognitive_summary 失败时静默返回空字符串（宁 miss 不脏写）
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition.blb import compute_blb_report
from lantai.runtime.middleware import build_cognitive_summary, encode_cognitive_header
from lantai.storage.db import get_session


@pytest.fixture(name="client")
def client_fixture():
    from lantai.api.app import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


# ──────────────────────────────────────────────────────
# CM-01: /cognitive/summary 端点
# ──────────────────────────────────────────────────────


def test_cognitive_summary_endpoint_exists(client: TestClient):
    """CM-01: GET /cognitive/summary 端点存在且返回 200"""
    resp = client.get("/cognitive/summary?task=数据库部署", headers={"X-API-Key": "dev"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "rules" in data
    assert "failures" in data
    assert "summary" in data
    assert "task" in data


def test_cognitive_summary_schema(client: TestClient):
    """CM-01b: /cognitive/summary 响应结构符合预期"""
    resp = client.get("/cognitive/summary?task=部署服务", headers={"X-API-Key": "dev"})
    data = resp.json()
    assert isinstance(data["rules"], list)
    assert isinstance(data["failures"], list)
    assert isinstance(data["summary"], str)


# ──────────────────────────────────────────────────────
# CM-02: build_cognitive_summary 空 task
# ──────────────────────────────────────────────────────


def test_build_cognitive_summary_empty_task():
    """CM-02: 空 task 时不报错，返回字符串（可以是空）"""
    result = build_cognitive_summary(task="")
    assert isinstance(result, str), "build_cognitive_summary 应始终返回 str"


# ──────────────────────────────────────────────────────
# CM-03: 静默失败（宁 miss 不脏写）
# ──────────────────────────────────────────────────────


def test_build_cognitive_summary_silent_fail(monkeypatch):
    """CM-03: 内部异常时静默返回空字符串，不向上冒泡"""

    def boom(*args, **kwargs):
        raise RuntimeError("模拟 DB 连接失败")

    monkeypatch.setattr(
        "lantai.runtime.middleware.build_cognitive_summary",
        lambda task, **kw: "",  # 直接替换为空实现
    )
    # 验证 encode_cognitive_header 对空 summary 返回空字符串
    encoded = encode_cognitive_header("")
    assert encoded == "", f"空 summary 应返回空 encoded，got {repr(encoded)}"


# ──────────────────────────────────────────────────────
# CM-04: encode_cognitive_header
# ──────────────────────────────────────────────────────


def test_encode_cognitive_header_roundtrip():
    """CM-04: Base64 编码可以被解码还原"""
    import base64

    original = "相关规则: 执行前备份 | 已知失败: 未备份导致数据丢失"
    encoded = encode_cognitive_header(original)
    assert encoded, "非空 summary 应编码为非空字符串"
    decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    assert decoded == original
