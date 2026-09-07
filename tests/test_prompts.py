import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.llm.prompts import EXTRACT_SYS
from lantai.models.tables import PromptTemplate
from lantai.services.prompt_service import get_prompt, update_prompt
from lantai.storage import db


@pytest.fixture
def mem_db():
    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(test_engine)

    def get_session_override():
        with Session(test_engine) as session:
            yield session

    db_module.engine = test_engine

    def session_factory():
        return Session(test_engine)

    return session_factory, test_engine


@pytest.fixture
def client():
    from lantai.core.auth import Principal, get_current_user

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id="test", allowed_lanes=[])
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_prompt_fallback(mem_db):
    session_factory, _ = mem_db
    # It should fallback to the constant
    val = get_prompt("EXTRACT_SYS", EXTRACT_SYS)
    assert val == EXTRACT_SYS


def test_prompt_update_and_fetch(mem_db):
    session_factory, _ = mem_db
    new_template = "You are a test extractor."
    update_prompt("EXTRACT_SYS", new_template, "test description")

    val = get_prompt("EXTRACT_SYS", EXTRACT_SYS)
    assert val == new_template

    # Verify DB
    with session_factory() as s:
        p = s.get(PromptTemplate, "EXTRACT_SYS")
        assert p is not None
        assert p.template == new_template
        assert p.description == "test description"


def test_prompts_api(mem_db, client):
    session_factory, _ = mem_db

    # 1. Update via API
    resp = client.put(
        "/prompts/EXTRACT_SYS", json={"template": "Updated via API", "description": "api update"}
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 2. Get via API
    resp = client.get("/prompts/EXTRACT_SYS")
    assert resp.status_code == 200
    assert resp.json()["template"] == "Updated via API"

    # 3. List via API
    resp = client.get("/prompts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "EXTRACT_SYS"

    # 4. Fallback in GET API
    resp = client.get("/prompts/CONTRADICTION_SYS")
    assert resp.status_code == 200
    assert "You are checking" in resp.json()["template"]
