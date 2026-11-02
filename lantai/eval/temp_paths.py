"""临时评测目录的分配与回收（票 05 续17：「临时物自动回收 / 不污染仓库外路径」验收线）。

背景（本机实测，非推断）：`lantai/eval/offline.py::run_offline_eval()` 与
`lantai/eval/deterministic_env.py::staged_deterministic_env()` 各自 `mkdtemp` 后从不回收
（修复前 `lantai/` 全树 `rmtree` 命中数 = 0），一轮票面全量净增 3 个 `lantai-fq-check-*`
+ 6 个 `lantai-staged-env-*`，本机累计 981 个目录 / 452 MB 且逐轮单调增长。

两条回收路由各对应一条实测约束：

- `reclaim()` 即时回收：SQLite 文件在 `engine.dispose()` 之后可当场删除（探针
  `.scratch/gate/r17/probe_dispose.py`：dispose-only ⇒ `exists=False`）。
- `reclaim_stale()` 超龄清扫：`chromadb.PersistentClient` 的连接池被类属性
  `SharedSystemClient._identifier_to_system` 强引用，进程存活期内不释放，
  `gc.collect()` + 四轮退避 `rmtree` 仍删不掉 `chroma/chroma.sqlite3`（探针
  `.scratch/gate/r17/probe_staged2.py`）。所以被 Chroma 用过的目录只能等**下一次分配时**
  清扫上一进程遗留的超龄同前缀目录——泄漏上界从「无限」压成「本进程存活期内创建的目录数」。

安全边界（宁 miss 不脏写）：只承认 `LANTAI_TEMP_PREFIXES` 在册的前缀、只承认
`tempfile.gettempdir()` 的**直接子目录**，其余路径一律拒绝回收（返回 False，不抛不删）；
调用方显式传入的 `tmp_dir` / `data_dir` 由调用方拥有，永不由本模块删除。
`STALE_TEMP_DIR_SECONDS` 是「同前缀目录已无活进程持有」的安全边界，不是评测门槛。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

# 在册前缀：生产评测代码自己创建、因而自己负责回收的临时目录家族。
LANTAI_TEMP_PREFIXES: tuple[str, ...] = ("lantai-fq-check-", "lantai-staged-env-")

# 一轮票面全量约 5 分钟；1 小时足以断定没有活进程还持有某个遗留目录。
STALE_TEMP_DIR_SECONDS: float = 3600.0


def temp_root() -> Path:
    """本模块唯一承认的临时根。"""
    return Path(tempfile.gettempdir())


def _resolve_owned(path: Path, prefixes: tuple[str, ...]) -> Path | None:
    """把候选路径收敛为「temp 根下的在册前缀目录」，否则返回 None。

    用 `resolve()` 比较：既规范化大小写/短名，也让指向 temp 外的符号链接在
    父目录这一关就被拒（`resolved.parent` 不再是 temp 根）。
    """
    root = temp_root().resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved.parent != root:
        return None
    if not resolved.name.startswith(prefixes):
        return None
    return resolved


def _reclaim(path: Path, prefixes: tuple[str, ...]) -> bool:
    owned = _resolve_owned(path, prefixes)
    if owned is None:
        return False
    if not owned.exists():
        return True
    shutil.rmtree(owned, ignore_errors=True)
    return not owned.exists()


def reclaim(path: Path) -> bool:
    """即时回收自己创建的临时目录；返回「目录是否已不在」。

    前缀不在册、不在 temp 根下、或被第三方句柄（典型：Chroma）按住删不掉时，
    一律返回 False 交给 `reclaim_stale()`——绝不假装删掉了。
    """
    return _reclaim(path, LANTAI_TEMP_PREFIXES)


def reclaim_stale(
    prefixes: tuple[str, ...] = LANTAI_TEMP_PREFIXES,
    max_age_seconds: float = STALE_TEMP_DIR_SECONDS,
) -> int:
    """清扫超龄的同前缀遗留目录，返回删除成功的目录数（未超龄的一律不动）。"""
    cutoff = time.time() - max_age_seconds
    removed = 0
    for prefix in prefixes:
        for candidate in temp_root().glob(f"{prefix}*"):
            try:
                if not candidate.is_dir() or candidate.stat().st_mtime > cutoff:
                    continue
            except OSError:  # 目录正被占用/已被并发回收：跳过，不硬来
                continue
            if _reclaim(candidate, prefixes):
                removed += 1
    return removed


def new_temp_dir(prefix: str, *, max_age_seconds: float = STALE_TEMP_DIR_SECONDS) -> Path:
    """分配一个本项目在册的临时目录，并顺带清扫同前缀的超龄遗留。"""
    if prefix not in LANTAI_TEMP_PREFIXES:
        raise ValueError(f"未登记的临时目录前缀：{prefix!r}（在册：{LANTAI_TEMP_PREFIXES}）")
    reclaim_stale(prefixes=(prefix,), max_age_seconds=max_age_seconds)
    return Path(tempfile.mkdtemp(prefix=prefix))
