"""Offline startup/index/search benchmark; every sample uses synthetic isolated data.

Run with the frozen development interpreter, for example:
    python scripts/perf_stage6.py --scale 1000 5000 --out .scratch/stage6-perf/before.json

Cold means a new process, not a flushed OS disk cache. Embedding is deterministic
and external LLM/reranking are disabled. Chroma must be real, never a substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("at least one sample is required")
    return {
        "iterations": len(samples),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "mean_ms": statistics.fmean(ordered),
        "raw_ms": samples,
    }


def isolated_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env.update(
        LANTAI_HOME=str(home),
        REMEMBRANCE_HOME=str(home),
        DATABASE_URL=f"sqlite:///{(home / 'bench.db').as_posix()}",
        CHROMADB_PATH=str(home / ".chromadb"),
        OPENAI_API_KEY="stage6-local-only",
        OPENAI_BASE_URL="http://127.0.0.1:9/v1",
        LANTAI_INTENT_OFF="1",
        RERANKER_ENABLED="false",
        LANTAI_RUN_SCHEDULER="false",
        MONITOR_ENABLED="false",
        COALESCE_ENABLED="false",
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        TEMP=str(home),
        TMP=str(home),
        TMPDIR=str(home),
    )
    return env


def cold_worker(spawn_start: float) -> dict:
    # The frozen environment's editable installation may point to another worktree.
    sys.path.insert(0, str(ROOT))
    start = time.perf_counter()
    import lantai.cli.mcp as mcp

    imported = time.perf_counter()
    lazy_modules = {name: name in sys.modules for name in ("chromadb", "sklearn", "torch")}
    from lantai.storage import db

    if not Path(mcp.__file__).resolve().is_relative_to(ROOT):
        raise AssertionError("MCP imported outside the requested worktree")
    if not Path(db.__file__).resolve().is_relative_to(ROOT):
        raise AssertionError("storage imported outside the requested worktree")
    db.init_db()
    ready = time.perf_counter()
    with db.engine.connect() as conn:
        from sqlalchemy import text

        version = conn.execute(text("PRAGMA user_version")).scalar_one()
    if not db.FTS_OK or db.get_migration_failures() or version != db.CURRENT_SCHEMA_VERSION:
        raise AssertionError("cold startup did not initialize the real schema and FTS")
    return {
        "mcp_import_ms": (imported - start) * 1000,
        "db_init_ms": (ready - imported) * 1000,
        "spawn_to_ready_ms": (ready - spawn_start) * 1000,
        "loaded_modules": len(sys.modules),
        "lazy_modules_loaded": lazy_modules,
        "schema_version": version,
        "source_root": str(ROOT),
    }


def scale_worker(scale: int, iterations: int, workers: int) -> dict:
    # Import from the script directory, without changing the product namespace.
    from perf_bench import bench_env

    start = time.perf_counter()
    home = Path(os.environ["LANTAI_HOME"])
    with bench_env(scale, seed=20261107, workdir=home) as ctx:
        if ctx.vector_backend != "chromadb-hnsw":
            raise RuntimeError(f"real Chroma required, got {ctx.vector_backend}")
        setup_ms = (time.perf_counter() - start) * 1000
        from lantai.retrieval.hybrid import hybrid_search
        from lantai.storage.fts import rebuild_fts, search_fts, search_fts_bm25

        if not Path(sys.modules[rebuild_fts.__module__].__file__).resolve().is_relative_to(ROOT):
            raise AssertionError("FTS imported outside the requested worktree")
        rebuild_samples = []
        for _ in range(5):
            with ctx.session_factory() as session:
                start = time.perf_counter()
                count = rebuild_fts(session)
                session.commit()
                rebuild_samples.append((time.perf_counter() - start) * 1000)
                assert count == scale
        with ctx.session_factory() as session:
            from sqlalchemy import text

            rows = session.execute(
                text("SELECT memory_id, content FROM memory_fts ORDER BY memory_id")
            ).all()
        index_digest = hashlib.sha256(
            json.dumps([list(r) for r in rows], ensure_ascii=False).encode()
        ).hexdigest()
        assert len(rows) == scale

        def search(name: str, i: int) -> tuple[float, list[str]]:
            query = ctx.hit_queries[i % len(ctx.hit_queries)]
            start = time.perf_counter()
            if name == "hybrid_search":
                result = hybrid_search(query, top_k=5, use_rerank=False)
                ids = [r["memory"]["id"] for r in result]
            else:
                with ctx.session_factory() as session:
                    raw = session.connection().connection.driver_connection
                    if name == "search_fts":
                        ids = search_fts(raw, query, top_k=10, principal=ctx.default_principal)
                    else:
                        hits = search_fts_bm25(
                            raw, query, top_k=30, principal=ctx.default_principal
                        )
                        ids = [r[0] for r in hits]
            elapsed = (time.perf_counter() - start) * 1000
            if not ids:
                raise AssertionError(f"{name} returned no hits for query {i}")
            return elapsed, ids

        scenarios = {}
        for name in ("search_fts", "search_fts_bm25", "hybrid_search"):
            for i in range(len(ctx.hit_queries)):
                search(name, i)
            samples = [search(name, i) for i in range(iterations)]
            scenarios[name] = summarize([r[0] for r in samples])
            scenarios[name]["hit_ids"] = [r[1] for r in samples]

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            parallel = list(pool.map(lambda i: search("hybrid_search", i), range(iterations)))
        elapsed = time.perf_counter() - start
        scenarios["hybrid_concurrent"] = summarize([r[0] for r in parallel])
        scenarios["hybrid_concurrent"].update(
            workers=workers, throughput_per_s=iterations / elapsed, hit_ids=[r[1] for r in parallel]
        )
        return {
            "scale": scale,
            "seed": 20261107,
            "vector_backend": ctx.vector_backend,
            "source_root": str(ROOT),
            "setup_including_seed_ms": setup_ms,
            "index_digest": index_digest,
            "fts_rows": len(rows),
            "fts_rebuild": summarize(rebuild_samples),
            "scenarios": scenarios,
        }


def child(kind: str, home: Path, args) -> dict:
    home.mkdir(exist_ok=True)
    start = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            kind,
            "--scale",
            str(args.scale[0]),
            "--iterations",
            str(args.iterations),
            "--workers",
            str(args.workers),
            "--spawn-start",
            str(start),
        ],
        cwd=ROOT,
        env=isolated_env(home),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    wall_ms = (time.perf_counter() - start) * 1000
    if proc.returncode:
        raise RuntimeError(f"{kind} exited {proc.returncode}: {proc.stderr[-6000:]}")
    result = json.loads(proc.stdout)
    result["process_wall_ms"] = wall_ms
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, nargs="+", default=[1000, 5000])
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=ROOT / ".scratch/stage6-perf/baseline.json")
    parser.add_argument("--worker", choices=["cold", "scale"], help=argparse.SUPPRESS)
    parser.add_argument("--spawn-start", type=float, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if min(*args.scale, args.iterations, args.repeats, args.workers) < 1:
        parser.error("scale, iterations, repeats and workers must be positive")
    if args.worker:
        result = (
            cold_worker(args.spawn_start or time.perf_counter())
            if args.worker == "cold"
            else scale_worker(args.scale[0], args.iterations, args.workers)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("chromadb", "SQLAlchemy", "numpy", "sqlmodel")
        },
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("lantai/storage/fts.py", "lantai/storage/db.py", "lantai/cli/mcp.py")
        },
        "scope": "fresh-process cold; OS cache uncontrolled; fake external embed; real Chroma",
        "cold": [],
        "cold_existing_db": [],
        "scales": {},
    }

    def save():
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="stage6-", dir=args.out.parent) as temp:
        root = Path(temp)
        try:
            for i in range(args.repeats):
                result["cold"].append(child("cold", root / f"cold-{i}", args))
                result["cold_existing_db"].append(child("cold", root / f"cold-{i}", args))
                save()
                print(f"冷启动采样：{i + 1}/{args.repeats}", flush=True)
            requested = args.scale
            for scale in requested:
                args.scale = [scale]
                result["scales"][str(scale)] = child("scale", root / f"scale-{scale}", args)
                save()
                print(f"索引与检索采样：{scale} 条", flush=True)
        except Exception as exc:
            result["error"] = str(exc)
            save()
            raise
    print(f"报告：{args.out}")
    return 0


if __name__ == "__main__":
    # Report text may contain characters outside the Windows legacy code page.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
