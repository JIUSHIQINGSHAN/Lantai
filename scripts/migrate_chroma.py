"""Explicit Chroma 0.6.3 -> 1.5.9 export/import; never open a live source.

No Lantai settings imports: every data path is an explicit CLI argument. Export
opens only a private copy under --work-dir, using Chroma 0.6.3. Import requires a
nonexistent target, uses supplied embeddings exclusively, and verifies every row.
See docs/chroma-migration.md for the cold-copy and rollback procedure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import stat
import sys
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "lantai-chroma-export-v1"
OLD_VERSION = "0.6.3"
NEW_VERSION = "1.5.9"
HNSW_KEYS = {
    "hnsw:space": "space",
    "hnsw:construction_ef": "ef_construction",
    "hnsw:search_ef": "ef_search",
    "hnsw:M": "max_neighbors",
    "hnsw:num_threads": "num_threads",
    "hnsw:resize_factor": "resize_factor",
    "hnsw:batch_size": "batch_size",
    "hnsw:sync_threshold": "sync_threshold",
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(value) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_link(path: Path) -> None:
    # Path.is_junction only exists in Python 3.12+, while the exporter runs in
    # the old 3.11 environment. Reject all Windows reparse points via lstat.
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    )
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ValueError(f"source contains a link/reparse point: {path}")


def tree_hashes(path: Path) -> dict[str, str]:
    """Read files only; check each directory before descending into it."""
    result = {}

    def visit(directory: Path) -> None:
        _reject_link(directory)
        for child in sorted(directory.iterdir()):
            _reject_link(child)
            if child.is_dir():
                visit(child)
            elif child.is_file():
                result[child.relative_to(path).as_posix()] = _sha256(child)

    visit(path)
    return result


def _disjoint(*paths: Path) -> None:
    for i, left in enumerate(paths):
        for right in paths[i + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError(f"paths must be separate, non-nested directories: {left}, {right}")


def _runtime(expected: str):
    import chromadb

    if chromadb.__version__ != expected:
        raise ValueError(f"this command requires chromadb=={expected}, got {chromadb.__version__}")
    return chromadb


def _client(path: Path, expected: str):
    chromadb = _runtime(expected)
    from chromadb.config import Settings

    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def _configuration(metadata: dict | None) -> tuple[dict, dict | None]:
    """0.6.3 configuration_json defaults can disagree with the actual HNSW metric."""
    hnsw = {"space": "l2"}
    user_metadata = {}
    for key, value in (metadata or {}).items():
        if key in HNSW_KEYS:
            hnsw[HNSW_KEYS[key]] = value
        elif key.startswith("hnsw:"):
            raise ValueError(f"unsupported legacy index setting: {key}")
        else:
            user_metadata[key] = value
    return {"hnsw": hnsw}, user_metadata or None


def _legacy_index_configuration(database_path: Path, collection_id: str) -> tuple[dict, dict]:
    """0.6.3 HNSW reads VECTOR segment metadata, not mutable collection metadata."""
    with closing(sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)) as db:
        segments = db.execute(
            "SELECT id, type FROM segments WHERE collection = ? AND scope = 'VECTOR'",
            (collection_id,),
        ).fetchall()
        if len(segments) != 1 or segments[0][1] != "urn:chroma:segment/vector/hnsw-local-persisted":
            raise ValueError("expected exactly one legacy persistent HNSW segment")
        values = db.execute(
            "SELECT key, str_value, int_value, float_value, bool_value FROM segment_metadata WHERE segment_id = ?",
            (segments[0][0],),
        ).fetchall()
    metadata = {
        key: next((value for value in fields if value is not None), None) for key, *fields in values
    }
    configuration, _ = _configuration(metadata)
    return configuration, metadata


def _rows(collection, batch_size: int):
    count = collection.count()
    for offset in range(0, count, batch_size):
        result = collection.get(
            limit=batch_size,
            offset=offset,
            include=["embeddings", "metadatas", "documents", "uris"],
        )
        vectors = result["embeddings"]
        if vectors is None or len(vectors) != len(result["ids"]):
            raise ValueError("missing stored embeddings; refusing to re-embed")
        for i, record_id in enumerate(result["ids"]):
            embedding = [float(value) for value in vectors[i]]
            if not embedding or not all(math.isfinite(value) for value in embedding):
                raise ValueError(f"invalid stored embedding for id {record_id!r}")
            yield {
                "id": record_id,
                "embedding": embedding,
                "metadata": result["metadatas"][i] if result["metadatas"] is not None else None,
                "document": result["documents"][i] if result["documents"] is not None else None,
                "uri": result["uris"][i] if result["uris"] is not None else None,
            }


def _matches(metadata: dict | None, where: dict | None) -> bool:
    if where is None:
        return True
    metadata = metadata or {}
    if "$and" in where:
        return all(_matches(metadata, part) for part in where["$and"])
    if "$or" in where:
        return any(_matches(metadata, part) for part in where["$or"])
    return all(metadata.get(key) == value for key, value in where.items())


def _filters(rows: list[dict]) -> list[dict | None]:
    """Probe each stored owner and owner/lane/domain boundary, plus an absent owner."""
    filters: dict[str, dict | None] = {"null": None}
    owners = {row["metadata"].get("user_id") for row in rows if row["metadata"] is not None}
    absent = "__lantai_migration_absent_owner__"
    while absent in owners:
        absent += "_"
    for owner in sorted({value for value in owners if isinstance(value, str)} | {absent}):
        value = {"$or": [{"user_id": owner}, {"user_id": ""}]}
        filters[_json(value)] = value
    for row in rows:
        metadata = row["metadata"] or {}
        owner = metadata.get("user_id")
        parts = []
        if isinstance(owner, str):
            parts.append({"$or": [{"user_id": owner}, {"user_id": ""}]})
        parts.extend({key: metadata[key]} for key in ("lane", "domain") if key in metadata)
        if parts:
            value = parts[0] if len(parts) == 1 else {"$and": parts}
            filters[_json(value)] = value
    return list(filters.values())


def _get_ids(collection, where: dict | None) -> set[str]:
    kwargs = {"where": where} if where else {}
    return set(collection.get(include=[], **kwargs)["ids"])


def _query(collection, embedding: list[float], top_k: int, where: dict | None) -> dict[str, float]:
    kwargs = {"where": where} if where else {}
    result = collection.query(
        query_embeddings=[embedding], n_results=top_k, include=["distances"], **kwargs
    )
    return dict(zip(result["ids"][0], map(float, result["distances"][0]), strict=True))


def _probes(
    collection, rows: list[dict], dimension: int | None, probe_count: int, all_self_probes: bool
) -> list[dict]:
    if rows:
        ordered = sorted(rows, key=lambda row: row["id"])
        step = max(1, len(ordered) // probe_count)
        vectors = [row["embedding"] for row in ordered[::step][:probe_count]]
    else:
        vectors = [[1.0] + [0.0] * ((dimension or 3) - 1)]
    probes = []
    for where in _filters(rows):
        expected = {row["id"] for row in rows if _matches(row["metadata"], where)}
        if _get_ids(collection, where) != expected:
            raise ValueError("old collection filter does not match exported metadata")
        for embedding in vectors:
            distances = _query(collection, embedding, 10, where)
            if not set(distances).issubset(expected):
                raise ValueError("old query returned a row outside the ACL filter")
            probes.append(
                {
                    "where": where,
                    "embedding": embedding,
                    "top_k": 10,
                    "allowed_ids": sorted(expected),
                    "distances": distances,
                }
            )
    if all_self_probes:
        for row in rows:
            probes.append(
                {
                    "where": None,
                    "embedding": row["embedding"],
                    "top_k": 10,
                    "self_id": row["id"],
                    "distances": _query(collection, row["embedding"], 10, None),
                }
            )
    return probes


def export_copy(
    source_copy: Path,
    bundle: Path,
    work_dir: Path,
    *,
    batch_size: int = 500,
    probe_count: int = 3,
    all_self_probes: bool = False,
) -> dict:
    """Use 0.6.3 read APIs on a private clone; supplied cold-copy bytes stay unchanged."""
    _runtime(OLD_VERSION)
    if batch_size < 1 or probe_count < 1:
        raise ValueError("batch_size and probe_count must be positive")
    source_copy, bundle, work_dir = (path.resolve() for path in (source_copy, bundle, work_dir))
    _disjoint(source_copy, bundle, work_dir)
    if not (source_copy / "chroma.sqlite3").is_file():
        raise ValueError("--source-copy must contain an existing chroma.sqlite3")
    if bundle.exists() or work_dir.exists():
        raise ValueError("bundle and work-dir must not exist; choose fresh paths")
    before = tree_hashes(source_copy)
    # copytree only reads source_copy. Chroma has no read-only PersistentClient;
    # this private directory absorbs housekeeping writes from otherwise read APIs.
    shutil.copytree(source_copy, work_dir)
    if tree_hashes(source_copy) != before or tree_hashes(work_dir) != before:
        raise ValueError("source changed during copying; stop writers and obtain a cold copy")
    with closing(
        sqlite3.connect(f"{(work_dir / 'chroma.sqlite3').as_uri()}?mode=ro", uri=True)
    ) as db:
        databases = db.execute("SELECT name, tenant_id FROM databases").fetchall()
        if databases != [("default_database", "default_tenant")]:
            raise ValueError(
                "only Lantai's default tenant/database is supported; refusing a partial export"
            )
    bundle.mkdir(parents=True)
    client = _client(work_dir, OLD_VERSION)
    manifest = {
        "format": FORMAT,
        "source_version": OLD_VERSION,
        "target_version": NEW_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_path": str(source_copy),
        "source_files": before,
        "collections": [],
    }
    names = sorted(
        value if isinstance(value, str) else value.name for value in client.list_collections()
    )
    for index, name in enumerate(names):
        collection = client.get_collection(name, embedding_function=None)
        rows = list(_rows(collection, batch_size))
        ids = {row["id"] for row in rows}
        if len(ids) != len(rows) or len(rows) != collection.count():
            raise ValueError(f"duplicate ids or changed count in {name}")
        dimensions = {len(row["embedding"]) for row in rows}
        if len(dimensions) > 1:
            raise ValueError(f"mixed embedding dimensions in {name}")
        # Empty-but-previously-used collections still have a dimension contract.
        with closing(
            sqlite3.connect(f"{(work_dir / 'chroma.sqlite3').as_uri()}?mode=ro", uri=True)
        ) as db:
            dimension = db.execute(
                "SELECT dimension FROM collections WHERE id = ?", (str(collection.id),)
            ).fetchone()[0]
        if dimensions and dimension not in dimensions:
            raise ValueError(f"stored dimension disagrees with vectors in {name}")
        declared_configuration, metadata = _configuration(collection.metadata)
        configuration, segment_metadata = _legacy_index_configuration(
            work_dir / "chroma.sqlite3", str(collection.id)
        )
        filename = f"collection-{index:04d}.jsonl"
        with (bundle / filename).open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(_json(row) + "\n")
        manifest["collections"].append(
            {
                "name": name,
                "count": len(rows),
                "dimension": dimension,
                "source_metadata": collection.metadata,
                "metadata": metadata,
                "source_segment_metadata": segment_metadata,
                "declared_configuration": declared_configuration,
                "configuration_source": "legacy_vector_segment_metadata",
                "configuration": configuration,
                "file": filename,
                "sha256": _sha256(bundle / filename),
                "probes": _probes(collection, rows, dimension, probe_count, all_self_probes),
            }
        )
    if tree_hashes(source_copy) != before:
        raise ValueError("source changed during export; bundle is not a valid cold snapshot")
    _write_json(bundle / "manifest.json", manifest)
    return {
        "ok": True,
        "collections": len(names),
        "records": sum(c["count"] for c in manifest["collections"]),
        "source_unchanged": True,
        "bundle": str(bundle),
        "work_dir": str(work_dir),
    }


def _load_bundle(bundle: Path) -> tuple[dict, list[tuple[dict, list[dict]]]]:
    with (bundle / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if (manifest.get("format"), manifest.get("source_version"), manifest.get("target_version")) != (
        FORMAT,
        OLD_VERSION,
        NEW_VERSION,
    ):
        raise ValueError("unsupported migration bundle format or versions")
    collections = []
    names = set()
    for spec in manifest["collections"]:
        filename = spec["file"]
        if Path(filename).name != filename or not filename.endswith(".jsonl"):
            raise ValueError("invalid collection file path")
        path = bundle / filename
        if _sha256(path) != spec["sha256"]:
            raise ValueError(f"checksum mismatch: {filename}")
        with path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if len(rows) != spec["count"] or len({row["id"] for row in rows}) != len(rows):
            raise ValueError("record count or duplicate ids in bundle")
        if spec["name"] in names:
            raise ValueError("duplicate collection name")
        names.add(spec["name"])
        for row in rows:
            if len(row["embedding"]) != spec["dimension"] or not all(
                math.isfinite(x) for x in row["embedding"]
            ):
                raise ValueError("invalid vector dimension or value in bundle")
        collections.append((spec, rows))
    return manifest, collections


def _insert(collection, rows: list[dict], batch_size: int) -> None:
    # Chroma rejects all-None optional fields. Group by presence to preserve absent
    # metadata/documents/URIs without inventing empty dictionaries or empty strings.
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] is not None for key in ("metadata", "document", "uri"))].append(row)
    for presence, group in groups.items():
        for offset in range(0, len(group), batch_size):
            batch = group[offset : offset + batch_size]
            kwargs = {
                "ids": [row["id"] for row in batch],
                "embeddings": [row["embedding"] for row in batch],
            }
            for present, key, argument in zip(
                presence,
                ("metadata", "document", "uri"),
                ("metadatas", "documents", "uris"),
                strict=True,
            ):
                if present:
                    kwargs[argument] = [row[key] for row in batch]
            collection.add(**kwargs)


def _effective_configuration(spec: dict, search_ef: int | None) -> dict:
    hnsw = dict(spec["configuration"]["hnsw"])
    hnsw.setdefault("ef_search", 100)
    if search_ef is not None:
        if search_ef < hnsw["ef_search"]:
            raise ValueError("explicit hnsw-search-ef must not reduce the source search breadth")
        hnsw["ef_search"] = search_ef
    return {"hnsw": hnsw}


def _oracle_distances(matrix, query, metric):
    import numpy as np

    if metric == "l2":
        return np.sum((matrix - query) ** 2, axis=1)
    if metric == "ip":
        return 1.0 - matrix @ query
    if metric == "cosine":
        denominator = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
        return 1.0 - np.divide(
            matrix @ query, denominator, out=np.zeros(len(matrix)), where=denominator != 0
        )
    raise ValueError(f"unsupported distance metric: {metric}")


def _verify_collection(
    collection,
    spec: dict,
    expected: list[dict],
    batch_size: int,
    tolerance: float,
    max_vector_ulps: int,
    search_ef: int | None = None,
) -> dict:
    import numpy as np

    actual = {row["id"]: row for row in _rows(collection, batch_size)}
    if set(actual) != {row["id"] for row in expected}:
        raise ValueError(f"id set mismatch in {spec['name']}")
    changed_vectors = changed_elements = largest_ulp = 0
    max_vector_delta = max_norm_delta = 0.0
    permitted_ulps = max_vector_ulps if spec["configuration"]["hnsw"]["space"] == "cosine" else 0
    for row in expected:
        found = actual[row["id"]]
        before = np.asarray(row["embedding"], dtype=np.float32)
        after = np.asarray(found["embedding"], dtype=np.float32)
        if before.shape != after.shape or not np.isfinite(after).all():
            raise ValueError(f"invalid vector shape/value in {spec['name']}")
        changed = before != after
        if changed.any():
            # For equal signs, IEEE float32 bit distance is the number of ULPs.
            # A changed sign is never rounding, except equal +/- zero above.
            ulps = np.abs(
                before.view(np.int32).astype(np.int64) - after.view(np.int32).astype(np.int64)
            )
            if (
                np.any(np.signbit(before[changed]) != np.signbit(after[changed]))
                or int(ulps.max()) > permitted_ulps
            ):
                raise ValueError(
                    f"vector mismatch in {spec['name']} id={row['id']!r}; max ULP={int(ulps.max())}, permitted={permitted_ulps}"
                )
            changed_vectors += 1
            changed_elements += int(changed.sum())
            largest_ulp = max(largest_ulp, int(ulps.max()))
            max_vector_delta = max(
                max_vector_delta,
                float(np.max(np.abs(before.astype(np.float64) - after.astype(np.float64)))),
            )
        norm_before, norm_after = (
            float(np.linalg.norm(before.astype(np.float64))),
            float(np.linalg.norm(after.astype(np.float64))),
        )
        norm_delta = abs(norm_before - norm_after)
        max_norm_delta = max(max_norm_delta, norm_delta)
        if norm_delta > permitted_ulps * np.finfo(np.float32).eps * norm_before:
            raise ValueError(f"vector norm drift in {spec['name']}")
        if any(_json(row[key]) != _json(found[key]) for key in ("metadata", "document", "uri")):
            raise ValueError(f"metadata/document/uri mismatch in {spec['name']} id={row['id']!r}")
    if _json(collection.metadata) != _json(spec["metadata"]):
        raise ValueError(f"collection metadata mismatch in {spec['name']}")
    effective_configuration = _effective_configuration(spec, search_ef)
    for key, value in effective_configuration["hnsw"].items():
        if collection.configuration["hnsw"][key] != value:
            raise ValueError(f"index configuration mismatch in {spec['name']}: {key}")
    max_delta = max_old_error = max_new_error = 0.0
    old_recall_sum = new_recall_sum = 0.0
    old_recall_min = new_recall_min = 1.0
    oracle_ids = [row["id"] for row in expected]
    oracle_index = {key: i for i, key in enumerate(oracle_ids)}
    oracle_matrix = np.asarray([row["embedding"] for row in expected], dtype=np.float64)
    checked_filters = set()
    changed_query_sets = self_queries = self_hits_before = self_hits_after = 0
    for probe in spec["probes"]:
        allowed = set(probe["allowed_ids"]) if "allowed_ids" in probe else set(actual)
        filter_key = _json(probe["where"])
        if filter_key not in checked_filters:
            if _get_ids(collection, probe["where"]) != allowed:
                raise ValueError(f"ACL/filter membership mismatch in {spec['name']}")
            checked_filters.add(filter_key)
        observed = _query(collection, probe["embedding"], probe["top_k"], probe["where"])
        if not set(observed).issubset(allowed):
            raise ValueError(f"query escaped ACL filter in {spec['name']}")
        prior = probe["distances"]
        if set(observed) != set(prior):
            changed_query_sets += 1
        if "self_id" in probe:
            self_queries += 1
            self_hits_before += probe["self_id"] in prior
            self_hits_after += probe["self_id"] in observed
        # ANN rebuilds may choose different IDs at exactly tied boundaries. Compare
        # ranked distances and common IDs; report count rather than claiming order.
        if len(observed) != len(prior):
            raise ValueError(f"query result count mismatch in {spec['name']}")
        deltas = [
            abs(a - b)
            for a, b in zip(sorted(observed.values()), sorted(prior.values()), strict=True)
        ]
        deltas += [abs(observed[key] - prior[key]) for key in observed.keys() & prior.keys()]
        delta = max(deltas, default=0.0)
        max_delta = max(max_delta, delta)
        # Compare each engine with the exact metric oracle. A new graph may find
        # closer neighbors that the old ANN missed; this is not data corruption.
        oracle = (
            _oracle_distances(
                oracle_matrix,
                np.asarray(probe["embedding"], dtype=np.float64),
                effective_configuration["hnsw"]["space"],
            )
            if expected
            else np.asarray([])
        )
        k = min(probe["top_k"], len(allowed))
        if len(observed) != k or not set(prior).issubset(allowed):
            raise ValueError(f"query count or old ACL mismatch in {spec['name']}")
        cutoff = sorted(float(oracle[oracle_index[key]]) for key in allowed)[k - 1] if k else 0.0
        acceptable = {key for key in allowed if oracle[oracle_index[key]] <= cutoff + tolerance}
        old_recall = len(set(prior) & acceptable) / k if k else 1.0
        new_recall = len(set(observed) & acceptable) / k if k else 1.0
        if new_recall < old_recall:
            raise ValueError(
                f"ANN recall regressed in {spec['name']}: {new_recall} < {old_recall}; review explicit hnsw-search-ef"
            )
        old_recall_sum += old_recall
        new_recall_sum += new_recall
        old_recall_min = min(old_recall_min, old_recall)
        new_recall_min = min(new_recall_min, new_recall)
        old_error = max(
            (abs(value - oracle[oracle_index[key]]) for key, value in prior.items()), default=0.0
        )
        new_error = max(
            (abs(value - oracle[oracle_index[key]]) for key, value in observed.items()), default=0.0
        )
        max_old_error = max(max_old_error, float(old_error))
        max_new_error = max(max_new_error, float(new_error))
        if not all(math.isfinite(value) for value in observed.values()) or new_error > tolerance:
            raise ValueError(
                f"query metric differs from oracle in {spec['name']}: {new_error} > {tolerance}"
            )
    return {
        "name": spec["name"],
        "records": len(expected),
        "dimension": spec["dimension"],
        "ids_equal": True,
        "vectors_float32_exact": changed_vectors == 0,
        "vectors_within_permitted_ulps": True,
        "permitted_vector_ulps": permitted_ulps,
        "changed_vectors": changed_vectors,
        "changed_vector_elements": changed_elements,
        "max_vector_ulps": largest_ulp,
        "max_vector_absolute_delta": max_vector_delta,
        "max_vector_norm_delta": max_norm_delta,
        "metadata_equal": True,
        "documents_equal": True,
        "uris_equal": True,
        "probes": len(spec["probes"]),
        "query_id_sets_equal": changed_query_sets == 0,
        "changed_query_id_sets": changed_query_sets,
        "self_queries": self_queries,
        "self_hits_before": self_hits_before,
        "self_hits_after": self_hits_after,
        "filter_membership_checks": len(checked_filters),
        "max_distance_delta": max_delta,
        "acl_membership_equal": True,
        "old_recall_at_k": old_recall_sum / len(spec["probes"]) if spec["probes"] else 1.0,
        "new_recall_at_k": new_recall_sum / len(spec["probes"]) if spec["probes"] else 1.0,
        "old_recall_min": old_recall_min,
        "new_recall_min": new_recall_min,
        "new_recall_not_worse": True,
        "max_old_oracle_distance_error": max_old_error,
        "max_new_oracle_distance_error": max_new_error,
        "source_search_ef": spec["configuration"]["hnsw"].get("ef_search", 100),
        "effective_configuration": effective_configuration,
    }


def verify_bundle(
    bundle: Path,
    target: Path,
    *,
    batch_size: int = 500,
    tolerance: float = 1e-5,
    max_vector_ulps: int = 0,
) -> dict:
    _runtime(NEW_VERSION)
    if (
        batch_size < 1
        or not math.isfinite(tolerance)
        or tolerance < 0
        or max_vector_ulps not in (0, 1)
    ):
        raise ValueError("invalid batch_size, distance tolerance or max_vector_ulps (only 0/1)")
    bundle, target = bundle.resolve(), target.resolve()
    _disjoint(bundle, target)
    manifest, collections = _load_bundle(bundle)
    _disjoint(target, Path(manifest["source_path"]).resolve())
    if not (target / "chroma.sqlite3").is_file():
        raise ValueError("target database is missing")
    # A tool-owned marker is written before creating the new DB, so a mistaken
    # verify --target <old-dir> never opens/auto-migrates a legacy database.
    marker = target / ".lantai-chroma-import.json"
    if not marker.is_file():
        raise ValueError("target is not an import of this bundle; refusing to open it")
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    if marker_data.get("manifest_sha256") != _sha256(bundle / "manifest.json"):
        raise ValueError("target is not an import of this bundle; refusing to open it")
    search_ef = marker_data.get("hnsw_search_ef")
    client = _client(target, NEW_VERSION)
    if {c.name for c in client.list_collections()} != {spec["name"] for spec, _ in collections}:
        raise ValueError("collection set mismatch")
    with closing(
        sqlite3.connect(f"{(target / 'chroma.sqlite3').as_uri()}?mode=ro", uri=True)
    ) as db:
        dimensions = dict(db.execute("SELECT name, dimension FROM collections").fetchall())
    if dimensions != {spec["name"]: spec["dimension"] for spec, _ in collections}:
        raise ValueError("persisted collection dimensions differ")
    reports = [
        _verify_collection(
            client.get_collection(spec["name"], embedding_function=None),
            spec,
            rows,
            batch_size,
            tolerance,
            max_vector_ulps,
            search_ef,
        )
        for spec, rows in collections
    ]
    client.close()
    return {
        "ok": True,
        "source_version": OLD_VERSION,
        "target_version": NEW_VERSION,
        "target": str(target),
        "distance_tolerance": tolerance,
        "max_vector_ulps": max_vector_ulps,
        "collections": reports,
        "records": sum(report["records"] for report in reports),
    }


def import_bundle(
    bundle: Path,
    target: Path,
    *,
    batch_size: int = 500,
    tolerance: float = 1e-5,
    max_vector_ulps: int = 0,
    hnsw_search_ef: int | None = None,
) -> dict:
    _runtime(NEW_VERSION)
    if (
        batch_size < 1
        or not math.isfinite(tolerance)
        or tolerance < 0
        or max_vector_ulps not in (0, 1)
    ):
        raise ValueError("invalid batch_size, distance tolerance or max_vector_ulps (only 0/1)")
    bundle, target = bundle.resolve(), target.resolve()
    manifest, collections = _load_bundle(bundle)
    _disjoint(bundle, target, Path(manifest["source_path"]).resolve())
    if target.exists():
        raise ValueError("target must not exist; existing/production directories are never opened")
    for spec, _ in collections:
        _effective_configuration(spec, hnsw_search_ef)
    target.mkdir(parents=True)
    _write_json(
        target / ".lantai-chroma-import.json",
        {
            "manifest_sha256": _sha256(bundle / "manifest.json"),
            "target_version": NEW_VERSION,
            "hnsw_search_ef": hnsw_search_ef,
        },
    )
    client = _client(target, NEW_VERSION)
    batch_size = min(batch_size, client.get_max_batch_size())
    for spec, rows in collections:
        kwargs = {"metadata": spec["metadata"]} if spec["metadata"] is not None else {}
        collection = client.create_collection(
            spec["name"],
            configuration=_effective_configuration(spec, hnsw_search_ef),
            embedding_function=None,
            **kwargs,
        )
        if not rows and spec["dimension"] is not None:
            # Public API has no dimension argument. Seed/delete an explicit vector
            # only for empty collections to retain their established dimension.
            collection.add(ids=["__migration_dimension__"], embeddings=[[0.0] * spec["dimension"]])
            collection.delete(ids=["__migration_dimension__"])
        _insert(collection, rows, batch_size)
    # Release writer/index caches before reopening for the first verification.
    # Deployment still requires a separate CLI verify process, documented below.
    client.close()
    report = verify_bundle(
        bundle, target, batch_size=batch_size, tolerance=tolerance, max_vector_ulps=max_vector_ulps
    )
    _write_json(target / "migration-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="0.6.3: read a cold copy via a private work copy")
    export.add_argument("--source-copy", type=Path, required=True)
    export.add_argument("--bundle", type=Path, required=True)
    export.add_argument("--work-dir", type=Path, required=True)
    export.add_argument("--probe-count", type=int, default=3)
    export.add_argument(
        "--all-self-probes",
        action="store_true",
        help="also replay every stored vector as a top-10 query",
    )
    export.add_argument("--batch-size", type=int, default=500)
    for name in ("import", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--target", type=Path, required=True)
        command.add_argument("--batch-size", type=int, default=500)
        command.add_argument("--distance-tolerance", type=float, default=1e-5)
        command.add_argument(
            "--max-vector-ulps",
            type=int,
            choices=(0, 1),
            default=0,
            help="explicitly accept at most one float32 ULP of cosine HNSW rounding (default exact)",
        )
        if name == "import":
            command.add_argument(
                "--hnsw-search-ef",
                type=int,
                default=None,
                help="explicit new search breadth; source setting preserved when omitted",
            )
    args = parser.parse_args(argv)
    try:
        native_path = args.work_dir if args.command == "export" else args.target
        if sys.platform == "win32" and not str(native_path.resolve()).isascii():
            raise ValueError("Windows native HNSW work-dir/target requires an ASCII-only path")
        if args.command == "export":
            report = export_copy(
                args.source_copy,
                args.bundle,
                args.work_dir,
                batch_size=args.batch_size,
                probe_count=args.probe_count,
                all_self_probes=args.all_self_probes,
            )
        else:
            operation = import_bundle if args.command == "import" else verify_bundle
            extra = {"hnsw_search_ef": args.hnsw_search_ef} if args.command == "import" else {}
            report = operation(
                args.bundle,
                args.target,
                batch_size=args.batch_size,
                tolerance=args.distance_tolerance,
                max_vector_ulps=args.max_vector_ulps,
                **extra,
            )
    except Exception as exc:
        print(f"Migration refused/failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
