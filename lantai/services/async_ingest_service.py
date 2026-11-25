"""潜移（ADR-0033）：异步零延迟记忆摄取管道服务。

提供：
1. submit_async_dialogue: 毫秒级返回 task_id 并将任务提交至后台线程池；
2. get_task_status: 查询任务状态 (queued/processing/completed/failed) 与提取结果；
3. clear_tasks: 任务注册表清理（供测试使用）。

勘合（票据 .scratch/kanhe-idempotency/01）：submit 经幂等键抢占——宿主网络
重试不再重复入库。显式键（idempotency_key 参数）done 回执 TTL 内重放原
task_id、同键不同文 422；未传键用内容指纹自动键，只护 600s 重试窗。
"""

import contextlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.exc import OperationalError

from lantai.core.attribution import Attribution, attribution_dict, resolve_attribution
from lantai.core.idempotency import auto_key, claim, fingerprint_payload, settle
from lantai.core.logger import logger
from lantai.core.time import iso_utc, parse_iso_utc, utcnow
from lantai.observability.ingest_events import record_ingest_exception, start_ingest_event

# 内存任务状态注册表
_TASKS: dict[str, dict[str, Any]] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qianyi_ingest_")

# 注册表保留策略（巡检 2026-11-24：此前 `_TASKS` 只增不减——每轮对话一条，
# 常驻服务跑几天就是几万条 dict，且 `result` 里带着候选正文；线程池队列也没有
# 上限。终态任务按 TTL 清扫，超上限时从最旧的终态任务开始淘汰；**在跑/排队中
# 的任务绝不淘汰**（淘汰会让轮询拿到 not_found，也够不着迟到结算）。
_TASK_RETENTION_SECONDS = 3600.0  # 终态任务保留 1 小时，覆盖宿主重试/轮询窗口
_TASKS_MAX = 1000  # 注册表硬顶（终态可淘汰；活跃任务超出时宁可暂不淘汰）


