r"""票 81：全仓无效转义序列残留清零锁（py_compile + Warning-as-error 双 filter）。

病灶三处（派单方 2026-11-11 现场 HEAD `241cf6ce` 扫描实证，票面原立两处、
第三处系票 54/51 出生面派单扩权收口，不冒充票面原案）：
1. `lantai/core/text.py:32` — `like_escape` 文档串 `\ % _`（票 28 出生面
   `2c89c9f`/`cf8edbf`；票面写 :28 已漂移）；
2. `tests/test_like_wildcard_escape.py:1` — 模块文档串 `\w`（票 28 出生面）；
3. `tests/test_fence_attr_escape_smoke.py:19` — 文档串 `\-`（票 54/51 出生面
   `66dea8ae`）。

弃用轨迹（DeprecationWarning 3.6 → SyntaxWarning 3.12 → SyntaxError 未来
版本）：venv 3.11 发 DeprecationWarning、3.12+ 发 SyntaxWarning，被 pyproject
`filterwarnings=["ignore::DeprecationWarning"]` 全局吞掉——CI 全绿假象机制。
双 filter（SyntaxWarning + DeprecationWarning as error）两解释器都锁住，不受
宿主 Python 版本影响。被测对象是磁盘上真实源码，真编译真文件，禁 mock
（py_compile 真编译即真执行，兼为不 mock 冒烟测试）。
"""

from __future__ import annotations

import py_compile
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 票 81 病灶三文件（产品 1 + 测试 2）。
_TICKET81_FILES = [
    _REPO_ROOT / "lantai" / "core" / "text.py",
    _REPO_ROOT / "tests" / "test_like_wildcard_escape.py",
    _REPO_ROOT / "tests" / "test_fence_attr_escape_smoke.py",
]

# 全仓 CLEAN 臂目录（派单规格钉死三目录）。
_CLEAN_DIRS = [
    _REPO_ROOT / "lantai",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "scripts",
]


def _compile_with_escape_filters(path: Path) -> None:
    """py_compile 真编译磁盘文件；SyntaxWarning/DeprecationWarning 即 raise。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        warnings.simplefilter("error", DeprecationWarning)
        py_compile.compile(str(path), doraise=True)


def test_ticket81_three_lesion_files_compile_clean():
    """病灶三文件逐个真编译：任何无效转义序列（双 filter）即红。"""
    for path in _TICKET81_FILES:
        _compile_with_escape_filters(path)


def test_ticket81_whole_repo_scan_clean():
    """全仓 CLEAN 臂：lantai/ tests/ scripts/ 三目录逐文件真编译零命中。

    防第三处再生；被测对象是磁盘上真实源码，禁 mock（真编译真文件）。"""
    hits = []
    for d in _CLEAN_DIRS:
        for p in sorted(d.rglob("*.py")):
            try:
                _compile_with_escape_filters(p)
            except (
                py_compile.PyCompileError,
                SyntaxWarning,
                DeprecationWarning,
            ) as exc:
                hits.append(f"{p.relative_to(_REPO_ROOT)}: {exc}")
    assert not hits, f"无效转义序列残留 {len(hits)} 处（票 81 CLEAN 臂）：\n" + "\n".join(hits)
