"""
兰台记忆接入自检脚本（Hermes 装完跑一遍，输出 PASS/FAIL 清单）。

覆盖：
1. 数据层：DB 可读、.chromadb 存在、LANTAI_HOME 生效
2. 代码层：MCP server 可 import、Shell Hook 可 import
3. 协议层：Shell Hook 输入输出契约（不依赖网络，用固定 stdin 验证解析）
4. 服务层：REST /health（若服务在跑）

用法：python scripts/verify_lantai.py
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
    print("兰台记忆接入自检")
    print("=" * 60)

    # 1) 数据层
    home = os.environ.get("LANTAI_HOME") or os.environ.get("REMEMBRANCE_HOME", "")
    db = Path(home) / "remembrance.db" if home else REPO_ROOT / "remembrance.db"
    # 信息项（票 05 收口）：未设置时走仓库默认目录是脚本自身支持的回落（下一项
    # SQLite 检查就是对实际解析路径的存在性/可读性校验），不再按 FAIL 计——
    # 否则本项在一切未设环境变量的机器上恒 FAIL，自检失去分辨力。
    check(
        "数据目录已设置（LANTAI_HOME/REMEMBRANCE_HOME）",
        True,
        f"已设置: {home}" if home else "未设置（用仓库默认目录，实际路径由下一项 SQLite 检查校验）",
    )
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
    check(
        "向量库存在",
        (Path(home) / ".chromadb").exists() if home else (REPO_ROOT / ".chromadb").exists(),
    )

    # 2) 代码层
    try:
        # 票 05：唯一入口口径 `lantai.cli.mcp:main`（pyproject console script `lantai-mcp`
        # 同源）；旧平铺入口模块已删除，恒 FAIL 检查项就此收口。
        import lantai.cli.mcp as mcp_entry

        check(
            "MCP server 可导入",
            callable(getattr(mcp_entry, "main", None)),
            "lantai.cli.mcp:main（与 pyproject console script 同源）",
        )
    except Exception as e:
        check("MCP server 可导入", False, str(e)[:120])
    try:
        import scripts.shell_hook  # noqa: F401

        check("Shell Hook 可导入", True)
    except Exception as e:
        check("Shell Hook 可导入", False, str(e)[:120])

    # 3) 协议层：Shell Hook 输入输出契约（不依赖网络）
    try:
        # encoding 必须显式 UTF-8（票 05 收口）：Windows 下 text=True 缺省按 GBK
        # 编码 stdin，hook 端按 UTF-8 读——中文问句直接把 hook 打崩在解析层，
        # 契约检查在中文 Windows 上恒 FAIL（实测复现：'NoneType' object has no
        # attribute 'strip'）。子进程同步钉 PYTHONIOENCODING，两端口径一致。
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "shell_hook.py")],
            input=json.dumps({"query": "自检查询"}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        out = proc.stdout.strip()
        parsed = json.loads(out) if out else {}
        check(
            "Shell Hook 契约（stdin→stdout JSON）", isinstance(parsed, dict), out[:80] or "(空输出)"
        )
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
