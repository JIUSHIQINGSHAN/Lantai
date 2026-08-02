"""恢复脚本——停服 → 覆盖文件 → 重启"""
import argparse
import shutil
import sys
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


def restore(src: str) -> str:
    """从指定目录恢复。"""
    src_dir = Path(src)
    if not src_dir.exists():
        raise FileNotFoundError(f"backup not found: {src}")

    home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")

    # 恢复 SQLite
    db_backup = src_dir / "remembrance.db"
    if db_backup.exists():
        shutil.copy2(db_backup, home / "remembrance.db")

    # 恢复 ChromaDB
    chroma_backup = src_dir / ".chromadb"
    if chroma_backup.exists():
        chroma_dest = home / ".chromadb"
        if chroma_dest.exists():
            shutil.rmtree(chroma_dest)
        shutil.copytree(chroma_backup, chroma_dest)

    print(f"Restore completed from: {src_dir}")
    return str(home)


def main():
    parser = argparse.ArgumentParser(description="恢复备份")
    parser.add_argument("backup_dir", nargs="?", default=None, help="备份目录路径")
    parser.add_argument("--force", action="store_true", help="服务运行中也强制恢复")
    args = parser.parse_args()

    # 停服保护：服务在线时拒绝热恢复（SQLite/Chroma 写入竞争）
    try:
        with urllib.request.urlopen(
                f"http://localhost:{settings.PORT}/health", timeout=1) as r:
            if r.status == 200 and not args.force:
                print("[restore] 服务正在运行，请先停止服务再恢复（或加 --force）")
                return 1
    except Exception:
        pass  # 服务未运行，安全

    backup_dir = Path(args.backup_dir) if args.backup_dir else find_latest_backup()
    restore(str(backup_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
