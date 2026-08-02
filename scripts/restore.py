"""恢复脚本——停服确认(fail-closed) → 路径限定 → manifest 校验 → 原子换入"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from remembrance.core.settings import settings


def find_latest_backup() -> Path:
    """查找最新的备份目录。"""
    home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")
    backups_dir = home / "backups"
    if not backups_dir.exists():
        raise FileNotFoundError("no backups directory")
    dirs = sorted(backups_dir.iterdir(), key=lambda p: p.name, reverse=True)
    if not dirs:
        raise FileNotFoundError("no backups found")
    return dirs[0]


def validate_backup_path(src: Path, home: Path) -> None:
    """备份目录必须位于 home/backups 下，且全路径无 symlink。"""
    backups_root = (home / "backups").resolve()
    try:
        resolved = src.resolve(strict=True)
    except FileNotFoundError:
        raise FileNotFoundError(f"backup not found: {src}")
    if backups_root not in resolved.parents:
        raise ValueError(f"backup must be under {backups_root}: {resolved}")
    for part in list(resolved.parents) + [resolved]:
        if part.is_symlink():
            raise ValueError(f"symlink not allowed in backup path: {part}")


def verify_manifest(src_dir: Path) -> None:
    """校验 manifest.json 存在且所有文件 hash 一致。"""
    manifest_path = src_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("manifest.json missing (backup created by v0.3.3+)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel, digest in manifest["files"].items():
        p = src_dir / rel
        if not p.exists():
            raise ValueError(f"file missing: {rel}")
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError(f"hash mismatch: {rel}")


def service_online(port: int) -> bool:
    """fail-closed 停服探测：只有明确连接被拒才视为停服；超时/异常视为在线。"""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=1) as r:
            return r.status == 200
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return False
        return True
    except Exception:
        return True


def restore(src: str) -> str:
    """从指定目录恢复（路径限定 + manifest 校验 + 原子换入）。"""
    src_dir = Path(src)
    home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")

    validate_backup_path(src_dir, home)
    verify_manifest(src_dir)

    # SQLite：临时文件 + os.replace 原子换入
    db_backup = src_dir / "remembrance.db"
    if db_backup.exists():
        tmp = home / ".restore-tmp.db"
        shutil.copy2(db_backup, tmp)
        os.replace(tmp, home / "remembrance.db")

    # ChromaDB：整体目录原子换入（旧目录先移走，失败可回滚）
    chroma_backup = src_dir / ".chromadb"
    if chroma_backup.exists():
        old = home / ".chromadb"
        old_tmp = home / ".chromadb.old-tmp"
        new_tmp = home / ".chromadb.new-tmp"
        if old_tmp.exists():
            shutil.rmtree(old_tmp)
        if new_tmp.exists():
            shutil.rmtree(new_tmp)
        if old.exists():
            os.replace(old, old_tmp)
        shutil.copytree(chroma_backup, new_tmp)
        os.replace(new_tmp, old)
        if old_tmp.exists():
            shutil.rmtree(old_tmp)

    print(f"Restore completed from: {src_dir}")
    return str(home)


def main():
    parser = argparse.ArgumentParser(description="恢复备份")
    parser.add_argument("backup_dir", nargs="?", default=None, help="备份目录路径")
    parser.add_argument("--force", action="store_true", help="服务运行中也强制恢复")
    args = parser.parse_args()

    # 停服保护：fail-closed——探测失败/超时一律视为服务可能在线，拒绝热恢复
    if service_online(settings.PORT) and not args.force:
        print("[restore] 服务可能正在运行（含探测失败情况）。请先停止服务，或加 --force")
        return 1

    backup_dir = Path(args.backup_dir) if args.backup_dir else find_latest_backup()
    try:
        restore(str(backup_dir))
    except (ValueError, FileNotFoundError) as e:
        print(f"[restore] 拒绝恢复: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
