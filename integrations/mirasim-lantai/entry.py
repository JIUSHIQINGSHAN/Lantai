"""Thin, repository-relative entry for reviewed Claude Code/Codex/DSH bundles."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser(description="兰台客户端入口")
    parser.add_argument("mode", choices=("mcp", "hook"))
    parser.add_argument("--host", required=True, choices=("claude-code", "codex", "dsh"))
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    home = args.home or Path(os.environ.get("LANTAI_HOME", ""))
    if not home.is_absolute() or (home.exists() and not home.is_dir()):
        print("入口未启动：必须显式配置绝对路径 LANTAI_HOME 数据目录", file=sys.stderr)
        return 2
    if not os.environ.get("API_KEY", "").strip():
        print("入口未启动：必须在宿主环境配置 API_KEY", file=sys.stderr)
        return 2
    if args.mode == "hook" and args.host == "dsh":
        print("入口未启动：DSH 使用插件生命周期", file=sys.stderr)
        return 2
    os.environ["LANTAI_HOME"] = str(home)
    # Pin all storage paths together, rather than inheriting another host's database.
    os.environ["DATABASE_URL"] = f"sqlite:///{(home / 'remembrance.db').as_posix()}"
    os.environ["CHROMADB_PATH"] = str(home / ".chromadb")
    sys.path.insert(0, str(ROOT))
    if args.mode == "mcp":
        os.environ["LANTAI_MCP_PROFILE"] = "core"
        from lantai.cli.mcp import main as serve

        serve()
    else:
        os.environ["LANTAI_HOST"] = args.host
        os.environ.setdefault("SHELL_HOOK_TIMEOUT", "8")
        # run_path preserves the existing handler, framing, and receipt contract.
        sys.argv = [str(ROOT / "scripts" / "shell_hook.py")]
        runpy.run_path(sys.argv[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
