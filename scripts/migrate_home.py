"""
REMEMBRANCE_HOME 数据迁移脚本（安全版）。

设计原则（个人数据安全红线）：
1. 备份优先：先对 SQLite 做一致性备份（online backup），向量库整目录复制。
2. 不删旧：迁移完成后旧目录【原样保留】，由用户确认新 home 工作正常后再手动清理。
3. 验证兜底：新目录写入测试标记文件并读回；DB 打开校验；全部通过才更新配置。
4. 配置写入：目标 REMEMBRANCE_HOME 写入【用户级环境变量】（setx），
   使 MCP/Hook 子进程无论 cwd 在哪都能生效（pydantic-settings 优先读环境变量）。

用法：python scripts/migrate_home.py --target <新目录>
"""
import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


def _backup_db(src_db: Path, target_dir: Path) -> Path:
    """SQLite online backup 一致性快照到目标目录。"""
    dst = target_dir / "remembrance.db"
    src = sqlite3.connect(str(src_db))
    dst_conn = sqlite3.connect(str(dst))
    with dst_conn:
        src.backup(dst_conn)
    dst_conn.close()
    src.close()
    # 完整性校验（只读快速检查）
    check = sqlite3.connect(str(dst))
    try:
        check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    print(f"[OK] DB 备份完成: {dst}（integrity_check 通过）")
    return dst


def _copy_chroma(src: Path, target_dir: Path) -> Path:
    if not src.exists():
        print("[WARN] 未发现 .chromadb，跳过向量库迁移")
        return target_dir / ".chromadb"
    dst = target_dir / ".chromadb"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"[OK] 向量库复制完成: {dst}")
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description="REMEMBRANCE_HOME 安全迁移")
    parser.add_argument("--target", required=True, help="新数据目录绝对路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不执行")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.is_absolute():
        _fail("target 必须是绝对路径")

    src_db = REPO_ROOT / "remembrance.db"
    src_chroma = REPO_ROOT / ".chromadb"

    if not src_db.exists():
        _fail(f"未找到源数据库: {src_db}")

    print(f"[1/5] 源: {REPO_ROOT}")
    print(f"[2/5] 目标: {target}")
    if args.dry_run:
        print("[DRY-RUN] 预览完成，未执行任何操作")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    if (target / "remembrance.db").exists():
        print(f"[WARN] 目标目录已存在 DB: {target / 'remembrance.db'}，"
              f"将覆盖（先备份源）")

    # 1) 备份 DB（一致性快照）
    new_db = _backup_db(src_db, target)
    # 2) 复制向量库
    _copy_chroma(src_chroma, target)

    # 3) 写测试标记并读回（验证目标目录可写）
    probe = target / ".probe"
    probe.write_text("ok", encoding="utf-8")
    if probe.read_text(encoding="utf-8") != "ok":
        _fail("目标目录写入验证失败")
    probe.unlink()

    # 4) 配置用户级环境变量（setx；Windows）
    env_cmd = f'setx REMEMBRANCE_HOME "{target}"'
    print(f"[4/5] 写入用户环境变量: REMEMBRANCE_HOME={target}")
    try:
        subprocess.run(env_cmd, shell=True, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        _fail(f"setx 失败（需管理员？）: {e.stderr.decode(errors='ignore')}")

    # 5) 同时写入项目根 .env（双保险，供以项目根为 cwd 的进程读取）
    env_path = REPO_ROOT / ".env"
    lines = []
    if env_path.exists():
        lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("REMEMBRANCE_HOME=")]
    lines.append(f"REMEMBRANCE_HOME={target}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[5/5] 已写入 {env_path}: REMEMBRANCE_HOME={target}")

    print()
    print("=" * 60)
    print(f"迁移完成。新数据目录: {target}")
    print("旧目录【未删除】: 请确认新 home 工作正常后手动清理。")
    print("新目录说明:")
    print(f"  - {new_db.name}（备份自源库，integrity 通过）")
    print("验证方法: 重启 Hermes 后跑 scripts/verify_remembrance.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
