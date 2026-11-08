"""勘合——潜移异步摄取幂等回执（票据 .scratch/kanhe-idempotency/01）。

上游 f0.3（aiduMEM `ducky/idempotency.py`）同款移植：宿主网络重试会让
`submit_async_dialogue` 每次都新造 task_id 重复提交，同一段话提纯入库两遍。

与上游的三处主要分歧（全部有据）：
1. claim 即回执——抢占与回执是同一行：提交时 INSERT（new），worker 结算时
   按 token 令牌寻行（v39 起令牌与时间解耦，票 74），防过期接管后迟到结算
   覆盖新主。上游 P1-10 的 SELECT→INSERT 两步竞态在一条原子 INSERT 语义下
   不存在。
2. 两种键两段语义——显式键（宿主传 idempotency_key）done 回执保留 TTL 天；
   自动指纹键（未传键，key=fp:<sha256>）只护 LEASE_SECONDS（600s）重试窗，
   settle 成功不落 done（行保持 accepted 到过期）。理由：MCP
   dialogue_add_async 不透传 session_id/turn，自动指纹长期保留会把「同一段
   话隔天再说」误判成重试——重试发生在秒-分钟尺度，长期去重是显式键的职责。
3. 时间一律 epoch 浮点秒——租约/TTL 是浮点运算，避开 sqlmodel aware/naive
   datetime 跨版本读回漂移的已知雷。所有权令牌不沿用时间：粗刻度时钟下
   相邻两次取时可同值（票 74 实测 time.time() 紧循环 10 万次仅 5 个唯一值），
   claim/接管另发 uuid4 token，created_at 只承担租约/TTL 运算。

不变量：同一 (key,user,tenant)+同指纹在租约窗内至多执行一次；
任务失败后同键必须可重试；回执表里永不出现正文（白名单回执）。
fail-open：幂等层 DB 异常记 error 后按无幂等放行——去重层故障不挡写入主路。
"""

import hashlib
import json
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import IdempotencyKey

# 回执白名单：ingest 返回值里只挑这几个标量落库。白名单制而非黑名单制——
# 黑名单枚举不完（上游 _REDACT_KEYS 就是黑名单，靠外审一轮轮补），白名单
# 漏一个键只是回执少个字段，不会泄漏正文。
_RECEIPT_WHITELIST = (
    "ingested",
    "candidate_id",
    "fastpath",
    "lane",
    "status",
    "task_id",
    "durable",
)

# fail-open 边界：DB 层故障按无幂等处理放行；其余异常（编程错误）如实上抛。
_DB_ERRORS = (SQLAlchemyError, OSError)

LEASE_SECONDS = 600.0  # accepted 租约：重试发生在秒-分钟尺度，600s 足够
TTL_SECONDS = float(settings.IDEMPOTENCY_TTL_DAYS) * 86400.0  # done 回执寿命


