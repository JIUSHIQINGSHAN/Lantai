"""
Remembrance 接入自检脚本（Hermes 装完跑一遍，输出 PASS/FAIL 清单）。

覆盖：
1. 数据层：DB 可读、.chromadb 存在、REMEMBRANCE_HOME 生效
2. 代码层：MCP server 可 import、Shell Hook 可 import
3. 协议层：Shell Hook 输入输出契约（不依赖网络，用固定 stdin 验证解析）
4. 服务层：REST /health（若服务在跑）

用法：python scripts/verify_remembrance.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print("Remembrance 接入自检")
    print("=" * 60)

    # 1) 数据层
    home = os.environ.get("REMEMBRANCE_HOME", "")
    db = Path(home) / "remembrance.db" if home else REPO_ROOT / "remembrance.db"
    check("REMEMBRANCE_HOME 已设置", bool(home),
          f"{home}" if home else "未设置（将用仓库默认目录）")
    check("SQLite 数据库存在", db.exists(), str(db))
    if db.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(str(db))
            row = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.close()
            check("数据库可读", row and row[0] > 0, f"表对象数={row[0]}")
        except Exception as e:
            check("数据库可读", False, str(e))
    check("向量库存在", (Path(home) / ".chromadb").exists() if home
          else (REPO_ROOT / ".chromadb").exists())

    # 2) 代码层
    try:
        import scripts.mcp_server  # noqa: F401
        check("MCP server 可导入", True)
    except Exception as e:
        check("MCP server 可导入", False, str(e)[:120])
    try:
        import scripts.shell_hook  # noqa: F401
        check("Shell Hook 可导入", True)
    except Exception as e:
        check("Shell Hook 可导入", False, str(e)[:120])

    # 3) 协议层：Shell Hook 输入输出契约（不依赖网络）
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "shell_hook.py")],
            input=json.dumps({"query": "自检查询"}), capture_output=True,
            text=True, timeout=15, env=os.environ.copy())
        out = proc.stdout.strip()
        parsed = json.loads(out) if out else {}
        check("Shell Hook 契约（stdin→stdout JSON）",
              isinstance(parsed, dict), out[:80] or "(空输出)")
    except subprocess.TimeoutExpired:
        check("Shell Hook 契约", False, "超时")
    except Exception as e:
        check("Shell Hook 契约", False, str(e)[:120])

    # 4) 服务层（若在跑）
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:8767/health", timeout=2)
        check("REST /health", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception:
        check("REST /health", False, "服务未启动（跳过即可，非必需）")

    print("=" * 60)
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    print(f"结果: {len(results) - n_fail}/{len(results)} 通过, {n_fail} 失败")
    if n_fail:
        print("存在 FAIL 项，请对照交接文档排查。")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
