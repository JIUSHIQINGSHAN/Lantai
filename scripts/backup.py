"""备份脚本——备份 SQLite db + ChromaDB dir + .env.example"""
import shutil
import sys
from pathlib import Path
from remembrance.core.settings import settings


def backup(dest: str = "") -> str:
    """备份到指定目录。"""
    home = Path(settings.REMEMBRANCE_HOME) if settings.REMEMBRANCE_HOME else Path(".")
    dest_dir = Path(dest) if dest else home / "backups" / f"backup_{settings.__class__.__name__}"
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
    dest = sys.argv[1] if len(sys.argv) > 1 else ""
    backup(dest)
