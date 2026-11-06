"""X-07 针对性锁（票 05 续24，评审清单 commit-review-v0.38-v0.60 §4 X-07）。

现场复现（`.scratch/gate/r25/x07_repro.py`，2026-11-04）：`scripts/backup.py` 在
LANTAI_HOME 已配置但 `remembrance.db` 不存在时，`if db_path.exists()` 静默跳过
拷贝段，照常打印「Backup completed: …」并返回成功——备份目录里只有
manifest.json、`files` 是空字典。运维照字面收下"成功"，灾备是空的。
v0.65 只修了 clause 1（"不读配置"→ 已读 settings.LANTAI_HOME），clause 2
（"成功"地备份空内容）仍然活着——`.scratch/v0.65-next-window-2026-11/map.md`
的"X-07 已修"是过度声明。

判据（宁 miss 不脏写的同款口径：备不出东西就明确失败，不许假成功）：
1. 配置的 home 缺库 ⇒ `backup()` 抛 FileNotFoundError，**且失败发生在建目录
   之前**（不留 litter 目录）；
2. 正常路径真实备份：备份目录含 remembrance.db，manifest 有该文件条目且
   sha256 与备份副本实测一致（不 mock——真实最小库直调）；
3. CLI 退出码：缺库时非零（`sys.exit(1)`），stdout 不再出现 "Backup completed"；
4. dry-run 不校验、不建目录、不抛。

泄漏纪律：全部落 tmp_path；settings.LANTAI_HOME 用 monkeypatch.setattr
（AST 污染锁禁裸写）；子进程显式传 LANTAI_HOME/DATABASE_URL 指向 tmp，
不触宿主真实库。
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from lantai.core.settings import settings

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKUP_PATH = _REPO_ROOT / "scripts" / "backup.py"


def _load_backup():
    spec = importlib.util.spec_from_file_location("backup_under_test", _BACKUP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_backup()


def _make_home_db(path: Path, marker: str = "九曲黄河入海流") -> None:
    """真实最小 SQLite 库（不 mock）：一张表一行，内容可字节级核对。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE probe (content TEXT)")
    conn.execute("INSERT INTO probe VALUES (?)", (marker,))
    conn.commit()
    conn.close()


# ── 1. 缺库必须判红，且不得先留下半成品目录 ─────────────────────────────────


def test_missing_db_fails_closed_before_mkdir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()  # 目录存在、库里没有——正是"配置正常但路径不对"的现场
    dest = tmp_path / "bk"
    monkeypatch.setattr(settings, "LANTAI_HOME", str(home))
    with pytest.raises(FileNotFoundError):
        B.backup(str(dest))
    # 失败必须无副作用：不许建出 dest 目录再空转 manifest
    assert not dest.exists(), f"backup 失败后仍建了目录（litter）：{sorted(dest.iterdir())}"


def test_missing_db_error_names_home_for_ops(tmp_path, monkeypatch):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setattr(settings, "LANTAI_HOME", str(home))
    with pytest.raises(FileNotFoundError) as exc:
        B.backup(str(tmp_path / "bk"))
    assert "remembrance.db" in str(exc.value)


# ── 2. 成功路径：真实备份 + manifest 字节级可核 ─────────────────────────────


def test_success_backs_up_db_with_verified_hash(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _make_home_db(home / "remembrance.db", marker="备份内容必须真实到达")
    dest = tmp_path / "bk"
    monkeypatch.setattr(settings, "LANTAI_HOME", str(home))
    out = B.backup(str(dest))
    assert Path(out) == dest
    copied = dest / "remembrance.db"
    assert copied.is_file()
    # 内容真的在副本里（online backup API 拷的是页，不是空文件）
    conn = sqlite3.connect(str(copied))
    try:
        assert conn.execute("SELECT content FROM probe").fetchall() == [("备份内容必须真实到达",)]
    finally:
        conn.close()
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert "remembrance.db" in manifest["files"]
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    assert manifest["files"]["remembrance.db"] == digest


def test_dry_run_does_not_validate_or_create(tmp_path, monkeypatch):
    home = tmp_path / "nope"
    dest = tmp_path / "bk-dry"
    monkeypatch.setattr(settings, "LANTAI_HOME", str(home))
    out = B.backup(str(dest), dry_run=True)
    assert Path(out) == dest
    assert not dest.exists()


# ── 3. CLI：发布流程实际消费的是退出码 ──────────────────────────────────────


def test_cli_rejects_empty_home_with_nonzero_exit(tmp_path):
    home = tmp_path / "cli-home"
    home.mkdir()
    dest = tmp_path / "cli-bk"
    env = dict(os.environ)
    env["LANTAI_HOME"] = str(home)
    env["DATABASE_URL"] = f"sqlite:///{home / 'remembrance.db'}"
    env.pop("REMEMBRANCE_HOME", None)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-I", str(_BACKUP_PATH), str(dest)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode != 0, (
        f"缺库时 CLI 仍退出 0（假绿）：stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "Backup completed" not in proc.stdout
    assert not dest.exists()
