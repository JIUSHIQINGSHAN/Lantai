"""恢复脚本——停服 → 覆盖文件 → 重启"""
import shutil
import sys
from pathlib import Path
from remembrance.core.settings import settings


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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python restore.py <backup_dir>")
        sys.exit(1)
    restore(sys.argv[1])
