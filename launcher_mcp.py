"""Hermes-safe MCP launcher.

Problem: Hermes sets PYTHONPATH to its own venv site-packages, which pollutes subprocess imports.
The Hermes venv has a broken pydantic install that shadows the project venv's working pydantic.
Solution: Clear PYTHONPATH entirely and set up the project venv manually.

入口（票 .scratch/v068-docs-audit/01）：MCP server 已随 bea001c1 从
scripts/mcp_server.py 迁到 lantai.cli.mcp（console script `lantai-mcp`）；
仓库根与 venv 一律从本脚本所在位置解析，不再写死机器盘符。
"""

import os
import sys
from pathlib import Path

# --- Clear Hermes venv pollution ---
os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTHONHOME", None)

# Remove Hermes paths from sys.path (inherited via PYTHONPATH)
hermes_paths = [p for p in sys.path if "hermes" in p.lower()]
for p in hermes_paths:
    sys.path.remove(p)

# --- Set up project venv ---
ROOT = Path(__file__).resolve().parent

# venv 候选：标准 .venv 优先，旧部署名 .venv-audit 兜底；都不在则跳过注入，
# 交给当前解释器自己解析（如已用项目 venv 的 python 运行）。
VENV = next(
    (
        ROOT / name
        for name in (".venv", ".venv-audit")
        if (ROOT / name / "Lib" / "site-packages").is_dir()
    ),
    None,
)

# Prepend project paths (repo root first so lantai is imported from this checkout)
sys.path.insert(0, str(ROOT))
if VENV is not None:
    sys.path.insert(0, str(VENV / "Lib" / "site-packages"))

# Update PATH and VIRTUAL_ENV for any subprocess
if VENV is not None:
    os.environ["PATH"] = f"{VENV / 'Scripts'}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["VIRTUAL_ENV"] = str(VENV)

# --- Load .env file ---
try:
    from dotenv import load_dotenv

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[launcher] Loaded .env from {env_path}", file=sys.stderr)
except ImportError:
    pass

# Now import and run the MCP server
from lantai.cli.mcp import main  # noqa: E402

if __name__ == "__main__":
    main()
