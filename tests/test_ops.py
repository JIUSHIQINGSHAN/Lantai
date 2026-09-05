"""备份/恢复加固测试（只测判定函数，不真实覆盖数据）"""
import importlib.util
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

_RESTORE_PATH = Path(__file__).parent.parent / "scripts" / "restore.py"


def _load_restore():
    spec = importlib.util.spec_from_file_location("restore", _RESTORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load_restore()


def test_validate_rejects_path_escape(tmp_path):
    home = tmp_path / "home"
    (home / "backups").mkdir(parents=True)
    evil = tmp_path / "outside"
    evil.mkdir()
    with pytest.raises(ValueError):
        R.validate_backup_path(evil, home)


def test_validate_accepts_inside_backups(tmp_path):
    home = tmp_path / "home"
    bk = home / "backups" / "backup_20260803_000000"
    bk.mkdir(parents=True)
    assert R.validate_backup_path(bk, home) is None


def test_verify_manifest_missing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        R.verify_manifest(empty)


def test_verify_manifest_hash_mismatch(tmp_path):
    src = tmp_path / "bk"
    src.mkdir()
    f = src / "remembrance.db"
    f.write_bytes(b"data")
    (src / "manifest.json").write_text(json.dumps(
        {"version": "0.3.3", "files": {"remembrance.db": "0" * 64}}), encoding="utf-8")
    with pytest.raises(ValueError):
        R.verify_manifest(src)


def test_service_online_fail_closed():
    # 200 → 在线 → True
    with patch("urllib.request.urlopen") as m:
        m.return_value.__enter__ = lambda s: s
        m.return_value.__exit__ = lambda *a: None
        m.return_value.status = 200
        assert R.service_online(8767) is True
    # 连接被拒 → 停服 → False
    with patch("urllib.request.urlopen") as m:
        m.side_effect = urllib.error.URLError(ConnectionRefusedError())
        assert R.service_online(8767) is False
    # 超时（socket.timeout）→ 保守视为在线 → True
    with patch("urllib.request.urlopen") as m:
        m.side_effect = urllib.error.URLError(TimeoutError())
        assert R.service_online(8767) is True
