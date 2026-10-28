"""MemProbe 评测服务器启动器（票 03）。

与 lme_run._start_server 同纪律：独立 LANTAI_HOME、断言 ≠ 生产路径、最小 env。
为什么不用 `python -m uvicorn lantai.api.app:app` 直启：lantai/llm/client.py 的
_timed 包装在**每次** chat/embed 外呼后经 record_llm_outcome 另开 session 写
llm_call_event 遥测；当外呼发生在未提交的写事务内（/import/jsonl 逐行 embed、
/dialogue 提取）时，遥测写被外层事务的 RESERVED 写锁阻塞到 sqlite busy_timeout
（db.py connect_args timeout=30）后抛 OperationalError 被吞——实测每行 +30s
（imp-probe-home4 探针：embed 步 33s、SDK HTTP 调用仅 0.16–0.31s、
llm_call_event 0 行；LongMemEval 灌库 30s/行 同根）。评测库是一次性库，该遥测
本来 100% 写不进（0 行），这里在**评测服务器进程内**把 record_llm_outcome 置
no-op：灌库语义不变、检索语义不变、落库数据不变，只去掉 30s/行的纯等死锁。
产品代码零改动；此补丁在预注册与报告留痕（2026-10-28，票 03）。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 脚本方式启动时 sys.path[0]=scripts/，lantai 包在仓库根——不插入则
# uvicorn.run 导入 lantai.api.app 失败（2026-10-28 seg0 首跑实测）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROD_LANTAI_HOME = r"C:\Users\Asus\AppData\Local\remembrance-data"  # .env 实配生产库


def _assert_double_gate() -> None:
    home = os.environ.get("LANTAI_HOME", "")
    if not home:
        raise SystemExit("LANTAI_HOME 未设置，拒启")
    prod = str(Path(PROD_LANTAI_HOME).resolve())
    ev = str(Path(home).resolve())
    if ev == prod or prod in ev or ev in prod:
        raise SystemExit(f"双闸失败：评测目录 {ev} 与生产路径 {prod} 重叠，拒启")
    print(f"[双闸通过] 评测库: {ev}（≠ 生产 {prod}）", flush=True)


def _noop_telemetry() -> None:
    import lantai.llm.client as _client

    _client.record_llm_outcome = lambda *a, **k: None
    print(
        "[遥测] 评测服务器进程内 record_llm_outcome → no-op（30s/外呼死锁规避，见文件头）",
        flush=True,
    )


if __name__ == "__main__":
    _assert_double_gate()
    _noop_telemetry()
    import uvicorn

    uvicorn.run(
        "lantai.api.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8793")),
        log_level="warning",
    )