def _task_finished_at(task: dict) -> float:
    """终态任务完成时刻（epoch 秒）；解析不了按 0（最旧）处理。"""
    raw = task.get("completed_at")
    if not raw:
        return 0.0
    try:
        return parse_iso_utc(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _sweep_tasks(now: float | None = None) -> int:
    """清理注册表：TTL 外的终态任务 + 超上限时淘汰最旧终态。返回删除条数。

    只淘汰 ``completed``/``failed``：``queued``/``processing`` 的任务还等着宿主
    轮询与迟到结算，淘汰它们等于把在跑的任务变成 not_found。
    """
    now = utcnow().timestamp() if now is None else now
    removed = 0
    for task_id, task in list(_TASKS.items()):
        if task.get("status") in ("completed", "failed"):
            if now - _task_finished_at(task) > _TASK_RETENTION_SECONDS:
                _TASKS.pop(task_id, None)
                removed += 1
    overflow = len(_TASKS) - _TASKS_MAX
    if overflow > 0:
        finished = sorted(
            (
                (task_id, task)
                for task_id, task in _TASKS.items()
                if task.get("status") in ("completed", "failed")
            ),
            key=lambda item: _task_finished_at(item[1]),
        )
        for task_id, _task in finished[:overflow]:
            _TASKS.pop(task_id, None)
            removed += 1
    return removed


def _start_ingest_event_selfheal(**kwargs) -> str:
    """建摄取事件行；缺表时补跑一次 init_db 再重试（与对话摄取同源自愈）。

    此前 HTTP 异步通道没有这层：真实库滞后于代码 schema 时，``/dialogue/async``
    在提交阶段就 500，连 task_id 都拿不到。非缺表错误不重试，原样上抛。
    """
    from lantai.storage.schema_guard import heal_missing_schema, is_missing_table_error

    try:
        return start_ingest_event(**kwargs)
    except OperationalError as exc:
        if not is_missing_table_error(exc):
            raise
        logger.warning("潜移：提交阶段命中缺失表，补跑 init_db 后重试一次: %s", exc)
        with contextlib.suppress(Exception):
            heal_missing_schema()
        return start_ingest_event(**kwargs)


def _execute_dialogue_task(
    task_id: str,
    text: str,
    user_id: str,
    source: str,
    session_id: str = "",
    turn: int | None = None,
    binding: dict | None = None,
    *,
    attribution: dict | Attribution | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    federation_id: str | None = None,
    ingest_event_id: str,
) -> None:
    """后台线程执行完整摄取流水线。"""
    # 注册表可能已被清扫（或测试 clear_tasks）：任务号没登记就无处写状态。
    # 不吞掉这次执行的话，worker 会在 ``_TASKS[task_id]`` 上抛 KeyError、
    # 在线程里静默死掉且不结算 claim；显式释放 claim 让重试可再执行。
    task = _TASKS.get(task_id)
    if task is None:
        logger.warning("潜移：任务【%s】不在注册表（已被清扫），本次不执行", task_id)
        # 没执行过任何业务写入，释放 claim 才是对的：ok=True 会给显式键落一条
        # 没有结果的 done 回执，让宿主从此重放到一个空任务上。
        settle(binding, ok=False)
        return
    try:
        task["status"] = "processing"
        task["started_at"] = iso_utc(utcnow())

        # 调用同步核心摄取函数
        from lantai.ingestion.dialogue import ingest_dialogue

        res = ingest_dialogue(
            text=text,
            user_id=user_id,
            source=source,
            session_id=session_id,
            turn=turn,
            attribution=attribution,
            tenant_id=tenant_id,
            agent_id=agent_id,
            federation_id=federation_id,
            _ingest_event_id=ingest_event_id,
        )

        task["status"] = "completed"
        task["result"] = res
        task["completed_at"] = iso_utc(utcnow())
        logger.info("潜移：异步对话摄取任务【%s】执行完成", task_id)
        # 勘合结算：显式键落 done 回执（TTL 内重放），自动键不落（到期自然失效）
        settle(binding, ok=True, result=res, task_id=task_id)
    except Exception as exc:
        logger.error("潜移：异步对话摄取任务【%s】执行失败: %s", task_id, type(exc).__name__)
        task["status"] = "failed"
        task["error"] = type(exc).__name__
        task["completed_at"] = iso_utc(utcnow())
        # Preserve a committed candidate on a post-commit exception; releasing
        # its idempotency claim would duplicate the already received dialogue.
        committed = bool(task.get("result"))
        try:
            committed = (
                record_ingest_exception([ingest_event_id], rejected=isinstance(exc, ValueError))
                or committed
            )
        finally:
            settle(binding, ok=committed, result=task.get("result"), task_id=task_id)


def submit_async_dialogue(
    text: str,
    user_id: str = "default",
    source: str = "dialogue",
    session_id: str = "",
    turn: int | None = None,
    idempotency_key: str = "",
    tenant_id: str = "",
    agent_id: str | None = None,
    federation_id: str | None = None,
    attribution: dict | Attribution | None = None,
) -> dict:
    """提交对话文本进行异步提纯摄取（毫秒级非阻塞返回）。

    勘合幂等（票据 .scratch/kanhe-idempotency/01）：传 idempotency_key 时
    同键同文在回执 TTL 内重放原 task_id（响应多一个 replayed: true 标记），
    同键不同文 422；不传时以内容指纹为自动键，只护 LEASE_SECONDS 重试窗。
    """
    # 票 43：通道开关在提交时 fail-fast——被拒请求不建任务、不占勘合键，
    # 免得宿主拿着 task_id 轮询出一条 failed 假任务
    from lantai.ingestion.dialogue import assert_dialogue_enabled

    assert_dialogue_enabled()
    raw_text = (text or "").strip()
    if not raw_text:
        raise ValueError("text 不能为空")

    envelope = attribution_dict(attribution)
    context = resolve_attribution(session_id, envelope)
    effective_session_id = context["session_id"] or ""
    fingerprint = fingerprint_payload(raw_text, user_id, source, effective_session_id, turn)
    explicit = bool((idempotency_key or "").strip())
    key = (idempotency_key or "").strip() or auto_key(fingerprint)
    # task_id 先于抢占生成（纯 uuid 无副作用）：抢到即随回执落库，重试方
    # 在租约窗内拿到的就是原任务号；没抢到则这个号从未注册，直接丢弃。
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    verdict = claim(key, user_id, tenant_id, fingerprint, task_id=task_id)
    if verdict["action"] == "disabled":
        # fail-open：幂等层故障不挡写入主路（勘合层已记 error 日志）
        binding = None
    elif verdict["action"] == "replay":
        original = verdict.get("task_id") or ""
        if original:
            return {
                "task_id": original,
                "status": "queued",
                "replayed": True,
                "ingest_event_id": (_TASKS.get(original) or {}).get("ingest_event_id"),
            }
        # 回执无任务号（理论不可达，防御）：按待处理让宿主稍后重试
        return {"task_id": "", "status": "pending", "replayed": True}
    elif verdict["action"] == "pending":
        return {
            "task_id": "",
            "status": "pending",
            "error": "同键请求正在处理，请稍后用原 task_id 查询",
        }
    else:
        binding = {
            "key": key,
            "user_id": user_id,
            "tenant_id": tenant_id or "",
            "claimed_at": verdict.get("claimed_at"),
            "token": verdict.get("token"),
            "explicit": explicit,
        }

    try:
        ingest_event_id = _start_ingest_event_selfheal(
            channel="dialogue",
            user_id=user_id,
            tenant_id=tenant_id or None,
            agent_id=agent_id,
            session_id=session_id,
            attribution=envelope,
        )
    except Exception:
        # Nothing was submitted; this owner must release its claim for retry.
        settle(binding, ok=False)
        raise
    # 注册前清扫：终态旧任务按 TTL/硬顶淘汰，注册表不随对话轮次无界增长。
    _sweep_tasks()
    task_info = {
        "task_id": task_id,
        "ingest_event_id": ingest_event_id,
        "status": "queued",
        "user_id": user_id,
        "source": source,
        "session_id": effective_session_id or None,
        "request_session_id": context.get("request_session_id"),
        "request_id": context.get("request_id"),
        "submitted_at": iso_utc(utcnow()),
        "result": None,
        "error": None,
    }
    _TASKS[task_id] = task_info

    # 提交至后台线程池
    try:
        _EXECUTOR.submit(
            _execute_dialogue_task,
            task_id,
            raw_text,
            user_id,
            source,
            session_id,
            turn,
            binding,
            attribution=envelope,
            tenant_id=tenant_id or None,
            agent_id=agent_id,
            federation_id=federation_id,
            ingest_event_id=ingest_event_id,
        )
    except Exception:
        task_info["status"] = "failed"
        task_info["error"] = "dispatch_error"
        try:
            record_ingest_exception([ingest_event_id], reason_code="dispatch_error")
        finally:
            settle(binding, ok=False)
        raise
    logger.info("潜移：已分发异步对话摄取任务【%s】", task_id)
    return {"task_id": task_id, "status": "queued", "ingest_event_id": ingest_event_id}


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
