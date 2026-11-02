"""生产评测代码的临时目录必须自动回收（票 05 续17，「临时物自动回收 / 不污染仓库外路径」验收线缺口）。

本机实测事实（不是推断，复现见 .scratch/gate/r17/）：

- lantai/eval/offline.py::run_offline_eval() 每次 mkdtemp(prefix="lantai-fq-check-") 建
  eval.db + FTS 后**直接 return**，函数体没有任何 try/finally，目录原地留在 %TEMP%。
  其文档字符串写的「finally 清理种子，不污染真实库」只覆盖 DB 行，连目录本身都没删。
- lantai/eval/deterministic_env.py::staged_deterministic_env() 的 finally 只
  removeHandler + 还原 LANTAI_INTENT_OFF，同样不回收自建临时目录。
- 一轮票面全量（pytest tests/ -q -o addopts=""）实测净增 3 个 lantai-fq-check-*
  + 6 个 lantai-staged-env-*；本机累计 289 + 692 个目录 / 452 MB（r15 记 915 个 / 442 MB
  起，逐轮单调增长）。lantai/ 全树 rmtree 命中数 = 0，即生产代码从不回收自己造的临时物。

回收分两条路由（由探针结果决定，不是设计偏好）：

1. 即时回收：SQLite 文件在 engine.dispose() 后可当场删除
   （.scratch/gate/r17/probe_dispose.py：dispose-only => exists=False）。
2. 超龄清扫：chromadb.PersistentClient 的连接池被类属性
   SharedSystemClient._identifier_to_system 强引用，进程存活期不释放——
   .scratch/gate/r17/probe_staged2.py 实测 gc.collect() + 四轮退避 rmtree 仍删不掉
   chroma/chroma.sqlite3。故被 Chroma 用过的目录只能在**下一次分配时**清扫上一进程
   遗留的超龄同前缀目录，把「无限增长」压成「本进程存活期内的目录数」。

反向保险（宁 miss 不脏写）：tmp_dir / data_dir 由调用方拥有，回收器绝不删调用方目录，
也绝不删 temp 根以外的路径——scripts/run_e1_e2_formal.py:174 就依赖调用方目录留存为跑测证据。
"""

import inspect
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lantai.eval import temp_paths
from lantai.eval.deterministic_env import staged_deterministic_env
from lantai.eval.offline import run_offline_eval

FQ_PREFIX = "lantai-fq-check-"
STAGED_PREFIX = "lantai-staged-env-"
# 机制验证专用前缀：不碰 %TEMP% 里真实的历史泄漏目录（那些由生产路由自己清扫，
# 测试不代替用户做批量删除）
TEST_PREFIX = "lantai-reclaim-probe-"

MINIMAL_DATASET = {
    "name": "temp-reclaim-regression",
    "namespace": "eval_fq",
    "cases": [
        {
            "category": "fresh",
            "query": "机器学习用于图像识别",
            "seeds": [{"content": "机器学习用于图像识别与自然语言处理", "lane": "fact"}],
            "target": 0,
        }
    ],
}


def _dirs_with(prefix: str) -> set[str]:
    root = Path(tempfile.gettempdir())
    return {d.name for d in root.glob(f"{prefix}*") if d.is_dir()}


def _make_aged(name: str, age_seconds: float) -> Path:
    d = Path(tempfile.gettempdir()) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "eval.db").write_bytes(b"x")
    old = time.time() - age_seconds
    os.utime(d, (old, old))
    return d


# ── 1. run_offline_eval：正常路径必须零残留 ──────────────────────────────


def test_run_offline_eval_reclaims_its_own_temp_dir():
    before = _dirs_with(FQ_PREFIX)
    result = run_offline_eval(MINIMAL_DATASET)
    # 前提检查：评测真的跑了（不 mock 内部计算，只替外部 embed/向量/意图面）
    assert result["metrics"]["sample_count"] > 0, result["metrics"]
    assert _dirs_with(FQ_PREFIX) - before == set(), "run_offline_eval 在 %TEMP% 留下临时目录"


def test_run_offline_eval_reclaims_temp_dir_when_inner_eval_fails():
    """异常路径同样回收，且不吞异常。"""
    before = _dirs_with(FQ_PREFIX)
    with pytest.raises(RuntimeError, match="inner boom"):
        with patch(
            "lantai.eval.forgetting_quality.evaluate_forgetting_quality",
            side_effect=RuntimeError("inner boom"),
        ):
            run_offline_eval(MINIMAL_DATASET)
    assert _dirs_with(FQ_PREFIX) - before == set(), "异常路径泄漏临时目录"


# ── 2. staged_deterministic_env：自建目录必须回收 ────────────────────────


def test_staged_env_reclaims_self_created_temp_dir():
    before = _dirs_with(STAGED_PREFIX)
    with staged_deterministic_env() as captor:
        created = _dirs_with(STAGED_PREFIX) - before
        # 前提检查：CM 确实自建了恰好一个临时目录（否则本用例空转）
        assert len(created) == 1, created
        assert captor.records == []
    assert _dirs_with(STAGED_PREFIX) - before == set(), "staged_deterministic_env 泄漏临时目录"


