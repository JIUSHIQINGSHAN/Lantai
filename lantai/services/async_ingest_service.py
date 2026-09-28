"""潜移（ADR-0033）：异步零延迟记忆摄取管道服务。

提供：
1. submit_async_dialogue: 毫秒级返回 task_id 并将任务提交至后台线程池；
2. get_task_status: 查询任务状态 (queued/processing/completed/failed) 与提取结果；
3. clear_tasks: 任务注册表清理（供测试使用）。
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lantai.core.logger import logger
from lantai.core.time import utcnow

# 内存任务状态注册表
_TASKS: dict[str, dict[str, Any]] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qianyi_ingest_")


def _execute_dialogue_task(
    task_id: str,
    text: str,
    user_id: str,
    source: str,
    session_id: str = "",
    turn: int | None = None,
) -> None:
    """后台线程执行完整摄取流水线。"""
    try:
        _TASKS[task_id]["status"] = "processing"
        _TASKS[task_id]["started_at"] = utcnow().isoformat()

        # 调用同步核心摄取函数
        from lantai.ingestion.dialogue import ingest_dialogue

        res = ingest_dialogue(
            text=text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            turn=turn,
        )

        _TASKS[task_id]["status"] = "completed"
        _TASKS[task_id]["result"] = res
        _TASKS[task_id]["completed_at"] = utcnow().isoformat()
        logger.info("潜移：异步对话摄取任务【%s】执行完成", task_id)
    except Exception as exc:
        logger.error("潜移：异步对话摄取任务【%s】执行失败: %s", task_id, exc)
        _TASKS[task_id]["status"] = "failed"
        _TASKS[task_id]["error"] = str(exc)
        _TASKS[task_id]["completed_at"] = utcnow().isoformat()


def submit_async_dialogue(
    text: str,
    user_id: str = "default",
    source: str = "dialogue",
    session_id: str = "",
    turn: int | None = None,
) -> dict:
    """提交对话文本进行异步提纯摄取（毫秒级非阻塞返回）。"""
    raw_text = (text or "").strip()
    if not raw_text:
        raise ValueError("text 不能为空")

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task_info = {
        "task_id": task_id,
        "status": "queued",
        "user_id": user_id,
        "source": source,
        "session_id": session_id or None,
        "submitted_at": utcnow().isoformat(),
        "result": None,
        "error": None,
    }
    _TASKS[task_id] = task_info

    # 提交至后台线程池
    _EXECUTOR.submit(_execute_dialogue_task, task_id, raw_text, user_id, source, session_id, turn)
    logger.info("潜移：已分发异步对话摄取任务【%s】", task_id)
    return {"task_id": task_id, "status": "queued"}


def get_task_status(task_id: str, principal=None) -> dict:
    """获取指定任务的执行状态与结果。

    归属校验（票 `.scratch/mcp-identity-gaps/01b`）：`_TASKS` 是进程内
    字典，`result` 里带着**摄取结果的完整 payload**（含提取出的候选正文）。
    此前一个身份都不取，MCP 的 `dialogue_task_status` 于是让 A 拿 B 的
    task_id 读到 B 的提取结果。task_id 形如 `task_<12 hex>`，虽然不可枚举，
    但会出现在对话日志、错误信息、以及 `dialogue_add_async` 的返回值里。

    不匹配一律回 `not_found`，**不区分「没有」与「不是你的」**——区分本身
    就是信息泄漏：能据此探知某 task_id 是否存在（同 `get_scratchpad`
    票 ownership-gaps/04 口径）。`principal=None`（内部 worker / 脚本）
    不校验，与改动前逐字一致。
    """
    tid = (task_id or "").strip()
    if tid not in _TASKS:
        return {"task_id": tid, "status": "not_found", "error": "任务不存在"}
    task = _TASKS[tid]
    if principal is not None:
        from lantai.core.acl import viewer_of

        owner = task.get("user_id")
        # 老任务（v0.22 之前入队）没有 user_id 字段：按「无归属老行」放行，
        # 同 ensure_can_delete 对 resource_user_id 为空的口径——判不可见会
        # 让升级前入队的任务永远查不到状态。
        if owner and owner != viewer_of(principal):
            return {"task_id": tid, "status": "not_found", "error": "任务不存在"}
    return task


def clear_tasks() -> None:
    """清理内存任务字典（测试使用）。"""
    _TASKS.clear()
