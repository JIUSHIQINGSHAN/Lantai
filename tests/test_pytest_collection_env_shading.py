"""命名空间遮蔽防线（票 .scratch/v0.68-next-window-2026-11/04）。

病理：宿主（Hermes 拉起的 shell）PYTHONPATH 含 hermes-agent 目录，其 `tests`
是带 `__init__.py` 的正式包；本仓 `tests/` 若无 `__init__.py` 只是命名空间
部分——Python 路径扫描规则是「任何位置的正式包压过全部命名空间部分」，与
pyproject `pythonpath=["."]` 把仓库根插到 sys.path 首位无关。结果 conftest
第一条 `from tests.support import ...` 即炸，全量收集零用例失败（假绿面：
不熟悉该报错形态的会话可能误判为代码问题）。

修复（本文件红→绿锁定）：本仓 `tests/` 升格为正式包——正包对正包时位置
定胜负，pytest 的仓库根注入在 sys.path 首位，本地包稳定反压宿主包。

两臂都用子进程真实复现（不 mock 解释器行为，不 `python -I`——隔离模式会连
本修复依赖的路径注入一并屏蔽，测不出目标行为；子进程自带环境零全局污染）。

第二向量（同样实测过）：PYTHONPATH 条目位次先于本仓 venv 的 site-packages，
外来环境的第三方依赖（hermes venv 的 pydantic，为他家解释器编译）会抢解析，
import 层直接二进制不兼容崩——`tests/__init__.py` 治不了它，由唯一登记式
写入器的外来条目清除（`harness_state.scrub_foreign_sys_path`）负责，第三条
测试锁定。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _shadow_dir(tmp_path: Path) -> Path:
    """假 hermes 形态：正式 `tests` 包、无 support 子模块（票 04 病灶最小复现体）。"""
    d = tmp_path / "shadow"
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "__init__.py").write_text("", encoding="utf-8")
    return d


def _child_env(shadow: Path) -> dict[str, str]:
    # PYTHONIOENCODING 钉 UTF-8：Windows 宿主缺省 cp936，父进程按 GBK 解码
    # UTF-8 报错文本会炸在 decode 层（票 30 同族教训）——报错原文必须可读。
    return {**os.environ, "PYTHONPATH": str(shadow), "PYTHONIOENCODING": "utf-8"}


def test_collection_survives_foreign_regular_tests_package(tmp_path):
    """敌意 PYTHONPATH（含正式 tests 包）下收集不得被打断（票 04 红样回归锁）。"""
    shadow = _shadow_dir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_retrieval_veto.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        env=_child_env(shadow),
        timeout=300,
    )
    assert proc.returncode == 0, (
        "全量收集被命名空间遮蔽打断（票 04 红样：正式 tests 包压过本仓命名空间）：\n"
        f"{(proc.stderr + proc.stdout)[-2000:]}"
    )
    combined = proc.stderr + proc.stdout
    assert "No module named 'tests.support'" not in combined


def test_import_tests_resolves_to_repo_under_hostile_pythonpath(tmp_path):
    """provenance 锁：敌意 PYTHONPATH 下 `import tests` 必须解析到本仓 `__init__.py`。"""
    shadow = _shadow_dir(tmp_path)
    probe = "import json, tests; print(json.dumps({'tests': tests.__file__}))"
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        env=_child_env(shadow),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    resolved = json.loads(proc.stdout)["tests"].replace("\\", "/").lower()
    expected = str(REPO_ROOT / "tests" / "__init__.py").replace("\\", "/").lower()
    assert resolved == expected, (
        f"`import tests` 被遮蔽到 {resolved}，应解析到本仓 {expected}"
        "（票 04：命名空间部分斗不过外来正式包，本仓 tests 必须自身是正式包）"
    )


def test_collection_survives_foreign_site_packages_dependency_shadow(tmp_path):
    """敌意 PYTHONPATH 里的外来 site-packages 不得遮蔽本仓 venv 的第三方依赖。

    病理：PYTHONPATH 条目在 sys.path 上先于 venv site-packages——hermes 宿主
    venv 的 pydantic（为宿主解释器编译）抢在本仓依赖之前解析，import 层直接
    崩（巡检实锤：pydantic_core 原生模块不兼容 ImportError）。conftest bootstrap
    必须清掉「既不在本仓、也不在活动解释器 sys.prefix 下」的外来条目。
    """
    foreign = tmp_path / "foreign-venv-site"
    (foreign / "pydantic").mkdir(parents=True)
    (foreign / "pydantic" / "__init__.py").write_text(
        "raise ImportError('遮蔽体：外来 pydantic 被解析，conftest 未清外来 sys.path')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_retrieval_veto.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        env=_child_env(foreign),
        timeout=300,
    )
    assert proc.returncode == 0, (
        "外来 site-packages 遮蔽了本仓 venv 的第三方依赖（票 04 第二向量）：\n"
        f"{(proc.stderr + proc.stdout)[-2000:]}"
    )
    assert "遮蔽体" not in (proc.stderr + proc.stdout)
