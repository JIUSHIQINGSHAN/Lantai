"""升级前检查：版本、数据目录、DB 表结构、向量库路径。退出码 0=可升级，1=有问题。"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lantai.core.settings import settings

REQUIRED_TABLES = [
    "rawdocument", "memorycandidate", "memoryitem", "memoryedge",
    "memoryproposal", "memorycheckpoint", "ingestsource",
    "evolutionfeedback", "documentchunk",
]


def main() -> int:
    issues: list[str] = []

    home = Path(settings.LANTAI_HOME) if settings.LANTAI_HOME else Path(".")
    print(f"[upgrade-check] LANTAI_HOME = {home}")
    if not home.exists():
        issues.append("LANTAI_HOME 不存在")

    db_url = settings.DATABASE_URL  # sqlite:///C:/...
    db_path = db_url.replace("sqlite:///", "")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        missing = [t for t in REQUIRED_TABLES if t not in existing]
        if missing:
            issues.append(f"DB 缺表: {', '.join(missing)}")
        print(f"[upgrade-check] DB 表: {len(existing)} 张，缺失 {len(missing)} 张")
    else:
        print("[upgrade-check] DB 文件不存在（首次启动将自动创建）")

    chroma_path = Path(settings.CHROMADB_PATH)
    if not chroma_path.exists():
        print("[upgrade-check] chromadb 目录不存在（首次启动将自动创建）")

    if not os.environ.get("OPENAI_API_KEY"):
        issues.append("OPENAI_API_KEY 未设置（LLM/Embedding 将不可用）")

    if issues:
        print("[upgrade-check] 发现问题:")
        for i in issues:
            print(f"  - {i}")
        return 1
    print("[upgrade-check] 通过，可以升级")
    return 0


if __name__ == "__main__":
    sys.exit(main())
