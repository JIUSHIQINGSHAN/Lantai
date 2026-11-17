"""Chroma 1.x Rust integration: actual persistent collections, no storage mocks."""

import chromadb
import numpy as np
import pytest
from chromadb.config import Settings as ChromaSettings

from lantai.core.acl import Principal, vector_owner_filter
from lantai.core.settings import settings
from lantai.storage.vector_store import ChromaVectorStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma"))
    return ChromaVectorStore()


def test_new_collection_configuration_and_no_implicit_embedding(store):
    assert store._collection.name == "lantai_vectors"
    assert store._collection.configuration["hnsw"]["space"] == "cosine"
    assert not (store._collection.metadata or {}).get("hnsw:space")
    # Lantai supplies embeddings; Chroma must never silently download/use a model.
    with pytest.raises(ValueError, match="embedding function"):
        store._collection.add(ids=["text-only"], documents=["must not embed"])


def test_legacy_collection_name_wins_without_creating_new_collection(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy-name")
    monkeypatch.setattr(settings, "CHROMADB_PATH", path)
    client = chromadb.PersistentClient(
        path=path, settings=ChromaSettings(anonymized_telemetry=False)
    )
    legacy = client.create_collection(
        "remembrance_vectors",
        configuration={"hnsw": {"space": "cosine"}},
        embedding_function=None,
        metadata={"migration_fixture": "legacy-name"},
    )
    legacy.add(ids=["existing"], embeddings=[[2.0, 0.0, 0.0]])
    assert all(hasattr(c, "name") for c in client.list_collections())
    store = ChromaVectorStore()
    assert store._collection.name == "remembrance_vectors"
    assert store._collection.metadata == {"migration_fixture": "legacy-name"}
    assert [c.name for c in client.list_collections()] == ["remembrance_vectors"]
    assert store.has("existing")
    assert store.search([1.0, 0.0, 0.0], 10)[0]["distance"] == pytest.approx(0, abs=1e-6)


def test_real_crud_cosine_numpy_results_and_missing_metadata(store):
    store.add(["x", "y"], [[2.0, 0.0, 0.0], [0.0, 3.0, 0.0]], [{"lane": "fact"}] * 2)
    store._collection.add(ids=["no-meta"], embeddings=[[-4.0, 0.0, 0.0]])
    raw = store._collection.get(ids=["x"], include=["embeddings"])
    assert isinstance(raw["embeddings"], np.ndarray)
    np.testing.assert_array_equal(raw["embeddings"], [[2.0, 0.0, 0.0]])
    hits = store.search([1.0, 0.0, 0.0], 50)
    assert [hit["id"] for hit in hits] == ["x", "y", "no-meta"]
    assert [hit["distance"] for hit in hits] == pytest.approx([0, 1, 2], abs=1e-6)
    assert all(type(hit["distance"]) is float for hit in hits)
    assert hits[-1]["metadata"] == {}
    store.add(["x"], [[0.0, 0.0, 2.0]], [{"lane": "rule"}])
    assert store._collection.count() == 3
    assert store.search([0.0, 0.0, 1.0], 1)[0]["metadata"] == {"lane": "rule"}
    store.delete(["x", "does-not-exist"])
    assert not store.has("x")
    assert store.has("y")
    store.delete([])
    store.add([], [], [])


def test_empty_collection_and_empty_filter_result(store):
    assert store.search([1.0, 0.0, 0.0], 5) == []
    assert not store.has("missing")
    store.delete(["missing"])
    store.add(["one"], [[1.0, 0.0, 0.0]], [{"lane": "fact"}])
    assert store.search([1.0, 0.0, 0.0], 5, {"lane": "absent"}) == []
    assert len(store.search([1.0, 0.0, 0.0], 5, {})) == 1


def test_real_acl_nested_filters_include_legacy_and_exclude_other_owner(store):
    store.add(
        ["own", "shared", "other", "wrong-lane", "missing-owner"],
        [[1.0, 0.0, 0.0]] * 5,
        [
            {"user_id": "alice", "lane": "fact", "domain": "user"},
            {"user_id": "", "lane": "fact", "domain": "user"},
            {"user_id": "bob", "lane": "fact", "domain": "user"},
            {"user_id": "alice", "lane": "rule", "domain": "user"},
            {"lane": "fact", "domain": "user"},
        ],
    )
    where = vector_owner_filter(Principal(user_id="alice"), {"lane": "fact", "domain": "user"})
    assert {hit["id"] for hit in store.search([1.0, 0.0, 0.0], 10, where)} == {"own", "shared"}
    assert len(store.search([1.0, 0.0, 0.0], 10, vector_owner_filter(Principal(role="admin")))) == 5


def test_dimension_error_preserves_previous_vector(store):
    store.add(["existing"], [[1.0, 0.0, 0.0]], [{"lane": "fact"}])
    with pytest.raises(chromadb.errors.InvalidArgumentError, match="dimension"):
        store.add(["existing"], [[1.0, 2.0]], [{"lane": "corrupt"}])
    with pytest.raises(chromadb.errors.InvalidArgumentError, match="dimension"):
        store.search([1.0, 2.0], 5)
    assert store._collection.count() == 1
    assert store.search([1.0, 0.0, 0.0], 1)[0]["metadata"] == {"lane": "fact"}


def test_vector_sync_failure_and_compensation_use_real_rust_store(store, monkeypatch):
    import lantai.storage.vector_store as vs_module
    from lantai.models.tables import MemoryItem
    from lantai.services.record_ops_service import (
        sync_vector_delete,
        sync_vector_has,
        sync_vector_upsert,
    )

    monkeypatch.setattr(vs_module, "_store", store)
    item = MemoryItem(id="compensated", content="vector compensation", user_id="alice", lane="fact")
    monkeypatch.setattr("lantai.services.record_ops_service.embed", lambda texts: [[1.0, 0.0, 0.0]])
    assert sync_vector_upsert(item.id, item)
    assert sync_vector_has(item.id) is True
    # The dependency failure is a real Rust dimension rejection, not a mock store exception.
    monkeypatch.setattr("lantai.services.record_ops_service.embed", lambda texts: [[1.0, 0.0]])
    assert sync_vector_upsert(item.id, item) is False
    assert sync_vector_has(item.id) is True
    assert sync_vector_delete(item.id)
    assert sync_vector_has(item.id) is False
    monkeypatch.setattr("lantai.services.record_ops_service.embed", lambda texts: [[0.0, 1.0, 0.0]])
    assert sync_vector_upsert(item.id, item)
    hits = store.search([0.0, 1.0, 0.0], 1, vector_owner_filter(Principal(user_id="alice")))
    assert hits[0]["id"] == item.id
    assert hits[0]["distance"] == pytest.approx(0, abs=1e-6)
    assert hits[0]["metadata"]["user_id"] == "alice"
