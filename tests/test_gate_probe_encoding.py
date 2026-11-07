"""门禁探针/子进程出口编码测试（票 30：A1 假 FAIL 门 + A4 _git 解码）。

被测链路全部真实执行、不 mock：
- 子进程用真解释器、真管道（raw bytes 捕获，父侧不替子侧做编码）；
- _git 用真实 git 子进程 + 临时仓库真 CJK 文件名。

红测前提（票 30 TDD 4）：本机 ANSI codepage = cp936（GBK）时红臂成立；
若宿主已整体处于 UTF-8 模式（PYTHONUTF8=1 等），子进程本就 utf-8，
红臂退化为回归锁（语义仍为「子出口必须钉 UTF-8」），不做假环境红。
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RELEASE_CHECK = Path(__file__).parent.parent / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check_probe_encoding", _RELEASE_CHECK)
RC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RC)

REPO = Path(__file__).resolve().parent.parent
STUB_DIR = REPO / "tests" / "support" / "host_matrix_stub"

# GBK 无法编码的字符：𠮷 (U+20BB7) 与 🧠 (U+1F9E0)——cp936 子进程 print 即 UnicodeEncodeError
_NON_GBK_PAYLOAD = "𠮷🧠"

# 票 30 A2 哨兵规则：stdout print 载荷含 ensure_ascii=False 的脚本，必须钉 UTF-8 出口
# （reconfigure 或 TextIOWrapper 两范式任一）。fail-closed：新增同款 print 不钉即红。
_PIN_MARKERS = (
    'sys.stdout.reconfigure(encoding="utf-8"',
    "io.TextIOWrapper(sys.stdout.buffer",
)
_PRINT_ASCII_OFF = re.compile(r"^\s*print\(.*ensure_ascii=False")


def _require_git() -> None:
    if not shutil.which("git"):
        pytest.skip("整机无 git 可执行文件，无法真实执行 _git 链路（宁 skip 不假绿）")


class TestIsolatedProbeEnvPinsChildStdio:
    def test_env_carries_pythonioencoding_utf8(self, tmp_path: Path):
        """对照臂：探针环境必须显式钉子进程 stdio 编码（对齐 verify_lantai.py 姊妹口径）。"""
        env = RC.isolated_probe_env(str(tmp_path / "probe-home"))
        assert env.get("PYTHONIOENCODING") == "utf-8"

    def test_probe_child_prints_non_gbk_chars_as_utf8_bytes(self, tmp_path: Path):
        """主臂：经探针环境拉起的子进程向管道写 GBK 外字符必须活下来且是 UTF-8 字节。

        票 30 病理（修复前，cp936 宿主实况）：子 Python 对管道按 locale cp936 编码，
        print('𠮷🧠') 直接 UnicodeEncodeError → 探针非零退出 → 假 FAIL 挡发布；
        或中文以 GBK 落管、父侧按 UTF-8 解码 → 门禁报告全 U+FFFD。
        """
        env = RC.isolated_probe_env(str(tmp_path / "probe-home"))
        proc = subprocess.run(
            [sys.executable, "-c", f"print({_NON_GBK_PAYLOAD!r})"],
            env=env,
            capture_output=True,  # 故意不传 encoding：断言子进程落管的原始字节
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
        assert proc.stdout.decode("utf-8").strip() == _NON_GBK_PAYLOAD


class TestGitHelperDecodesUtf8:
    def test_git_output_with_cjk_filename_round_trips(self, tmp_path: Path):
        """A4：_git 裸 text=True 按 cp936 解码 git（Git-for-Windows 发 UTF-8）——
        CJK 路径经 quotepath=false 原样输出时必须按 UTF-8 解码，不产 mojibake。"""
        _require_git()
        repo = tmp_path / "repo"
        repo.mkdir()
        cjk_name = "档案-𠮷.md"
        (repo / cjk_name).write_text("x", encoding="utf-8")
        for args in (
            ["init", "-q"],
            ["-c", "user.name=测试", "-c", "user.email=t@t", "add", cjk_name],
            ["-c", "user.name=测试", "-c", "user.email=t@t", "commit", "-q", "-m", "档案提交"],
        ):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

        r = RC._git(repo, "-c", "core.quotepath=false", "ls-files")
        assert r.returncode == 0, r.stderr
        assert cjk_name in r.stdout, f"git 输出解码失真: {r.stdout!r}"


class TestReportScriptsStdoutPinned:
    def test_ensure_ascii_print_scripts_pin_stdout_utf8(self):
        """A2 哨兵：report 面 print 载荷可含 GBK 外字符（emoji/U+2028/罕用字），
        cp936 宿主控制台/管道上直接 UnicodeEncodeError 崩点。凡 print(
        ...ensure_ascii=False) 的脚本必须钉 UTF-8 stdout（票 30 A2）。"""
        offenders = []
        for script in sorted((REPO / "scripts").glob("*.py")):
            text = script.read_text(encoding="utf-8")
            if not any(_PRINT_ASCII_OFF.match(ln) for ln in text.splitlines()):
                continue
            if not any(marker in text for marker in _PIN_MARKERS):
                offenders.append(script.name)
        assert not offenders, f"以下脚本 print ensure_ascii=False 却未钉 UTF-8 stdout: {offenders}"


class TestForgettingQualityReportArm:
    def test_report_lands_and_stdout_survives_non_gbk_query(self, tmp_path: Path):
        """A2 冒烟（票面 TDD 3）：run_forgetting_quality.py 真跑（临时 home）——
        报告含 GBK 外字符时，print 臂崩（修复前）/不崩（修复后）两态下报告文件均落盘。

        被测链路：真实 main() 的 render_report → print → write_text 全链；仅
        build_chinese_dataset 以输入替身换入含 '𠮷' 的最小数据集（数据源边界替身，
        纪律允许口径），检索管线真跑（host_matrix_stub 既有 embedding 网络替身 +
        临时 LANTAI_HOME，向量断网即按 ADR-0028 降级 FTS/BM25）。"""
        out_dir = tmp_path / "out"
        home = tmp_path / "lantai-home"
        home.mkdir(parents=True)
        out_str = str(out_dir).replace("\\", "/")
        driver = (
            "import sys\n"
            "sys.path.insert(0, 'scripts')\n"
            "sys.path.insert(0, '.')\n"
            "from lantai.storage.db import init_db\n"
            "init_db()\n"
            "import run_forgetting_quality as m\n"
            "def _dataset():\n"
            "    return {'name': 'enc-smoke', 'namespace': 'enc_smoke_v068t30',"
            " 'description': 'ticket30 smoke', 'apply_forgetting': False, 'cases': ["
            "{'category': 'typo', 'query': '𠮷字号查询',"
            " 'seeds': [{'content': '𠮷字号种子内容', 'lane': 'fact'}], 'target': 0}]}\n"
            "m.build_chinese_dataset = _dataset\n"
            f"sys.argv = ['run_forgetting_quality.py', '--intent', 'rule',"
            f" '--judge', 'rule', '--top-k', '3', '--out', r'{out_str}']\n"
            "raise SystemExit(m.main())\n"
        )
        env = dict(os.environ)
        env["LANTAI_HOME"] = str(home)
        env["LANTAI_TEST_STUB_EMBED"] = "1"
        env["PYTHONPATH"] = os.pathsep.join([str(STUB_DIR), str(REPO)])
        env["API_KEY"] = "test-key"
        # 故意不设 PYTHONIOENCODING：子进程 stdout 须由脚本自身钉（修复前 cp936 即红）
        env.pop("PYTHONIOENCODING", None)
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            cwd=REPO,
            env=env,
            capture_output=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")[-2000:]
        reports = list(out_dir.glob("*.md"))
        assert reports, "print 臂异常时报告文件未落盘（票 30 A2 顺序病理）"
        report_text = reports[0].read_text(encoding="utf-8")
        assert "𠮷字号查询" in report_text
        assert "𠮷字号查询" in proc.stdout.decode("utf-8")
