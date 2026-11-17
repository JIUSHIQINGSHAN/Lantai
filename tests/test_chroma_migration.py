"""Import a committed real 0.6.3 export into real Rust Chroma, no storage mocks."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import chromadb
import numpy as np
import pytest

from scripts.migrate_chroma import (
    _client,
    _configuration,
    _probes,
    _verify_collection,
    import_bundle,
    main,
    tree_hashes,
    verify_bundle,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chroma-0.6.3"


def test_real_legacy_export_import_and_verify(tmp_path):
    before = tree_hashes(FIXTURE)
    target = tmp_path / "migrated"
    report = import_bundle(FIXTURE, target, batch_size=2)
    assert report["ok"]
    assert report["records"] == 4
    assert all(
        row["vectors_float32_exact"] and row["acl_membership_equal"]
        for row in report["collections"]
    )
    assert all(row["max_distance_delta"] <= 1e-5 for row in report["collections"])
    assert tree_hashes(FIXTURE) == before
    assert verify_bundle(FIXTURE, target, batch_size=1) == report
    client = _client(target, "1.5.9")
    assert {c.name for c in client.list_collections()} == {
        "remembrance_vectors",
        "empty-used",
        "empty-new",
    }
    collection = client.get_collection("remembrance_vectors", embedding_function=None)
    assert collection.configuration["hnsw"]["max_neighbors"] == 24
    assert collection.get(ids=["missing-meta"])["metadatas"] == [None]
    assert collection.get(ids=["a"])["metadatas"] == [
        {"user_id": "alice", "lane": "fact", "domain": "user", "n": 1, "flag": True}
    ]
    empty = client.get_collection("empty-used", embedding_function=None)
    assert empty.count() == 0
    with pytest.raises(chromadb.errors.InvalidArgumentError, match="dimension"):
        empty.add(ids=["wrong-dimension"], embeddings=[[1.0, 2.0]])
    with pytest.raises(ValueError, match="embedding function"):
        collection.add(ids=["implicit-embedding"], documents=["must not embed"])


def test_import_refuses_existing_target_before_opening_it(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "chroma.sqlite3").write_bytes(b"never open or migrate this directory")
    before = tree_hashes(target)
    with pytest.raises(ValueError, match="must not exist"):
        import_bundle(FIXTURE, target)
    assert tree_hashes(target) == before


def test_verify_refuses_unmarked_legacy_directory(tmp_path):
    target = tmp_path / "unmarked"
    target.mkdir()
    (target / "chroma.sqlite3").write_bytes(b"never open this database")
    before = tree_hashes(target)
    with pytest.raises(ValueError, match="refusing to open"):
        verify_bundle(FIXTURE, target)
    assert tree_hashes(target) == before


def test_import_detects_corruption_before_creating_target(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(FIXTURE, bundle)
    (bundle / "collection-0002.jsonl").write_text("corrupted\n", encoding="utf-8")
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="checksum mismatch"):
        import_bundle(bundle, target)
    assert not target.exists()


def test_verify_detects_same_count_different_ids_and_changed_metadata(tmp_path):
    target = tmp_path / "target"
    import_bundle(FIXTURE, target)
    collection = _client(target, "1.5.9").get_collection(
        "remembrance_vectors", embedding_function=None
    )
    collection.update(ids=["a"], metadatas=[{"user_id": "mallory"}])
    with pytest.raises(ValueError, match="metadata/document/uri mismatch"):
        verify_bundle(FIXTURE, target)
    collection.delete(ids=["a"])
    collection.add(ids=["replacement"], embeddings=[[2.0, 0.0, 1.0]])
    with pytest.raises(ValueError, match="id set mismatch"):
        verify_bundle(FIXTURE, target)


def test_verify_detects_changed_vectors(tmp_path):
    target = tmp_path / "target"
    import_bundle(FIXTURE, target)
    collection = _client(target, "1.5.9").get_collection(
        "remembrance_vectors", embedding_function=None
    )
    collection.update(ids=["a"], embeddings=[[0.0, 2.0, 1.0]])
    with pytest.raises(ValueError, match="vector mismatch"):
        verify_bundle(FIXTURE, target)


def test_explicit_search_ef_persists_through_fresh_process(tmp_path):
    target = tmp_path / "ef-migrated"
    report = import_bundle(FIXTURE, target, hnsw_search_ef=1000)
    assert all(
        row["effective_configuration"]["hnsw"]["ef_search"] == 1000 for row in report["collections"]
    )
    assert all(row["source_search_ef"] == 100 for row in report["collections"])
    # subprocess uses the real interpreter/backend and a report file, never an in-process mock.
    output = tmp_path / "fresh.json"
    code = (
        "import json,sys; from pathlib import Path; from scripts.migrate_chroma import verify_bundle; "
        "r=verify_bundle(Path(sys.argv[1]),Path(sys.argv[2])); "
        "Path(sys.argv[3]).write_text(json.dumps(r),encoding='utf-8')"
    )
    subprocess.run(
        [sys.executable, "-B", "-c", code, str(FIXTURE), str(target), str(output)], check=True
    )
    fresh = json.loads(output.read_text(encoding="utf-8"))
    assert fresh["ok"] and fresh["records"] == 4
    assert all(row["new_recall_not_worse"] for row in fresh["collections"])


def test_real_cosine_one_ulp_is_explicit_and_larger_changes_fail(tmp_path):
    client = _client(tmp_path / "rounding", "1.5.9")
    collection = client.create_collection(
        "rounding", configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
    )
    vectors = np.random.default_rng(42).normal(size=(220, 16)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    collection.add(ids=[str(i) for i in range(len(vectors))], embeddings=vectors)
    rows = [
        {
            "id": str(i),
            "embedding": vector.tolist(),
            "metadata": None,
            "document": None,
            "uri": None,
        }
        for i, vector in enumerate(vectors)
    ]
    spec = {
        "name": "rounding",
        "dimension": 16,
        "metadata": None,
        "configuration": {"hnsw": {"space": "cosine"}},
        "probes": _probes(collection, rows, 16, 3, True),
    }
    with pytest.raises(ValueError, match="vector mismatch"):
        _verify_collection(collection, spec, rows, 500, 1e-5, 0)
    report = _verify_collection(collection, spec, rows, 500, 1e-5, 1)
    assert report["max_vector_ulps"] == 1
    assert report["changed_vectors"] > 0
    assert report["vectors_float32_exact"] is False
    collection.update(ids=["0"], embeddings=[[1.0] + [0.0] * 15])
    with pytest.raises(ValueError, match="vector mismatch"):
        _verify_collection(collection, spec, rows, 500, 1e-5, 1)
    client.close()


def test_cli_refuses_non_ascii_native_path_before_creating_it(tmp_path, monkeypatch, capsys):
    import scripts.migrate_chroma as migration

    # Only the command's platform input changes. Its real path check rejects
    # before import_bundle can construct a native client, on every test host.
    monkeypatch.setattr(migration, "sys", SimpleNamespace(platform="win32", stderr=sys.stderr))
    target = tmp_path / "索引"
    assert main(["import", "--bundle", str(FIXTURE), "--target", str(target)]) == 1
    assert "ASCII-only path" in capsys.readouterr().err
    assert not target.exists()


def test_configuration_translation_uses_legacy_hnsw_metadata():
    configuration, metadata = _configuration(
        {"hnsw:space": "cosine", "hnsw:M": 24, "custom": "kept"}
    )
    assert configuration == {"hnsw": {"space": "cosine", "max_neighbors": 24}}
    assert metadata == {"custom": "kept"}
    with pytest.raises(ValueError, match="unsupported legacy index"):
        _configuration({"hnsw:unknown": 1})


def test_cli_import_and_verify_report_success(tmp_path, capsys):
    target = tmp_path / "target"
    assert main(["import", "--bundle", str(FIXTURE), "--target", str(target)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["records"] == 4
    assert main(["verify", "--bundle", str(FIXTURE), "--target", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]


def test_export_wrong_runtime_fails_before_accessing_source(tmp_path, capsys):
    assert (
        main(
            [
                "export",
                "--source-copy",
                str(tmp_path / "source"),
                "--bundle",
                str(tmp_path / "bundle"),
                "--work-dir",
                str(tmp_path / "work"),
            ]
        )
        == 1
    )
    assert "requires chromadb==0.6.3" in capsys.readouterr().err
    assert not (tmp_path / "work").exists()
