"""票 06：legacy schema parity + 门禁老库盲区回归锁。

约定与 `test_migration_v29` 一致：真实临时 SQLite + 真实迁移链，**不 mock**。
所有库文件都落在 pytest `tmp_path` 里，不碰 LANTAI_HOME、不碰宿主库。

这些测试要钉死的事实（票 05 续9 实测）：

- 老库缺列在真实运行路径上是**崩溃级**故障（`no column named shared_scope`），
  而门禁当时只比对 create_all 建出的全新库，结构上不可能发现它；
- 判据与基线分离：同一套 parity 判据，喂全新库和喂「上一版真实 DDL + 真实迁移链」，
  结论必须一致地严格。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

REPO = Path(__file__).resolve().parents[1]

import importlib.util


def _load_script(module_name: str):
    """按文件路径加载 scripts/ 下的模块，**不动 sys.path**。

    曾经这里是 `sys.path.insert(0, REPO/"scripts")`：那等于把 scripts 目录变成
    整个测试会话的隐式导入源（`import backup` / `import restore` / `import shell_hook`
    都可能被劫持），而且 conftest 的复位哨兵只盯 `lantai.*`/`tests.*`，看不见它。
    本票的主题就是不留这种全局污染，所以按路径加载。
    """
    path = REPO / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"parity_uut_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


parity = _load_script("schema_parity")

from lantai.storage.migrations import (  # noqa: E402
    TARGET_SCHEMA_VERSION,
    apply_migrations,
    get_migration_failures,
)


def _load_release_check():
    return _load_script("release_check")


SNAPSHOT_DIR = REPO / "tests" / "fixtures" / "legacy-schema"
SNAPSHOTS = sorted(SNAPSHOT_DIR.glob("*.json"))


def _replay(snapshot: dict, db_path: Path):
    """按快照 DDL 重建上一版真实表形状，然后走真实启动路径升级到当前模型。"""
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    parity.build_legacy_db(con, snapshot)
    engine = create_engine("sqlite:///" + str(db_path).replace("\\", "/"))
    SQLModel.metadata.create_all(engine)  # 只补新表，不给老表加列——历史漏检的根源
    engine.dispose()
    apply_migrations(con)
    con.commit()
    return con


# --------------------------------------------------------------------------
# 快照本身：可提交、无行数据、来源可查
# --------------------------------------------------------------------------


def test_snapshots_present_and_ddl_only():
    assert SNAPSHOTS, "老库锚点快照一个都没有，门禁的老库判据形同虚设"
    for path in SNAPSHOTS:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["contains_row_data"] is False
        assert set(data) >= {"schema_version", "source_tag", "source_target_version", "tables"}
        # 结构上杜绝行数据：表里只有 columns/indexes，列里只有 5 个 DDL 元数据键
        for name, spec in data["tables"].items():
            assert set(spec) == {"columns", "indexes"}, name
            for col in spec["columns"]:
                assert set(col) == {"name", "type", "nullable", "default", "pk"}, (name, col)


@pytest.mark.parametrize("snapshot_path", SNAPSHOTS, ids=lambda p: p.stem)
def test_snapshot_tag_is_a_real_ancestor_of_head(snapshot_path):
    tag = json.loads(snapshot_path.read_text(encoding="utf-8"))["source_tag"]
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"refs/tags/{tag}"],
            cwd=str(REPO),
        ).returncode
        == 0
    ), f"快照声称来自 {tag}，仓库里却没这个标签"
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", tag, "HEAD"], cwd=str(REPO)
        ).returncode
        == 0
    ), f"{tag} 不是 HEAD 的祖先，快照来历可疑"


# --------------------------------------------------------------------------
# 契约 1：老库经真实迁移链升级后，必须与当前模型完全对齐
# --------------------------------------------------------------------------


@pytest.mark.parametrize("snapshot_path", SNAPSHOTS, ids=lambda p: p.stem)
def test_legacy_replay_lands_on_model_parity(snapshot_path, tmp_path):
    snap = parity.load_snapshot(snapshot_path)
    con = _replay(snap, tmp_path / f"legacy_{snap['source_tag']}.db")
    try:
        findings = parity.parity_findings(con)
        end_version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()

    assert get_migration_failures() == [], f"[{snap['source_tag']}] 迁移链自身报错"
    assert end_version == TARGET_SCHEMA_VERSION, (
        f"[{snap['source_tag']}] 从 v{snap['source_target_version']} 起跑，"
        f"只到 v{end_version}，目标 v{TARGET_SCHEMA_VERSION}"
    )
    assert findings["missing_tables"] == []
    assert findings["absent_columns"] == [], (
        f"[{snap['source_tag']}] 老库跑完整条迁移链仍缺列，真实写入会直接崩："
        f"{findings['absent_columns']}"
    )
    assert findings["pk_drift"] == []
    assert findings["index_drift"] == [], (
        f"[{snap['source_tag']}] 列补上了但索引没跟上：{findings['index_drift']}"
    )


@pytest.mark.parametrize("snapshot_path", SNAPSHOTS, ids=lambda p: p.stem)
def test_replayed_legacy_db_actually_accepts_model_writes(snapshot_path, tmp_path):
    """不缺列不是纸面结论：老库升级后必须真能按模型写入并读回。"""
    from lantai.models.tables import MemoryCandidate

    snap = parity.load_snapshot(snapshot_path)
    db_path = tmp_path / f"write_{snap['source_tag']}.db"
    con = _replay(snap, db_path)
    con.close()

    engine = create_engine("sqlite:///" + str(db_path).replace("\\", "/"))
    from sqlmodel import Session, select

    with Session(engine) as s:
        s.add(MemoryCandidate(id="c_leg", document_id="d", shared_scope="swarm_shared"))
        s.commit()
        got = s.exec(select(MemoryCandidate).where(MemoryCandidate.id == "c_leg")).first()
        assert got is not None and got.shared_scope == "swarm_shared"
    engine.dispose()


# --------------------------------------------------------------------------
# 契约 2：NULL 方向差异只能来自书面清单，且清单不许变成死账
# --------------------------------------------------------------------------


@pytest.mark.parametrize("snapshot_path", SNAPSHOTS, ids=lambda p: p.stem)
def test_null_drift_stays_inside_written_waivers(snapshot_path, tmp_path):
    snap = parity.load_snapshot(snapshot_path)
    con = _replay(snap, tmp_path / f"waiver_{snap['source_tag']}.db")
    findings = parity.parity_findings(con)
    con.close()
    unwaived = parity.ungated_null_drift(findings)
    assert unwaived == [], (
        "出现了清单之外的 NULL 约束漂移。要么补迁移收紧，要么在 "
        "scripts/schema_parity.py 里写清物理成因再入清单：\n" + "\n".join(unwaived)
    )


def test_waiver_ledger_has_no_dead_entries(tmp_path):
    """清单每一项都必须能被某个锚点真实复现——防止豁免清单烂成无人认领的死账。"""
    measured: set[str] = set()
    for path in SNAPSHOTS:
        snap = parity.load_snapshot(path)
        con = _replay(snap, tmp_path / f"rot_{snap['source_tag']}.db")
        measured.update(parity.parity_findings(con)["null_drift_keys"])
        con.close()
    assert measured == set(parity.NULL_DRIFT_WAIVERS), (
        f"实测漂移={sorted(measured)} 清单={sorted(parity.NULL_DRIFT_WAIVERS)}；"
        "清单外有新漂移=没收口，漂移消失却留着条目=死账"
    )


def test_every_waiver_is_backed_by_a_non_null_model_default():
    """纸面豁免的运行时依据：ORM 写入路径不会落 NULL（模型侧必有非 None 默认值）。"""
    from sqlmodel import Field  # noqa: F401

    for key, reason in parity.NULL_DRIFT_WAIVERS.items():
        assert isinstance(reason, str) and len(reason) > 12, key
        table, column = key.split(".")
        sa_table = SQLModel.metadata.tables.get(table)
        assert sa_table is not None, key
        col = sa_table.columns[column]
        assert col.nullable is False, f"{key} 模型侧本就不是 NOT NULL，豁免无意义"
        assert col.default is not None or col.server_default is not None, (
            f"{key} 老库允许 NULL、模型又不给默认值，ORM 真能写出 NULL"
        )


# --------------------------------------------------------------------------
# 契约 3：门禁判据必须拦得住——阴性对照（变异测试）
# --------------------------------------------------------------------------


def _payload_from(findings: dict, tag: str, end_version: int) -> dict:
    anchor = dict(findings)
    anchor.update(
        {
            "source_tag": tag,
            "end_version": end_version,
            "migration_failures": list(get_migration_failures()),
        }
    )
    return {
        "anchors": [anchor],
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "home_isolated": True,
        "probe_errors": [],
    }


def test_gate_blocks_when_a_column_is_missing_on_legacy_db(tmp_path):
    """把「v35 那跳没补成」的历史真实状态重放给门禁：必须判红。"""
    rc = _load_release_check()
    snap = parity.load_snapshot(SNAPSHOT_DIR / "v0.25.0.json")
    con = _replay(snap, tmp_path / "drift.db")
    con.execute("DROP INDEX IF EXISTS ix_memorycandidate_shared_scope")
    con.execute("ALTER TABLE memorycandidate DROP COLUMN shared_scope")
    con.commit()
    findings = parity.parity_findings(con)
    con.close()

    assert "memorycandidate.shared_scope" in findings["absent_columns"]
    issues = rc.legacy_parity_issues_from_payload(
        _payload_from(findings, snap["source_tag"], TARGET_SCHEMA_VERSION)
    )
    assert any("仍缺列" in i for i in issues), f"缺列没被判红：{issues}"


def test_clean_repo_payload_passes_the_judge(tmp_path):
    """同一套判据在修好的代码上必须全绿——红绿对照的另一半。"""
    rc = _load_release_check()
    snap = parity.load_snapshot(SNAPSHOT_DIR / "v0.25.0.json")
    con = _replay(snap, tmp_path / "clean.db")
    findings = parity.parity_findings(con)
    end_version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    payload = _payload_from(findings, snap["source_tag"], end_version)
    # 单独一个锚点时，另一个锚点独有的 NULL 漂移不会复现——按单锚点判会误报清单死账，
    # 所以这里只断言「缺列/索引/版本」这几类硬伤为空。
    hard = [i for i in rc.legacy_parity_issues_from_payload(payload) if "死账" not in i]
    assert hard == [], f"干净老库被判红：{hard}"


def test_gate_blocks_unwaived_null_drift_and_vacuous_anchors():
    rc = _load_release_check()
    forged = {
        "missing_tables": [],
        "absent_columns": [],
        "pk_drift": [],
        "index_drift": [],
        "null_drift_keys": ["memoryitem.brand_new_col"],
        "null_drift": [
            "memoryitem.brand_new_col NULL 约束漂移 model_nullable=False db_nullable=True"
        ],
    }
    issues = rc.legacy_parity_issues_from_payload(
        _payload_from(forged, "vX", TARGET_SCHEMA_VERSION)
    )
    assert any(
        "清单内的 NULL" in i or "清单之外" in i or "不在书面豁免清单内" in i for i in issues
    ), issues

    assert rc.legacy_parity_issues_from_payload(
        {"anchors": [], "target_schema_version": TARGET_SCHEMA_VERSION, "home_isolated": True}
    ), "零锚点必须判红，否则该项可以空转假绿"

    assert rc.legacy_parity_issues_from_payload(
        {
            "anchors": [
                {
                    "source_tag": "vX",
                    "end_version": TARGET_SCHEMA_VERSION,
                    "null_drift_keys": list(parity.NULL_DRIFT_WAIVERS),
                    "null_drift": [],
                }
            ],
            "target_schema_version": TARGET_SCHEMA_VERSION,
            "home_isolated": False,
        }
    ), "探针没跑在临时 home 里必须判红"

    assert rc.legacy_parity_issues_from_payload(
        {
            "anchors": [
                {
                    "source_tag": "vX",
                    "end_version": TARGET_SCHEMA_VERSION - 1,
                    "null_drift_keys": list(parity.NULL_DRIFT_WAIVERS),
                    "null_drift": [],
                }
            ],
            "target_schema_version": TARGET_SCHEMA_VERSION,
            "home_isolated": True,
        }
    ), "迁移链没跑到目标版本必须判红"


def test_probe_script_errors_are_not_swallowed(tmp_path):
    """探针自己炸了要如实回报，不能当成通过。"""
    rc = _load_release_check()
    proc = subprocess.run(
        [
            sys.executable,
            "-u",
            str(REPO / "scripts" / "legacy_parity_probe.py"),
            str(tmp_path / "no_such_dir"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("LEGACYPAYLOAD"))
    payload = json.loads(line[len("LEGACYPAYLOAD") :])
    assert payload["anchors"] == []
    assert not payload.get("home_isolated")  # home_isolated 由门禁层判定，探针本身不声明
    assert rc.legacy_parity_issues_from_payload(payload), "空锚点目录不该放行"


def test_fresh_baseline_is_structurally_blind_to_legacy_absence(tmp_path):
    """钉住盲区本身：全新库口径干净，同一时刻老库形状却缺列。

    这就是 memorycandidate.shared_scope 躲过发布门禁的机制，
    今后谁想把老库回放项拆掉，这条会先红。
    """
    rc = _load_release_check()
    fresh_payload = rc.schema_drift_probe(REPO)
    assert rc.schema_drift_issues_from_payload(fresh_payload) == [], (
        "前置条件变了：全新库口径本身已经能报漂移，本用例的对照意义要重写"
    )

    snap = parity.load_snapshot(SNAPSHOT_DIR / "v0.25.0.json")
    con = _replay(snap, tmp_path / "blind.db")
    con.execute("DROP INDEX IF EXISTS ix_memorycandidate_shared_scope")
    con.execute("ALTER TABLE memorycandidate DROP COLUMN shared_scope")
    con.commit()
    assert parity.parity_findings(con)["absent_columns"], "老库形状没有缺列，对照失效"
    con.close()
