"""MCP 入口三处冒烟（票 `.scratch/v0.68-next-window-2026-11/05-mcp-entry-code-broken.md`）。

病灶：`scripts/mcp_server.py` 已删（现行唯一入口 `lantai.cli.mcp:main`，与 pyproject
console script 一致），但 launcher_mcp.py / launch_mcp.bat / scripts/verify_lantai.py
三处仍指向旧入口或旧机器绝对路径——照脚本做必然 ImportError。

三臂全为真实执行、不 mock 被测导入链：
1. launcher_mcp.py 子进程干跑：stdin 直接 EOF，`main()` 的 stdio 循环空转即退——
   绿态应干净退出；红态死在 `ModuleNotFoundError: mcp_server`。判据按票面：
   不得抛 ModuleNotFoundError（宁可死在可解释的启动校验上）。
2. scripts/verify_lantai.py 子进程实跑（真仓环境）：解析 stdout，「MCP server 可导入」
   检查项必须 PASS——该脚本本身是部署自检工具，恒 FAIL 即失去分辨力。
3. 进程内真实 `import lantai.cli.mcp` + `callable(main)`（入口存在性硬断言）。
另加旧机器绝对路径回归锁（`C:\\Users\\Asus` / `.venv-audit` 三文件禁现）。

子进程属外部边界（票面允许）；env 只摘 PYTHONPATH/PYTHONHOME（票 04 同源污染），
其余原样透传；`-I` 隔离宿主 site/user-site 干扰，被测脚本各自负责仓库根入 path。
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

_FORBIDDEN_IMPORT_ERRORS = ("ModuleNotFoundError", "No module named")
_FORBIDDEN_LEGACY = ("C:\\Users\\Asus", ".venv-audit")


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    # -X utf8 而非 PYTHONIOENCODING：-I 隐含 -E 会忽略一切 PYTHON* 环境变量，
    # UTF-8 stdio 必须走命令行（实测：靠环境变量时子进程 stdio 退回 GBK，
    # 中文检查项在捕获流里成 mojibake，断言只能信 ASCII 令牌）。
    return subprocess.run(  # noqa: S603 — 固定 argv 测试装置
        [PY, "-I", "-X", "utf8", "-u", *args],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(REPO_ROOT),
        env=_clean_env(),
    )


class TestLauncherSmoke:
    def test_launcher_dry_run_without_module_not_found(self):
        """launcher_mcp.py 干跑：stdin EOF 下子进程退出，全程不得现 ModuleNotFoundError。

        按票面口径不断言 returncode==0：环境缺件时允许死在可解释的启动校验上，
        但入口 import 链断（ModuleNotFoundError）必红；入口存在性由
        TestUniqueEntryContract 的进程内真实导入硬断言补齐。
        """
        proc = _run([str(REPO_ROOT / "launcher_mcp.py")], timeout=60)
        combined = (proc.stdout or "") + (proc.stderr or "")
        for needle in _FORBIDDEN_IMPORT_ERRORS:
            assert needle not in combined, (
                f"launcher_mcp.py 照跑即断（票 05 病灶 1 未修）——子进程输出：\n{combined[:2000]}"
            )

    def test_launcher_has_no_legacy_machine_paths(self):
        """launcher 不得再硬编码旧机器绝对路径（`C:\\Users\\Asus` / `.venv-audit`）。"""
        text = (REPO_ROOT / "launcher_mcp.py").read_text(encoding="utf-8")
        for legacy in _FORBIDDEN_LEGACY:
            assert legacy not in text, f"launcher_mcp.py 仍含旧机器残留：{legacy}"


class TestVerifyLantaiMcpCheck:
    def test_verify_mcp_check_passes_in_real_repo(self):
        """verify_lantai.py 实跑：「MCP server 可导入」检查项必须 PASS（票 05 病灶 3）。

        断言只信 ASCII 令牌（`MCP` + `lantai.cli.mcp:main` + `[PASS]`）——
        中文在跨进程捕获流里可能被任一端的控制台编码折腾（见 _run 注释），
        ASCII 匹配对编码彻底免疫。
        """
        proc = _run([str(REPO_ROOT / "scripts" / "verify_lantai.py")])
        lines = (proc.stdout or "").splitlines()
        mcp_lines = [ln for ln in lines if "MCP" in ln and "lantai.cli.mcp" in ln]
        assert mcp_lines, f"verify_lantai.py 输出中找不到 MCP 检查项：\n{proc.stdout[:2000]}"
        assert any(ln.startswith("[PASS]") for ln in mcp_lines), (
            f"MCP 检查项非 PASS（票 05 票病灶 3 未修）：{mcp_lines}"
        )

    def test_verify_script_has_no_legacy_import(self):
        """verify_lantai.py 不得再 import 已删除的 scripts.mcp_server。"""
        text = (REPO_ROOT / "scripts" / "verify_lantai.py").read_text(encoding="utf-8")
        assert "scripts.mcp_server" not in text, (
            "verify_lantai.py 仍指向已删除的 scripts.mcp_server"
        )


class TestUniqueEntryContract:
    def test_lantai_cli_mcp_main_importable_and_callable(self):
        """进程内真实导入唯一入口 `lantai.cli.mcp:main`（不 mock、不替身）。"""
        import lantai.cli.mcp as mcp_entry

        assert callable(getattr(mcp_entry, "main", None)), "lantai.cli.mcp:main 不存在或不可调用"

    def test_three_entry_files_point_to_same_contract(self):
        """三处入口统一 `lantai.cli.mcp` 口径，旧入口/旧路径零残留（票 05 修复目标 4）。"""
        launcher = (REPO_ROOT / "launcher_mcp.py").read_text(encoding="utf-8")
        bat = (REPO_ROOT / "launch_mcp.bat").read_text(encoding="utf-8")
        verify = (REPO_ROOT / "scripts" / "verify_lantai.py").read_text(encoding="utf-8")

        assert "from lantai.cli.mcp import main" in launcher
        assert "mcp_server" not in launcher.replace("launcher_mcp", ""), (
            "launcher_mcp.py 仍残留 mcp_server 引用"
        )
        assert "lantai.cli.mcp" in bat or "lantai-mcp" in bat, "bat 未指向唯一入口"
        assert "mcp_server" not in bat, "bat 仍残留 mcp_server 引用"
        assert "lantai.cli.mcp" in verify, "verify_lantai.py 未指向唯一入口"

        # bat 相对仓库根定位（%~dp0），三文件旧机器绝对路径零残留
        assert "%~dp0" in bat, "bat 未用 %~dp0 相对定位仓库根"
        for name, text in (
            ("launcher_mcp.py", launcher),
            ("launch_mcp.bat", bat),
            ("verify_lantai.py", verify),
        ):
            for legacy in _FORBIDDEN_LEGACY:
                assert legacy not in text, f"{name} 仍含旧机器残留：{legacy}"
