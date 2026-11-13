"""Schema parity 判定的唯一真源（票 06）。

门禁以前只比对「create_all 建出来的全新库」与模型，于是「老库缺列」这一类
永远看不见——`memorycandidate.shared_scope` 就是这么躲过发布门禁的（票 05 续9）。
本模块把**判据**与**基线库**拆开：判据只有一套，基线可以是全新库，也可以是
上一版/远古版真实 schema 经真实迁移链升级后的库。

判据一律是结构性比对，不引入任何可调数字。

安全边界：本模块只做 DDL 层比对，绝不读取任何业务行数据；快照 fixture 也只存
表名/列名/类型/nullable/default/pk/索引，不含用户数据。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlmodel import SQLModel

SNAPSHOT_SCHEMA_VERSION = 1

# 已知并书面豁免的 NULL 方向差异：模型 NOT NULL、老库经 ALTER 后为 nullable。
# 物理成因：SQLite `ALTER TABLE ... ADD COLUMN x TEXT NOT NULL` 对已有行的表不可行
# （无填充值即报错），带 DEFAULT 也只会建成 nullable。真要收紧必须走 12 步表重建
# （create new -> copy -> drop -> rename），风险远大于收益，本票明确不做。
#
# 该清单是**上界**而不是容忍度：门禁只放行「清单内的列」，新出现的 NULL 漂移一律红；
# tests/test_legacy_schema_parity.py 另有一条反烂账断言，要求清单里的每一项都能被
# 某个已提交锚点真实复现，删不掉漂移就不许留空条目，修好漂移就必须删条目。
#
# 运行时校验（不是纸面豁免）：这些列在模型侧都有非 None 默认值，ORM 写入路径不会
# 落 NULL；裸 SQL 写 NULL 才能触发，属已知残余风险（见票 06）。
NULL_DRIFT_WAIVERS: dict[str, str] = {
    "memoryitem.shared_scope": (
        "v35 ALTER ADD COLUMN 建为 nullable；模型 default='private'，ORM 不落 NULL（票 06）"
    ),
    "skillcrystal.shared_scope": (
        "v35 ALTER ADD COLUMN 建为 nullable；模型 default='private'，ORM 不落 NULL（票 06）"
    ),
    "skillcrystal.success_count": (
        "v35 ALTER ADD COLUMN INTEGER DEFAULT 0，建为 nullable；模型 default=0（票 06）"
    ),
    "memorycandidate.shared_scope": (
        "v36 ALTER ADD COLUMN 建为 nullable；模型 default='private'，ORM 不落 NULL（票 06）"
    ),
    "retrieval_event.fts_terms_truncated": (
        "v41 ALTER ADD COLUMN BOOLEAN DEFAULT 0，建为 nullable；模型 default=False（票 49）"
    ),
}

_REQUIRED_COL_KEYS = {"name", "type", "nullable", "default", "pk"}
_REQUIRED_TABLE_KEYS = {"columns", "indexes"}


def load_snapshot(path: Path) -> dict:
    """读取 schema 快照并做结构校验：只允许 DDL 元数据，杜绝行数据混入仓库。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"快照 schema_version 非预期：{path}")
    if data.get("contains_row_data"):
        raise ValueError(f"快照声称含行数据，禁止入库：{path}")
    if not isinstance(data.get("source_tag"), str) or not data.get("source_tag"):
        raise ValueError(f"快照缺 source_tag：{path}")
    if not isinstance(data.get("source_target_version"), int):
        raise ValueError(f"快照缺 source_target_version：{path}")
    tables = data.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ValueError(f"快照 tables 为空：{path}")
    for name, spec in tables.items():
        if set(spec) != _REQUIRED_TABLE_KEYS:
            raise ValueError(f"快照表 {name} 字段越界（只允许 columns/indexes）：{set(spec)}")
        for col in spec["columns"]:
            if set(col) != _REQUIRED_COL_KEYS:
                raise ValueError(f"快照列定义字段越界（{name}）：{set(col)}")
            if not isinstance(col["name"], str) or len(col["name"]) > 128:
                raise ValueError(f"快照列名异常（{name}）")
    data["_source_path"] = str(path)
    return data


