"""宿主钩子安装出口（票 06 宿主矩阵 / 片 05）。

把兰台的 shell hook 接到各 AI 宿主上。**默认只打印配置片段，绝不写宿主目录**
（宁 miss 不脏写——静默改坏用户配置不可接受）；显式 `--write` 才落盘并打印落点。

用法：
    python scripts/install_host_hooks.py --host claude-code            # 只打印
    python scripts/install_host_hooks.py --host codex --write          # 落盘
    python scripts/install_host_hooks.py --host cursor --write --target ./out

宿主矩阵与协议见 `docs/host-hook-protocol.md`。命令钩子矩阵 = hermes / claude-code
/ codex；**cursor 无命令钩子入口**，只提供降级档（静态规则注入）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO / "scripts" / "shell_hook.py"

# 命令钩子矩阵（走 shell_hook 协议）+ cursor 降级档。
# 注意：hermes 走**插件通道**（hermes-plugin/），不经 shell 钩子配置文件，
# 故本安装脚本不为它生成片段——放进 ALL_HOSTS 会造成「合法取值必然报错」。
DEGRADED_HOSTS = ("cursor",)
INSTALLABLE_HOSTS = ("claude-code", "codex") + DEGRADED_HOSTS
ALL_HOSTS = INSTALLABLE_HOSTS


def _hook_command(host: str) -> str:
    """宿主侧调用 hook 的命令行（设置 LANTAI_HOST 以触发响应适配）。

    **跨平台**：`VAR=value cmd` 是 POSIX 语法，在 Windows 的 cmd/PowerShell 下
    会报「不是内部或外部命令」（实证 rc=1）——本仓库主平台是 win32，故不用该形式。
    改用 `python -c "import os,runpy; os.environ[...]=...; runpy.run_path(...)"`，
    在任一平台的 shell 下都成立，且**路径含空格也安全**（整段在双引号内，
    内部的单引号只做 Python 字符串定界，不参与 shell 分词）。
    """
    # 路径一律正斜杠：反斜杠在 `-c` 的 Python 源码串里会被当转义序列
    # （`C:\Users\...` 的 `\U` 直接 SyntaxError）。Windows 的 Python 接受正斜杠路径。
    py = f'"{Path(sys.executable).as_posix()}"'
    hook = HOOK_SCRIPT.as_posix()
    inner = (
        f"import os, runpy; "
        f"os.environ['LANTAI_HOST'] = {host!r}; "
        f"runpy.run_path({hook!r}, run_name='__main__')"
    )
    return f'{py} -c "{inner}"'


def build_snippet(host: str) -> str:
    """生成该宿主的配置片段（字符串；未落盘）。"""
    if host == "claude-code":
        # 两个事件：UserPromptSubmit（每轮注入）+ SessionStart（会话启动注入底本
        # checkpoint，协议 §2.4）。二者共用同一 command handler。
        cmd = _hook_command("claude-code")
        return json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}
                    ],
                    "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}],
                }
            },
            ensure_ascii=False,
            indent=2,
        )

    if host == "codex":
        # Codex 须显式开启 features.hooks；事件名与 Claude Code 同名。
        cmd = _hook_command("codex")
        return json.dumps(
            {
                "features": {"hooks": True},
                "hooks": {
                    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": cmd}]}],
                    "SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}],
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    if host == "cursor":
        # Cursor 无命令钩子入口（调研实证，2026-09-25）——降级档：静态规则注入。
        # 如实声明其不计入命令钩子矩阵，避免「≥3 宿主冒烟」口径名不副实。
        return build_cursor_rule()

    raise ValueError(f"未知宿主：{host!r}（可选：{', '.join(ALL_HOSTS)}）")


def build_cursor_rule() -> str:
    """Cursor 降级档规则文件（`.cursor/rules/lantai.mdc`）内容。"""
    return (
        "---\n"
        "description: 兰台记忆使用规范\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# 兰台记忆（降级档）\n\n"
        "> 本宿主无命令型钩子入口，故**不计入命令钩子矩阵**——以下是静态规则注入，"
        "不含运行时检索与注入回执。需要完整能力请用命令钩子宿主"
        "（Hermes / Claude Code / Codex）。\n\n"
        "对话中确认的新事实，可调用兰台 MCP 工具 `add` 保存为长期记忆；"
        "需要检索时调用 `search`。每轮主动检索建议不超过 3 次。\n"
    )


def build_agents_fragment() -> str:
    """AGENTS.md 片段（Cursor 与 Codex 均原生读取 AGENTS.md）。

    设计规格承诺降级档含「AGENTS.md 片段 + .mdc 规则」两件：`.mdc` 是 Cursor
    专属规则，AGENTS.md 是跨宿主的最低公分母。**片段**语义＝供追加，不整文件覆盖。
    """
    return (
        "\n## 兰台记忆\n\n"
        "对话中确认的新事实，可调用兰台 MCP 工具 `add` 保存为长期记忆；"
        "需要检索时调用 `search`。每轮主动检索建议不超过 3 次。\n"
    )


def _target_for(host: str, target_dir: Path) -> Path:
    """各宿主片段的落点（在 target_dir 下）。"""
    if host == "claude-code":
        return target_dir / ".claude" / "settings.json"
    if host == "codex":
        return target_dir / ".codex" / "hooks.json"
    if host == "cursor":
        return target_dir / ".cursor" / "rules" / "lantai.mdc"
    raise ValueError(f"未知宿主：{host!r}")


def _write_cursor(target_dir: Path) -> int:
    """Cursor 降级档：落 `.mdc` 规则 + 追加 AGENTS.md 片段（不覆盖既有文件）。"""
    rule_path = _target_for("cursor", target_dir)
    if rule_path.exists():
        print(f"错误：落点已存在，拒绝覆盖：{rule_path}", file=sys.stderr)
        return 3
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    rule_path.write_text(build_cursor_rule(), encoding="utf-8")
    print(f"已写入落点：{rule_path}")

    agents = target_dir / "AGENTS.md"
    fragment = build_agents_fragment()
    if agents.exists():
        existing = agents.read_text(encoding="utf-8")
        if "兰台记忆" in existing:
            print(f"AGENTS.md 已含兰台片段，跳过：{agents}")
        else:
            agents.write_text(existing.rstrip() + "\n" + fragment, encoding="utf-8")
            print(f"已追加 AGENTS.md 片段：{agents}")
    else:
        agents.write_text(fragment.lstrip("\n"), encoding="utf-8")
        print(f"已写入落点：{agents}")

    print("（cursor 为降级档，不计入命令钩子矩阵。）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="兰台宿主钩子安装出口")
    parser.add_argument("--host", required=True, choices=list(ALL_HOSTS))
    parser.add_argument("--write", action="store_true", help="落盘（默认只打印）")
    parser.add_argument("--target", default=".", help="落盘根目录（默认当前目录）")
    args = parser.parse_args()

    try:
        snippet = build_snippet(args.host)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if not args.write:
        print(snippet)
        if args.host in DEGRADED_HOSTS:
            print(
                f"\n（提示：{args.host} 为降级档，不计入命令钩子矩阵；"
                f"另附 AGENTS.md 片段：）\n{build_agents_fragment().strip()}",
                file=sys.stderr,
            )
        return 0

    if args.host == "cursor":
        return _write_cursor(Path(args.target))

    path = _target_for(args.host, Path(args.target))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # 宁 miss 不脏写：不静默覆盖用户的既有配置
        print(f"错误：落点已存在，拒绝覆盖：{path}", file=sys.stderr)
        return 3
    path.write_text(snippet, encoding="utf-8")
    print(f"已写入落点：{path}")
    if args.host in DEGRADED_HOSTS:
        print(f"（{args.host} 为降级档，不计入命令钩子矩阵。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
