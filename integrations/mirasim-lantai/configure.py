"""Preview host configuration; write only into an explicitly selected new directory."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HOSTS = ("claude-code", "codex", "dsh")
ENTRY = Path(__file__).resolve().with_name("entry.py")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build_bundle(
    host: str, home: Path, *, python: Path | None = None, hooks: bool = False
) -> dict[str, str]:
    """Return host-native files without reading credentials or touching host state."""
    if host not in HOSTS:
        raise ValueError("host must be claude-code, codex, or dsh")
    home = Path(home)
    python = Path(python or sys.executable)
    if not home.is_absolute():
        raise ValueError("home must be an absolute data directory")
    if home.exists() and not home.is_dir():
        raise ValueError("home must be a directory")
    if not python.is_absolute() or not python.is_file():
        raise ValueError("python must be an existing absolute executable path")
    if hooks and host == "dsh":
        raise ValueError("DSH uses its plugin lifecycle, not command hooks")
    if hooks and os.name == "nt":
        # list2cmdline quotes argv for CreateProcess, not cmd.exe expansion/operators.
        for path in (home, python, ENTRY):
            if any(char in str(path) for char in '&|<>^%!"()\r\n'):
                raise ValueError("hook paths must not contain Windows shell expansion characters")
    server = {
        "command": str(python),
        "args": [str(ENTRY), "mcp", "--host", host],
        "env": {"PYTHONUTF8": "1", "LANTAI_HOME": str(home), "LANTAI_MCP_PROFILE": "core"},
    }
    if host == "dsh":
        return {
            "cordis.patch.yml": _json(
                [
                    {
                        "insert": [
                            {
                                "id": "lantai-attribution",
                                "name": "@local/dsh-lantai-attribution",
                                "disabled": True,
                                "config": {
                                    "serverName": "lantai",
                                    "transport": "stdio",
                                    **server,
                                    "expandTemplates": False,
                                    "failOnStartupError": True,
                                },
                            }
                        ]
                    }
                ]
            )
        }
    if host == "claude-code":
        files = {".mcp.json": _json({"mcpServers": {"lantai": server}})}
    else:
        # Keep Unicode literal: JSON surrogate-pair escapes are invalid TOML.
        files = {
            ".codex/config.toml": (
                "[mcp_servers.lantai]\n"
                f"command = {json.dumps(server['command'], ensure_ascii=False)}\n"
                f"args = {json.dumps(server['args'], ensure_ascii=False)}\n"
                "\n[mcp_servers.lantai.env]\n"
                + "".join(
                    f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
                    for key, value in server["env"].items()
                )
            )
        }
    if hooks:
        # Put data selection in argv: command hooks have no MCP-style env field.
        argv = [str(python), str(ENTRY), "hook", "--host", host, "--home", str(home)]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        hook = {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
        settings = {"hooks": {"SessionStart": [hook], "UserPromptSubmit": [hook]}}
        if host == "claude-code":
            files[".claude/settings.json"] = _json(settings)
        else:
            files[".codex/config.toml"] += "\n[features]\nhooks = true\n"
            files[".codex/hooks.json"] = _json(settings)
    return files


def write_bundle(files: dict[str, str], output: Path) -> None:
    """Refuse all existing destinations, including dangling directory links."""
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; choose a new review directory")
    for name in files:
        path = Path(name)
        if path.anchor or path.drive or ".." in path.parts:
            raise ValueError("bundle paths must stay inside output")
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def main() -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser(description="兰台三宿主配置预览与隔离导出")
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--hooks", action="store_true", help="显式加入命令钩子")
    parser.add_argument("--output", type=Path, help="写入全新审阅目录；缺省仅预览")
    args = parser.parse_args()
    try:
        files = build_bundle(args.host, args.home, python=args.python, hooks=args.hooks)
        if args.output is not None:
            write_bundle(files, args.output)
        print(_json(files), end="")
    except (OSError, ValueError) as exc:
        print(f"配置未生成：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
