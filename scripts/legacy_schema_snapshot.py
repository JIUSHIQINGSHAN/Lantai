"""生成「上一版真实 schema 快照」（票 06：补齐门禁的老库盲区）。

快照只含 DDL 元数据（表名/列名/类型/nullable/default/pk/索引），**不含任何行数据**，
因此可以安全入库。它由 `git worktree` 把目标标签的代码取出来，用该版模型 create_all
建到临时库，再 `PRAGMA` 读回真实形状——机械生成，不手写、不维护谎言。

用法：
    python scripts/legacy_schema_snapshot.py --tag v0.25.0
    python scripts/legacy_schema_snapshot.py --tag v0.65.0

输出落在 `tests/fixtures/legacy-schema/<tag>.json`。发布滚动时把上一个发布标签补一份
即可（票 06 验收契约 2）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "tests" / "fixtures" / "legacy-schema"
SNAPSHOT_SCHEMA_VERSION = 1
_MARKER = "LEGACYSNAP"


def _dump_here() -> None:
    """在 worktree 里执行：用该版模型建库并打印 DDL 快照。"""
    import sqlite3

    sys.path.insert(0, os.getcwd())
    from sqlmodel import SQLModel, create_engine

    import lantai.models.tables  # noqa: F401

    with contextlib.suppress(Exception):
        import lantai.parameters.trust_models  # noqa: F401

    home = os.environ["LANTAI_HOME"]
    db = os.path.join(home, "legacy_src.db")
    eng = create_engine("sqlite:///" + db.replace("\\", "/"))
    SQLModel.metadata.create_all(eng)
    eng.dispose()

    con = sqlite3.connect(db)
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    tables: dict = {}
    names = sorted(
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    for name in names:
        cols = [
            {
                "name": r[1],
                "type": r[2],
                "nullable": not bool(r[3]),
                "default": r[4],
                "pk": bool(r[5]),
            }
            for r in con.execute(f'PRAGMA table_info("{name}")')
        ]
        idx = []
        for r in list(con.execute(f'PRAGMA index_list("{name}")')):
            iname = r[1]
            if not iname or str(iname).startswith("sqlite_"):
                continue
            icols = [
                c[2] for c in list(con.execute(f'PRAGMA index_info("{iname}")')) if c[2] is not None
            ]
            idx.append({"name": iname, "unique": bool(r[2]), "cols": icols})
        tables[name] = {"columns": cols, "indexes": idx}
    con.close()
    print(_MARKER + json.dumps({"source_user_version": ver, "tables": tables}, ensure_ascii=False))


def _target_version_of(worktree: Path) -> int:
    mig = worktree / "lantai" / "storage" / "migrations.py"
    m = re.search(r"^TARGET_SCHEMA_VERSION\s*=\s*(\d+)", mig.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"无法从 {mig} 解析 TARGET_SCHEMA_VERSION")
    return int(m.group(1))


def build(tag: str) -> Path:
    parent = Path(tempfile.mkdtemp(prefix="lantai_legacy_wt_"))
    wt = parent / "wt"
    git_err = subprocess.run(
        ["git", "worktree", "add", "--detach", str(wt), tag],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    if git_err.returncode != 0:
        shutil.rmtree(parent, ignore_errors=True)
        raise SystemExit(f"worktree 创建失败（tag={tag}）：{git_err.stderr[-400:]}")
    try:
        home = tempfile.mkdtemp(prefix="lantai_legacy_home_")
        env = dict(os.environ)
        env.update(
            {
                "LANTAI_HOME": home,
                "REMEMBRANCE_HOME": home,
                "PYTHONIOENCODING": "utf-8",
                "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
            }
        )
        for k in (
            "API_KEY",
            "OPENAI_API_KEY",
            "RERANKER_API_KEY",
            "DATABASE_URL",
            "CHROMADB_PATH",
            "LANTAI_MCP_PROFILE",
            "MCP_TOOL_PROFILE",
            "LANTAI_GATE_HOST_HOME",
            "VIRTUAL_ENV",
        ):
            env.pop(k, None)
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--_dump"],
            cwd=str(wt),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        shutil.rmtree(home, ignore_errors=True)
        line = next((ln for ln in (proc.stdout or "").splitlines() if ln.startswith(_MARKER)), "")
        if not line:
            raise SystemExit(
                f"快照生成失败（tag={tag}, exit={proc.returncode}）：{(proc.stderr or '')[-600:]}"
            )
        payload = json.loads(line[len(_MARKER) :])
        payload["schema_version"] = SNAPSHOT_SCHEMA_VERSION
        payload["source_tag"] = tag
        payload["source_target_version"] = _target_version_of(wt)
        payload["contains_row_data"] = False
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{tag}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )
        n_tables = len(payload["tables"])
        n_cols = sum(len(t["columns"]) for t in payload["tables"].values())
        print(
            f"[snapshot] {out.relative_to(REPO)} tables={n_tables} cols={n_cols} "
            f"target_version={payload['source_target_version']} bytes={out.stat().st_size}"
        )
        return out
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=str(REPO))
        shutil.rmtree(parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--_dump" in argv:
        _dump_here()
        return 0
    ap = argparse.ArgumentParser(description="生成上一版 schema 快照（只含 DDL，不含行数据）")
    ap.add_argument("--tag", required=True, help="发布标签，例如 v0.25.0")
    args = ap.parse_args(argv)
    build(args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