def fingerprint_payload(
    text: str, user_id: str, source: str, session_id: str, turn: int | None
) -> str:
    """内容指纹（自动键的 key 与显式键的 conflict 判定共用）。"""
    payload = json.dumps(
        {
            "text": text,
            "user_id": user_id,
            "source": source,
            "session_id": session_id,
            "turn": turn,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def auto_key(fingerprint: str) -> str:
    """未传显式键时的自动键：内容指纹前缀 fp:。"""
    return f"fp:{fingerprint}"


def _receipt_json(result: dict | None) -> str | None:
    """ingest 返回值 → 白名单标量回执 JSON（零正文）。"""
    if not isinstance(result, dict):
        return None
    receipt = {k: result[k] for k in _RECEIPT_WHITELIST if k in result}
    return json.dumps(receipt, ensure_ascii=False, default=str) if receipt else None


def claim(key: str, user_id: str, tenant_id: str, fingerprint: str, task_id: str = "") -> dict:
    """抢占写入键。

    task_id 由调用方在抢占**前**生成（纯 uuid，无副作用）并随抢占一并落库
    ——回执从出生就有任务号，宿主重试在租约窗内拿到的就是原任务号去轮询，
    不存在「抢占成功到回执落库之间」的窗口（上游靠 claimed_at 令牌 + 两段
    settle 补的洞，兰台一条 INSERT 不开这个洞）。

    返回动作指令：
    - {"action": "new", "claimed_at": <float>, "token": <str>}：抢到，可提交
      执行；token 是所有权令牌（uuid4，票 74），binding 必须随行携带，迟到
      的 release/settle 以它寻行；
    - {"action": "replay", "task_id": ...}：同键同文且回执有效，重放原任务号；
    - {"action": "pending", "retry_after": <sec>}：同键同文但任务在跑/键刚被
      接管，让客户端稍后用原 task_id 查询；
    - {"action": "disabled"}：幂等层 DB 故障，fail-open（调用方照常提交）。
    同键不同文且行未过期：raise ValueError（调用方映射 422，冲突不 fail-open）。
    """
    normalized = str(key or "").strip()
    if not normalized:
        return {"action": "new", "claimed_at": None, "token": None}

    now = utcnow().timestamp()
    token = uuid.uuid4().hex
    receipt_json = _receipt_json({"task_id": task_id}) if task_id else None
    from lantai.storage import db

    try:
        with db.get_session() as s:
            row = IdempotencyKey(
                key=normalized,
                user_id=user_id,
                tenant_id=tenant_id or "",
                fingerprint=fingerprint,
                response_json=receipt_json,
                state="accepted",
                created_at=now,
                token=token,
            )
            s.add(row)
            try:
                s.commit()
                return {"action": "new", "claimed_at": now, "token": token}
            except SQLAlchemyError as exc:
                s.rollback()
                if "unique" not in str(exc).lower() and "primary" not in str(exc).lower():
                    raise
                # 作用域值先算好再进 where——`col == x or ""` 会因优先级把
                # 裸字符串传进 where（ArgumentError，且被 fail-open 吞成 disabled）
                tenant_scope = tenant_id or ""
                existing = s.exec(
                    select(IdempotencyKey).where(
                        IdempotencyKey.key == normalized,
                        IdempotencyKey.user_id == user_id,
                        IdempotencyKey.tenant_id == tenant_scope,
                    )
                ).first()
                if existing is None:
                    # 抢占失败却读不到行：对手刚 release。让客户端重试。
                    return {"action": "pending", "retry_after": 1.0}
                return _judge_existing(existing, fingerprint, now, task_id)
    except _DB_ERRORS as exc:
        logger.error("勘合：幂等层不可用，本次请求按无幂等处理（可能重复落库）：%s", exc)
        return {"action": "disabled"}


def _judge_existing(row: IdempotencyKey, fingerprint: str, now: float, task_id: str = "") -> dict:
    """对已存在的同键行判定 replay/pending/conflict/过期接管。"""
    if row.fingerprint != fingerprint:
        # 同键不同文：未过期即冲突；过期则条件接管（显式键换内容重用是合法态）
        if now - row.created_at < TTL_SECONDS:
            raise ValueError("idempotency key conflict: same key, different content")
        return _take_over(row, fingerprint, now, task_id)
    if row.state == "done":
        # done 回执 TTL 内有效；回执里的 task_id 是重放答案
        if now - row.created_at < TTL_SECONDS:
            old_task_id = _receipt_task_id(row.response_json)
            if old_task_id:
                return {"action": "replay", "task_id": old_task_id}
        return _take_over(row, fingerprint, now, task_id)
    # accepted（provisional）：租约窗内 = 任务在跑或刚提交，重放原任务号让
    # 客户端轮询；租约过期 = 原任务丢失（重启/驱逐），条件接管重新执行——
    # 不能永远 pending 把客户端锁死。
    if now - row.created_at < LEASE_SECONDS:
        old_task_id = _receipt_task_id(row.response_json)
        if old_task_id:
            return {"action": "replay", "task_id": old_task_id}
        return {
            "action": "pending",
            "retry_after": max(1.0, LEASE_SECONDS - (now - row.created_at)),
        }
    return _take_over(row, fingerprint, now, task_id)


def _take_over(row: IdempotencyKey, fingerprint: str, now: float, task_id: str = "") -> dict:
    """过期行的条件接管：令牌仍等于读到的旧值才算抢到（并发双发现只一个赢）。

    判据是 token 列（v39 起，票 74）：接管成功即改写 token，旧主迟到的
    release/settle 按旧 token 寻行必落空——粗刻度时钟下相邻两次取时可同值，
    令牌与时间解耦后同刻度不再构成碰撞。升级窗存量老行 token 为 NULL，
    沿用 created_at 相等判据并限定 token IS NULL：新世界出生的行必有 token，
    不会被旧时间戳误命中。

    与 claim 同理，接管即回执：新任务号随接管一并落库，重试方仍拿得到
    轮询目标（回执从出生有任务号的不变量在接管行上同样成立）。
    """
    from lantai.storage import db

    new_token = uuid.uuid4().hex
    try:
        with db.get_session() as s:
            cond = (
                IdempotencyKey.token == row.token
                if row.token
                else and_(
                    IdempotencyKey.token.is_(None),
                    IdempotencyKey.created_at == row.created_at,
                )
            )
            target = s.exec(
                select(IdempotencyKey).where(
                    IdempotencyKey.key == row.key,
                    IdempotencyKey.user_id == row.user_id,
                    IdempotencyKey.tenant_id == row.tenant_id,
                    cond,
                )
            ).first()
            if target is None:
                return {"action": "pending", "retry_after": 1.0}
            target.fingerprint = fingerprint
            target.response_json = _receipt_json({"task_id": task_id}) if task_id else None
            target.state = "accepted"
            target.created_at = now
            target.token = new_token
            s.add(target)
            s.commit()
            return {"action": "new", "claimed_at": now, "token": new_token}
    except _DB_ERRORS as exc:
        logger.error("勘合：过期接管失败，按无幂等处理：%s", exc)
        return {"action": "disabled"}


def _receipt_task_id(response_json: str | None) -> str | None:
    if not response_json:
        return None
    try:
        receipt = json.loads(response_json)
        tid = receipt.get("task_id")
        return str(tid) if tid else None
    except (TypeError, ValueError):
        return None


def settle(binding: dict | None, *, ok: bool, result: dict | None = None, task_id: str = "") -> str:
    """worker 结算 claim（binding 由 submit 在 claim==new 时构造）。

    ok=True：显式键落 done 回执（白名单标量）；自动指纹键不落 done——行
    保持 accepted 到租约过期自然失效。ok=False：删除自己令牌的行，重试可
    重新执行（失败的任务永远不得以 accepted 重放，上游 S-2/S-8 教训）。
    只结清 token 令牌匹配的那条 claim（票 74）——行过期被接管后令牌已换
    新，迟到的结算找不到行即返回 "gone"，不覆盖新主；升级窗旧 binding
    （只有 claimed_at）走「created_at 相等且 token IS NULL」旧判据，同样
    不会命中已接管的行。
    返回 "finalized" | "released" | "gone" | "skipped" | "error" | "n/a"。
    """
    if not binding or not binding.get("key"):
        return "skipped"
    key = binding["key"]
    user_id = binding.get("user_id") or ""
    tenant_id = binding.get("tenant_id") or ""
    token = binding.get("token")
    claimed_at = binding.get("claimed_at")
    from lantai.storage import db

    try:
        with db.get_session() as s:
            if token:
                cond = IdempotencyKey.token == token
            else:
                # 旧 binding 无 token（升级窗/手工构造）：旧判据并限定 token
                # IS NULL——新世界出生的行必有 token，不会被旧时间戳误命中
                cond = and_(
                    IdempotencyKey.token.is_(None),
                    IdempotencyKey.created_at == claimed_at,
                )
            target = s.exec(
                select(IdempotencyKey).where(
                    IdempotencyKey.key == key,
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.tenant_id == tenant_id,
                    cond,
                )
            ).first()
            if target is None:
                return "gone"
            if not ok:
                s.delete(target)
                s.commit()
                return "released"
            if binding.get("explicit") is True:
                receipt = dict(result or {})
                receipt["task_id"] = task_id or str(receipt.get("task_id") or "")
                receipt["durable"] = True
                target.response_json = _receipt_json(receipt)
                target.state = "done"
                s.add(target)
                s.commit()
                return "finalized"
            return "n/a"  # 自动指纹键：成功即走，行到期自然失效
    except _DB_ERRORS as exc:
        logger.error("勘合：settle 失败（key=%s ok=%s）：%s", key, ok, exc)
        return "error"


def release(
    key: str,
    user_id: str,
    tenant_id: str,
    *,
    claimed_at: float | None = None,
) -> None:
    """删除 claim 使重试可执行（settle(ok=False) 的底层；独立暴露给运维面）。

    带 claimed_at 时只删自己那条 claim（v39 起限定 token IS NULL 的升级窗
    旧判据——粗刻度时钟下接管可把新主行 created_at 改写回与旧令牌相等，
    裸浮点相等判据会误删新主行，票 74；新世界 claim 的定向释放走
    settle(ok=False)，binding 携带 token）；无令牌时清同键全部未完成 claim
    （运维面强释放）。**done 回执在任何形态下都不删**（已完成的结果不能被
    运维面顺手抹掉重放）——此前 `state != "done"` 守卫只在带令牌分支内，
    无令牌调用把 done 回执一并删净、重试从 replay 退化成 re-execution
    （票 75，探针 `.scratch/_v068_probe_r9_release_nukes_done.py` 实锤）。
    """
    from lantai.storage import db

    try:
        with db.get_session() as s:
            stmt = select(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.tenant_id == tenant_id,
                IdempotencyKey.state != "done",
            )
            if claimed_at is not None:
                stmt = stmt.where(
                    and_(
                        IdempotencyKey.token.is_(None),
                        IdempotencyKey.created_at == claimed_at,
                    )
                )
            for target in s.exec(stmt).all():
                s.delete(target)
            s.commit()
    except _DB_ERRORS as exc:
        logger.warning("勘合：release 失败（key=%s）：%s", key, exc)


def purge_expired(now: float | None = None) -> int:
    """TTL 清扫：删掉 done 回执超 TTL、accepted 超 LEASE 的行。返回删除数。

    accepted 超 LEASE 本可留给接管逻辑，但那种行已无任务在跑（重启丢了
    _TASKS），留着只会让同文重试走到接管分支多绕一步——直接清了等价且省行。
    过滤下推 SQL（票 75）：此前全量加载「超 LEASE」的行（含 TTL 内 done
    回执）进内存再 Python 过滤，行数随时面线性增长；现 `(state != done OR
    age >= TTL)` 与超龄条件一并下推，done 未超 TTL 的行不再出库。
    """
    now = utcnow().timestamp() if now is None else now
    from lantai.storage import db

    try:
        with db.get_session() as s:
            stale = s.exec(
                select(IdempotencyKey).where(
                    IdempotencyKey.created_at < now - LEASE_SECONDS,
                    or_(
                        IdempotencyKey.state != "done",
                        IdempotencyKey.created_at < now - TTL_SECONDS,
                    ),
                )
            ).all()
            for row in stale:
                s.delete(row)
            s.commit()
            return len(stale)
    except _DB_ERRORS as exc:
        logger.error("勘合：purge 失败：%s", exc)
        return 0
