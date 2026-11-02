"""宿主真实数据目录绊线（票 `.scratch/v0.66-next-window-2026-11` 票 05）。

`scripts/release_check.py` 用它把「测试有没有连宿主真实 LANTAI_HOME 库」变成
**可阻断**的事实：只监听 `sqlite3.connect` 的路径，不改变任何行为——每次连接都
原样放行，命中真实库路径的那一次记一行 `[HIT]` 到报告。

为什么按**路径**判定而不是比对 mtime：开发机上常驻的兰台服务自己也会写库，
mtime 一直在变却与测试无关；绊线只认真正由测试进程发起的那一次连接。
绊线没装载 = 无法证明 = 门禁不放行（见 `tripwire_report_issues`）——不给
「静默不检测」留成假绿通道。

装载（只加监听，不改测试语义）：
    PYTHONPATH=tests/support pytest tests/ -p real_home_tripwire
环境变量：
    LANTAI_GATE_REAL_HOME  要盯住的真实库文件（空则回落 settings 推导）
    LANTAI_GATE_REPORT     报告落盘路径（空则整个绊线不装载，零开销）
"""

import os
import sqlite3
import traceback

REPORT = os.environ.get("LANTAI_GATE_REPORT", "")
REAL_HOME = os.environ.get("LANTAI_GATE_REAL_HOME", "")

_current = {"nodeid": "<pre-collect>"}
_real_paths: set[str] = set()
_orig_connect = sqlite3.connect


def _norm(path) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except Exception:  # noqa: BLE001 - 规范化失败只影响比对，不影响放行
        return str(path)


def _candidate_paths() -> set[str]:
    paths: list[str] = []
    if REAL_HOME:
        paths.append(REAL_HOME)
    else:
        try:  # 没显式给出时回落到 settings（此刻会话尚未重指数据目录）
            from lantai.core.settings import settings

            url = str(settings.DATABASE_URL or "")
            paths.append(url.replace("sqlite:///", ""))
            if settings.LANTAI_HOME:
                paths.append(os.path.join(str(settings.LANTAI_HOME), "remembrance.db"))
        except Exception:  # noqa: BLE001 - 被测项目里没有 lantai 也要能跑
            pass
    return {_norm(p) for p in paths if p}


def _w(line: str) -> None:
    if not REPORT:
        return
    with open(REPORT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pytest_configure(config):
    if not REPORT:
        return
    with open(REPORT, "w", encoding="utf-8"):
        pass  # 截断上一轮报告
    _real_paths.update(_candidate_paths())
    if not _real_paths:
        _w("[TRIPEWIRE-NOT-ARMED] 无真实库路径可盯（env 未给且 settings 不可用）")
        return
    _w("[TRIPEWIRE-ARMED] " + "; ".join(sorted(_real_paths)))

    def traced_connect(database, *args, **kwargs):
        key = _norm(database)
        if key in _real_paths:
            stack = "".join(traceback.format_stack()[:-1][-6:])
            _w(f"[HIT] test={_current['nodeid']} connect({key})")
            _w("  stack:" + stack.replace("\n", "\n         "))
        else:
            _w(f"[miss] test={_current['nodeid']} connect({key})")
        return _orig_connect(database, *args, **kwargs)

    sqlite3.connect = traced_connect


def pytest_runtest_setup(item):
    _current["nodeid"] = item.nodeid


def pytest_sessionfinish(session, exitstatus):
    if REPORT:
        _w(f"[TRIPEWIRE-FINISH] exit={exitstatus}")