def test_staged_env_reclaims_self_created_temp_dir_when_body_raises():
    before = _dirs_with(STAGED_PREFIX)
    with pytest.raises(ValueError, match="body boom"):
        with staged_deterministic_env():
            assert len(_dirs_with(STAGED_PREFIX) - before) == 1
            raise ValueError("body boom")
    assert _dirs_with(STAGED_PREFIX) - before == set(), "异常路径泄漏自建临时目录"
    assert "LANTAI_INTENT_OFF" not in os.environ, "修复不得改坏既有的环境变量复原"


def test_staged_env_never_deletes_caller_owned_tmp_dir(tmp_path):
    """反向保险：调用方传进来的目录属于调用方，回收器绝不删。"""
    own = tmp_path / "caller-owned"
    own.mkdir()
    before = _dirs_with(STAGED_PREFIX)
    with staged_deterministic_env(own):
        assert (own / "chroma").is_dir(), "CM 未把调用方目录当落点（前提检查）"
    assert own.is_dir(), "回收器删掉了调用方拥有的目录（脏写）"
    assert (own / "chroma").is_dir()
    assert _dirs_with(STAGED_PREFIX) - before == set(), "传了 tmp_dir 还另建临时目录"


def test_staged_env_never_deletes_caller_owned_data_dir(tmp_path):
    own = tmp_path / "eval-data"
    own.mkdir()
    with staged_deterministic_env(data_dir=own):
        assert (own / "chroma").is_dir()
        assert (own / "eval.db").exists()
    assert own.is_dir() and (own / "chroma").is_dir(), "data_dir 被回收（脏写）"


def test_staged_env_restores_preexisting_intent_flag(monkeypatch):
    """既有 finally 的语义不得被回收逻辑改坏：原值必须逐字还原。"""
    monkeypatch.setenv("LANTAI_INTENT_OFF", "keep-me")
    with staged_deterministic_env():
        assert os.environ["LANTAI_INTENT_OFF"] == "1"
    assert os.environ["LANTAI_INTENT_OFF"] == "keep-me"


# ── 3. 回收器自身的判据（机制验证用隔离前缀，不动历史泄漏） ──────────────


def test_reclaim_stale_removes_aged_dirs_and_keeps_fresh_ones():
    aged_a = _make_aged(f"{TEST_PREFIX}aged-a", temp_paths.STALE_TEMP_DIR_SECONDS + 600)
    aged_b = _make_aged(f"{TEST_PREFIX}aged-b", temp_paths.STALE_TEMP_DIR_SECONDS + 600)
    fresh = _make_aged(f"{TEST_PREFIX}fresh-c", 5)
    try:
        removed = temp_paths.reclaim_stale(prefixes=(TEST_PREFIX,))
        assert removed >= 2, removed
        assert not aged_a.exists() and not aged_b.exists()
        # 未超龄的一律不动：可能是别的并发进程正在用的目录
        assert fresh.exists(), "清扫越界删掉未超龄目录"
    finally:
        _rmtree_quiet(fresh)


def test_reclaim_refuses_paths_outside_temp_root(tmp_path):
    outside = tmp_path / "lantai-fq-check-notmine"
    outside.mkdir()
    assert temp_paths.reclaim(outside) is False
    assert outside.is_dir(), "回收器删了 temp 根以外的路径（脏写）"


def test_reclaim_refuses_foreign_prefix():
    alien = Path(tempfile.gettempdir()) / "not-lantai-temp-alien"
    alien.mkdir(exist_ok=True)
    marker = alien / "keep.txt"
    marker.write_text("x")
    try:
        assert temp_paths.reclaim(alien) is False
        assert marker.exists(), "回收器删了非本项目前缀的目录（脏写）"
    finally:
        _rmtree_quiet(alien)


def test_new_temp_dir_only_accepts_registered_prefixes():
    assert set(temp_paths.LANTAI_TEMP_PREFIXES) == {FQ_PREFIX, STAGED_PREFIX}
    with pytest.raises(ValueError):
        temp_paths.new_temp_dir("some-other-thing-")


def test_production_sites_allocate_via_reclaiming_helper():
    """接线检查：两个泄漏点必须走 temp_paths（否则机制绿了病灶还活着）。"""
    src_offline = inspect.getsource(run_offline_eval)
    src_staged = inspect.getsource(staged_deterministic_env)
    assert "temp_paths.new_temp_dir(" in src_offline, "offline 未接回收器"
    assert "temp_paths.new_temp_dir(" in src_staged, "staged_env 未接回收器"
    assert "mkdtemp(" not in src_offline and "mkdtemp(" not in src_staged
    assert "temp_paths.reclaim(" in src_offline
    assert "temp_paths.reclaim(" in src_staged


def _rmtree_quiet(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