def build_legacy_db(conn: sqlite3.Connection, snapshot: dict) -> None:
    """按快照 DDL 重建上一版真实表形状，并把 user_version 摆到该版的目标值。

    刻意**不**调用 create_all：这里要的是「当时磁盘上真实存在的那张表」，
    列由那一版的模型建出来，缺的列就是缺的。
    """
    for name, spec in snapshot["tables"].items():
        cols = spec["columns"]
        pks = [c["name"] for c in cols if c["pk"]]
        parts = []
        for c in cols:
            frag = f'"{c["name"]}" {c["type"] or "TEXT"}'
            if not c["nullable"]:
                frag += " NOT NULL"
            if c["default"] is not None:
                frag += f" DEFAULT {c['default']}"
            if c["pk"] and len(pks) == 1:
                frag += " PRIMARY KEY"
            parts.append(frag)
        if len(pks) > 1:
            parts.append("PRIMARY KEY (" + ", ".join(chr(34) + pk + chr(34) for pk in pks) + ")")
        conn.execute(f'CREATE TABLE "{name}" ({", ".join(parts)})')
        for ix in spec["indexes"]:
            if not ix["cols"]:
                continue
            uniq = "UNIQUE " if ix["unique"] else ""
            cols_sql = ", ".join(f'"{c}"' for c in ix["cols"])
            conn.execute(
                f'CREATE {uniq}INDEX IF NOT EXISTS "{ix["name"]}" ON "{name}" ({cols_sql})'
            )
    conn.execute(f"PRAGMA user_version = {snapshot['source_target_version']}")
    conn.commit()


def _db_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    }


def parity_findings(conn: sqlite3.Connection) -> dict:
    """比对「当前模型 metadata」与「conn 指向的实库」，返回结构性漂移。

    只报事实，不做豁免判断——豁免由 `ungated_null_drift()` / 门禁层决定。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.parameters.trust_models  # noqa: F401

    meta = SQLModel.metadata.tables
    present = _db_tables(conn)
    out: dict = {
        "missing_tables": sorted(set(meta) - present),
        "absent_columns": [],
        "null_drift": [],
        "null_drift_keys": [],
        "pk_drift": [],
        "index_drift": [],
        "extra_columns": [],
        "tables_checked": len(meta),
        "tables_built": len(present),
    }
    for name, table in sorted(meta.items()):
        if name not in present:
            continue
        rows = list(conn.execute(f'PRAGMA table_info("{name}")'))
        cols = {r[1]: {"nullable": not bool(r[3]), "pk": bool(r[5])} for r in rows}
        for col in table.columns:
            if col.name not in cols:
                out["absent_columns"].append(f"{name}.{col.name}")
                continue
            if bool(col.nullable) != cols[col.name]["nullable"]:
                out["null_drift_keys"].append(f"{name}.{col.name}")
                out["null_drift"].append(
                    f"{name}.{col.name} NULL 约束漂移 "
                    f"model_nullable={col.nullable} db_nullable={cols[col.name]['nullable']}"
                )
            if bool(col.primary_key) != cols[col.name]["pk"]:
                out["pk_drift"].append(f"{name}.{col.name}")
        model_indexed = {c.name for c in table.columns if c.index}
        if model_indexed:
            have = set()
            for r in list(conn.execute(f'PRAGMA index_list("{name}")')):
                if not r[1] or str(r[1]).startswith("sqlite_"):
                    continue
                for c in list(conn.execute(f'PRAGMA index_info("{r[1]}")')):
                    if c[2] is not None:
                        have.add(c[2])
            for col in sorted(model_indexed - have):
                out["index_drift"].append(f"{name}.{col} 模型声明 index=True 但实库无可用索引")
        out["extra_columns"].extend(
            f"{name}.{c}" for c in sorted(set(cols) - {x.name for x in table.columns})
        )
    return out


def ungated_null_drift(findings: dict) -> list[str]:
    """清单外的 NULL 漂移——这部分才是门禁要拦的。"""
    return [
        item
        for key, item in zip(findings.get("null_drift_keys", []), findings.get("null_drift", []))
        if key not in NULL_DRIFT_WAIVERS
    ]


def null_drift_waivers_without_evidence(measured_keys: set[str]) -> list[str]:
    """清单里已经复现不出来的条目（防止豁免清单变成无人认领的死账）。"""
    return sorted(set(NULL_DRIFT_WAIVERS) - measured_keys)
