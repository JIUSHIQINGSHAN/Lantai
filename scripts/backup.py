"""备份脚本——SQLite online backup + ChromaDB 拷贝 + manifest 校验信息"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from lantai.core.settings import settings


def backup(dest: str = "", dry_run: bool = False) -> str:
    """备份到指定目录。dry_run=True 时只打印目标路径，不执行。"""
    home = Path(settings.LANTAI_HOME) if settings.LANTAI_HOME else Path(".")
    dest_dir = Path(dest) if dest else home / "backups" / f"backup_{datetime.now():%Y%m%d_%H%M%S}"
    if dry_run:
        print(f"[dry-run] would backup to: {dest_dir}")
        return str(dest_dir)

    dest_dir.mkdir(parents=True, exist_ok=True)

    # SQLite 一致性快照（online backup API，避免热拷脏数据）
    db_path = home / "remembrance.db"
    if db_path.exists():
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(dest_dir / "remembrance.db"))
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

    # ChromaDB 目录
    chroma_path = home / ".chromadb"
    if chroma_path.exists():
        shutil.copytree(chroma_path, dest_dir / ".chromadb", dirs_exist_ok=True)

    # manifest：文件清单 + sha256（restore 校验用）
    manifest = {
        "version": settings.BACKUP_MANIFEST_VERSION,
        "created_at": datetime.now().isoformat(),
        "files": {},
    }
    for f in dest_dir.rglob("*"):
        if f.is_file() and f.name != "manifest.json":
            rel = str(f.relative_to(dest_dir)).replace("\\", "/")
            manifest["files"][rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Backup completed: {dest_dir}")
    return str(dest_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="备份 SQLite db + ChromaDB")
    parser.add_argument("dest", nargs="?", default="", help="目标目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将备份到的目录名")
    args = parser.parse_args()
    backup(args.dest, dry_run=args.dry_run)
