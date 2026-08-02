"""备份脚本——备份 SQLite db + ChromaDB dir + .env.example"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from remembrance.core.settings import settings


def backup(dest: str = "", dry_run: bool = False) -> str:
    """备份到指定目录。"""
    home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")
    dest_dir = Path(dest) if dest else home / "backups" / f"backup_{datetime.now():%Y%m%d_%H%M%S}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 备份 SQLite
    db_path = home / "remembrance.db"
    if db_path.exists():
        shutil.copy2(db_path, dest_dir / "remembrance.db")

    # 备份 ChromaDB
    chroma_path = home / ".chromadb"
    if chroma_path.exists():
        shutil.copytree(chroma_path, dest_dir / ".chromadb", dirs_exist_ok=True)

    # 备份 .env.example（不含密钥）
    env_example = home / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, dest_dir / ".env.example")

    print(f"Backup completed: {dest_dir}")
    return str(dest_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="备份 SQLite db + ChromaDB")
    parser.add_argument("dest", nargs="?", default="", help="目标目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将备份到的目录名")
    args = parser.parse_args()
    if args.dry_run:
        home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")
        dest_dir = Path(args.dest) if args.dest else home / "backups" / f"backup_{datetime.now():%Y%m%d_%H%M%S}"
        print(f"[dry-run] will backup to: {dest_dir}")
        sys.exit(0)
    backup(args.dest)
