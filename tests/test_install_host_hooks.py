"""宿主钩子安装出口测试（票 06 宿主矩阵 / 片 05）。

缝隙：`build_snippet(host) -> str` 与 `main() -> int`（纯函数 + CLI 出口）。

安全语义（宁 miss 不脏写）：默认**只打印**，绝不写宿主目录；`--write` 才落盘。
"""

import json
import sys

import pytest

import scripts.install_host_hooks as cli


class TestBuildSnippet:
    def test_claude_code_snippet_is_valid_json_with_hooks(self):
        text = cli.build_snippet("claude-code")
        data = json.loads(text)
        assert "hooks" in data
        assert "UserPromptSubmit" in data["hooks"]

    def test_codex_snippet_mentions_features_flag(self):
        text = cli.build_snippet("codex")
        assert "features" in text and "hooks" in text

    def test_cursor_snippet_is_markdown_rule(self):
        text = cli.build_snippet("cursor")
        # Cursor 无命令钩子 → 降级档是静态规则（Markdown），不是 JSON
        assert "alwaysApply" in text or "---" in text

    def test_cursor_snippet_declares_not_in_matrix(self):
        """降级档须如实声明不计入命令钩子矩阵。"""
        text = cli.build_snippet("cursor")
        assert "不计入" in text or "降级" in text

    def test_hook_command_points_at_real_script(self):
        """片段里的 hook 命令须指向真实存在的 hook 脚本路径。"""
        data = json.loads(cli.build_snippet("claude-code"))
        blob = json.dumps(data, ensure_ascii=False)
        assert "shell_hook.py" in blob

    def test_hook_command_is_cross_platform(self):
        """命令不得用 `VAR=value cmd`（POSIX 语法，win32 的 cmd/PowerShell 报
        「不是内部或外部命令」——实证 rc=1）。

        路径用正斜杠是稳健性选择（消除对 `%r` 转义的依赖），非硬性要求——
        反斜杠若经 `%r` 正确转义（`\\\\`）同样可运行，已有实证，故不作断言。
        """
        cmd = cli._hook_command("claude-code")
        assert not cmd.startswith("LANTAI_HOST="), "不得用 POSIX 环境变量前缀"
        assert "runpy.run_path" in cmd and "LANTAI_HOST" in cmd

    def test_generated_command_actually_runs(self, tmp_path):
        """真实执行生成的命令（win32 实证：非零退出即失败）。"""
        import os
        import subprocess

        cmd = cli._hook_command("claude-code")
        data = json.loads(cli.build_snippet("claude-code"))
        real = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        env = dict(os.environ)
        env["LANTAI_HOME"] = str(tmp_path / "home")
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            real,
            shell=True,
            input='{"prompt":"x"}',
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=300,
        )
        assert r.returncode == 0, f"命令不可执行：{r.stderr[-500:]}"
        assert cmd  # 保持引用（避免未使用告警）


class TestMainDefaultPrintsOnly:
    def test_default_prints_without_writing(self, monkeypatch, capsys, tmp_path):
        """默认：打印到 stdout，不落任何文件。"""
        monkeypatch.setattr(sys, "argv", ["install_host_hooks.py", "--host", "claude-code"])
        before = set(tmp_path.iterdir())
        rc = cli.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "hooks" in out
        assert set(tmp_path.iterdir()) == before  # 未写

    def test_write_flag_writes_to_target_dir(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            sys,
            "argv",
            ["install_host_hooks.py", "--host", "claude-code", "--write", "--target", str(tmp_path)],
        )
        rc = cli.main()
        assert rc == 0
        written = list(tmp_path.rglob("*"))
        assert any(p.is_file() for p in written), "应落盘至少一个文件"
        assert "落点" in capsys.readouterr().out

    def test_invalid_host_errors_not_silent(self, monkeypatch, capsys):
        """非法 --host 值 → 报错且非零退出，不静默（argparse choices 拦截）。"""
        monkeypatch.setattr(sys, "argv", ["install_host_hooks.py", "--host", "nope"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code != 0
        assert "nope" in capsys.readouterr().err

    def test_refuses_to_overwrite_existing(self, monkeypatch, capsys, tmp_path):
        """落点已存在 → 拒绝覆盖（宁 miss 不脏写，不静默改坏用户配置）。"""
        existing = tmp_path / ".claude" / "settings.json"
        existing.parent.mkdir(parents=True)
        existing.write_text('{"user": "precious"}', encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            ["install_host_hooks.py", "--host", "claude-code", "--write", "--target", str(tmp_path)],
        )
        rc = cli.main()
        assert rc != 0
        assert "拒绝覆盖" in capsys.readouterr().err
        assert existing.read_text(encoding="utf-8") == '{"user": "precious"}'


class TestCursorDegradedLane:
    def test_cursor_write_produces_rule_and_agents(self, monkeypatch, capsys, tmp_path):
        """降级档含两件：`.mdc` 规则 + AGENTS.md 片段（设计规格承诺）。"""
        monkeypatch.setattr(
            sys,
            "argv",
            ["install_host_hooks.py", "--host", "cursor", "--write", "--target", str(tmp_path)],
        )
        assert cli.main() == 0
        rule = tmp_path / ".cursor" / "rules" / "lantai.mdc"
        agents = tmp_path / "AGENTS.md"
        assert rule.is_file() and "兰台" in rule.read_text(encoding="utf-8")
        assert agents.is_file() and "兰台记忆" in agents.read_text(encoding="utf-8")

    def test_cursor_appends_agents_without_clobbering(self, monkeypatch, capsys, tmp_path):
        """既有 AGENTS.md 须**追加**片段，不覆盖用户内容。"""
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# 用户既有内容\n", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            ["install_host_hooks.py", "--host", "cursor", "--write", "--target", str(tmp_path)],
        )
        assert cli.main() == 0
        text = agents.read_text(encoding="utf-8")
        assert "# 用户既有内容" in text
        assert "兰台记忆" in text

    def test_cursor_agents_append_is_idempotent(self, monkeypatch, capsys, tmp_path):
        """重复安装不重复追加片段。"""
        monkeypatch.setattr(
            sys,
            "argv",
            ["install_host_hooks.py", "--host", "cursor", "--write", "--target", str(tmp_path)],
        )
        assert cli.main() == 0
        # 第二次：.mdc 已存在 → 拒绝覆盖（返回非零），AGENTS.md 不应再追加
        rc = cli.main()
        assert rc != 0
        text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert text.count("兰台记忆") == 1
