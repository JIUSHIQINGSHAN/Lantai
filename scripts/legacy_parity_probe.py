"""老库升级回放探针（票 06）。由 `scripts/release_check.py` 在临时 LANTAI_HOME 里子进程执行。

对每一份 schema 快照：

1. 按该版真实 DDL 建表（列/nullable/pk/default/索引齐全，缺的列就是缺的）；
2. 把 `user_version` 摆到那一版的 `TARGET_SCHEMA_VERSION`——这是关键：只有从
   「上一版已经跑完当时的迁移链」这个状态出发，才等价于老用户磁盘上的库；
3. 走**真实启动路径** `SQLModel.metadata.create_all()` + `apply_migrations()`
   （create_all 只补新表、不会给老表加列，正是历史上漏迁移没人发现的原因）；
4. 用唯一真源判据 `schema_parity.parity_findings()` 比对当前模型。

只读 DDL，绝不读写业务数据；所有文件落在调用方给的临时 LANTAI_HOME 内。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for entry in (str(REPO), str(REPO / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import schema_parity as parity  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402

import lantai.models.tables  # noqa: E402,F401
import lantai.parameters.trust_models  # noqa: E402,F401
from lantai.core.settings import settings  # noqa: E402
from lantai.storage.migrations import (  # noqa: E402
    TARGET_SCHEMA_VERSION,
    apply_migrations,
    get_migration_failures,
)

MARKER = "LEGACYPAYLOAD"


def run_one(snapshot_path: Path) -> dict:
    snap = parity.load_snapshot(snapshot_path)
    tag = snap["source_tag"]
    db = Path(settings.LANTAI_HOME) / f"legacy_replay_{tag}.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    try:
        parity.build_legacy_db(con, snap)
        eng = create_engine("sqlite:///" + str(db).replace("\\", "/"))
        SQLModel.metadata.create_all(eng)
        eng.dispose()
        apply_migrations(con)
        con.commit()
        findings = parity.parity_findings(con)
        findings["source_tag"] = tag
        findings["start_version"] = snap["source_target_version"]
        findings["end_version"] = con.execute("PRAGMA user_version").fetchone()[0]
        findings["migration_failures"] = get_migration_failures()
        return findings
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    snap_dir = Path(argv[0]) if argv else REPO / "tests" / "fixtures" / "legacy-schema"
    anchors = []
    errors = []
    for path in sorted(snap_dir.glob("*.json")):
        try:
            anchors.append(run_one(path))
        except Exception as exc:  # 探针自己炸了也必须如实回报，不许静默绿
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    payload = {
        "anchors": anchors,
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "home_used": str(settings.LANTAI_HOME),
        "probe_errors": errors,
    }
    print(MARKER + json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
