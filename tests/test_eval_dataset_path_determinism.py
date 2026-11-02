"""评测数据集路径确定性回归锁（票 05）。

同一提交必须给出同一红绿。`tests/test_longmemeval_core.py` 的整模块 skipif 判据是
`DATASET.exists()`；若 DATASET 是相对路径，「这条评测线到底跑不跑」就取决于调用者的
当前工作目录。本文件不受该 skipif 影响，在任何机器、任何 cwd 下都真实执行这两条断言。
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_MODULE = REPO / "tests" / "test_longmemeval_core.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_probe_mod", _MODULE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_dataset_path_is_repo_anchored():
    ds = Path(_load().DATASET)
    assert ds.is_absolute(), f"DATASET 必须是仓库根锚定的绝对路径，实际 {ds!r} 随 cwd 漂移"
    assert ds == REPO / "data" / "longmemeval" / "longmemeval_s_cleaned.json"


def test_dataset_existence_is_cwd_independent(tmp_path, monkeypatch):
    m = _load()
    before = Path(m.DATASET).exists()
    monkeypatch.chdir(tmp_path)
    assert Path(m.DATASET).exists() is before, "DATASET.exists() 随 cwd 改变 => 门禁红绿不可复现"
