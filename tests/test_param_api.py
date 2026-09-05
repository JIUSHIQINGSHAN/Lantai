"""
参数建议 API 冒烟测试——TestClient 直打端点（数据库真实，LLM mock）。
"""
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.tables import ParamSuggestion
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash


@pytest.fixture(scope="function")
def client():
    import lantai.models.tables  # noqa: F401
    from lantai.parameters.registry import get_adjustable_names
    names = get_adjustable_names()
    saved = {n: getattr(settings, n) for n in names}

    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("lantai.storage.vector_store.ChromaVectorStore"), \
         patch("lantai.retrieval.hybrid.get_vector_store",
               return_value=Mock(search=Mock(return_value=[]))), \
         patch("lantai.retrieval.hybrid.embed",
               return_value=[[0.1] * 8]):
        from api_server import app
        with TestClient(app) as c:
            yield c, get_test_session

    # 恢复 settings 白名单参数（审批/回滚测试会原位修改单例）
    for n, v in saved.items():
        setattr(settings, n, v)


def _seed_suggestion(session_factory, status="pending"):
    snap = default_snapshot()
    after = dict(snap)
    after["RETRIEVAL_W_VECTOR"] = 0.55
    after["RETRIEVAL_W_BM25"] = 0.30
    with session_factory() as s:
        sug = ParamSuggestion(
            id=new_id("psg"), run_id=new_id("par"), status=status,
            confidence=0.9, title="调整 BM25", summary="s", rationale="r",
            expected_benefit="b", risk_notes="n", validation_plan="p",
            source_document_ids=["raw_x"],
            evidence=[{"source_document_id": "raw_x", "quote": "q",
                       "finding": "f", "applicability": "a"}],
            changes=[{"name": "RETRIEVAL_W_VECTOR", "before": 0.6,
                      "after": 0.55, "reason": "r"},
                     {"name": "RETRIEVAL_W_BM25", "before": 0.25,
                      "after": 0.30, "reason": "r"}],
            before_snapshot=snap, after_snapshot=after,
            base_snapshot_hash=snapshot_hash(snap),
            registry_version="sha256:v", fingerprint=new_id("fp"))
        s.add(sug)
        s.commit()
        return sug.id


class TestParamAdviceApi:
    def test_list_empty(self, client):
        c, _ = client
        r = c.get("/param-suggestions")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_list_and_detail(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        r = c.get("/param-suggestions")
        assert r.status_code == 200
        assert r.json()["items"][0]["id"] == sug

        d = c.get(f"/param-suggestions/{sug}")
        assert d.status_code == 200
        body = d.json()
        assert body["changes"][0]["after"] == 0.55
        assert body["before_snapshot"]["RETRIEVAL_W_VECTOR"] == 0.6

    def test_approve_flow(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        r = c.post(f"/param-suggestions/{sug}/decision",
                   json={"decision": "accepted", "note": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert body["override"]["revision"] == 1
        # settings 生效
        assert settings.RETRIEVAL_W_VECTOR == 0.55
        # runtime-params 可见
        rp = c.get("/runtime-params")
        assert rp.status_code == 200
        assert rp.json()["revision"] == 1
        assert rp.json()["snapshot"]["RETRIEVAL_W_BM25"] == 0.30

    def test_reject_flow(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        r = c.post(f"/param-suggestions/{sug}/decision",
                   json={"decision": "rejected", "note": "语料差异"})
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    def test_approve_twice_conflict(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        c.post(f"/param-suggestions/{sug}/decision",
               json={"decision": "accepted"})
        r = c.post(f"/param-suggestions/{sug}/decision",
                   json={"decision": "accepted"})
        assert r.status_code == 409

    def test_rollback_flow(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        applied = c.post(f"/param-suggestions/{sug}/decision",
                         json={"decision": "accepted"}).json()
        oid = applied["override"]["id"]
        rb = c.post(f"/param-overrides/{oid}/rollback", json={"note": "效果差"})
        assert rb.status_code == 200
        body = rb.json()
        assert body["rollback_override"]["operation"] == "rollback"
        assert body["effective_snapshot"]["RETRIEVAL_W_VECTOR"] == 0.6
        assert settings.RETRIEVAL_W_VECTOR == 0.6

    def test_rollback_conflict(self, client):
        c, sf = client
        sug = _seed_suggestion(sf)
        applied = c.post(f"/param-suggestions/{sug}/decision",
                         json={"decision": "accepted"}).json()
        oid = applied["override"]["id"]
        c.post(f"/param-overrides/{oid}/rollback", json={})
        r = c.post(f"/param-overrides/{oid}/rollback", json={})
        assert r.status_code == 409

    def test_runtime_params_no_leak(self, client):
        c, _ = client
        r = c.get("/runtime-params")
        assert r.status_code == 200
        body = r.json()
        # 只暴露六个白名单参数 + 元信息，绝不泄漏密钥
        assert "API_KEY" not in body["snapshot"]
        assert set(body["snapshot"]) == {
            "RETRIEVAL_W_VECTOR", "RETRIEVAL_W_BM25", "RETRIEVAL_W_FTS",
            "RETRIEVAL_W_DECAY", "DEDUP_MERGE_THRESHOLD",
            "DEDUP_UPDATE_THRESHOLD"}
        assert body["revision"] == 0

    def test_not_found(self, client):
        c, _ = client
        r = c.get("/param-suggestions/psg_nonexistent")
        assert r.status_code == 404
