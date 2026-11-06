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

    # SQLite 一致性快照（online backup API，避免热拷脏数据）
    db_path = home / "remembrance.db"
    # X-07（票 05 续24）：缺库判红，不再静默跳过拷贝段后照常打印
    # "Backup completed"。旧行为下 LANTAI_HOME 配错/库未生成时备份目录里只有
    # manifest.json（files 为空），运维照字面收下"成功"，灾备实为空。
    # 宁 miss 不脏写：明确失败并指名路径，且发生在建目标目录之前（不留 litter）。
    if not db_path.exists():
        raise FileNotFoundError(
            f"SQLite 数据库缺失，拒绝产出空备份: {db_path}"
            "（检查 LANTAI_HOME 配置，或确认服务至少初始化过一次）"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

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
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Backup completed: {dest_dir}")
    return str(dest_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="备份 SQLite db + ChromaDB")
    parser.add_argument("dest", nargs="?", default="", help="目标目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将备份到的目录名")
    args = parser.parse_args()
    try:
        backup(args.dest, dry_run=args.dry_run)
    except (FileNotFoundError, OSError) as e:
        print(f"[backup] 备份失败: {e}")
        sys.exit(1)
