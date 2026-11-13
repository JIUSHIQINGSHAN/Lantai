"""
检索事件日志（方向二弱标注源）——只追加，失败不影响主链路。

无标注评估集的现实替代：记录"哪条记忆被召回 + 当时生效参数 + 延迟"，
后续 dry-run/shadow 用这些事件做相对指标（zero_result / jaccard / 弱命中率）。
"""

from sqlalchemy import func
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import loggable, logger
from lantai.core.text import query_norm_hash
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, RetrievalEvent
from lantai.parameters.registry import default_snapshot
from lantai.parameters.validation import snapshot_hash
from lantai.storage import db
from lantai.storage.cas import atomic_update
from lantai.storage.fts import bm25_terms_truncated


def _norm_hash(query: str) -> str:
    # 票 50 自审收敛：归一口径上移 core.text.query_norm_hash 单真源
    # （mokan 审计日志 query_fp 同源，观测口径不参与召回）
    return query_norm_hash(query)


# 系统注入噪音的确定性前缀（Hermes 技能库维护 / 记忆保存 / Skill 安装模板）。
# 这类查询不是用户的真实记忆回忆，混入评估集会稀释 dry-run 指标。
_SYSTEM_NOISE_PREFIXES = (
    "review the conversation above",
    "consider saving to memory",
    "请帮我安装这个 agent skill",
)
# 长度阈值：实测存量数据 201-500 字符区间为 0 条——真实回忆查询几乎都 ≤200，
# >500 的基本是超长系统注入指令（技能库维护 prompt 达 5-7k 字符）。天然鸿沟，可安全判定。
_SYSTEM_NOISE_MAX_LEN = 500


def is_system_noise(query: str) -> bool:
    """判定一次检索查询是否为系统注入噪音（非用户真实记忆回忆）。

    纯函数、无副作用；宁 miss 不误标——只认确定性前缀 + 长度鸿沟，
    不依赖关键词匹配，避免把用户的真实长查询误判为噪音。
    """
    q = (query or "").strip()
    if not q:
        return False
    lower = q.lower()
    if any(lower.startswith(p) for p in _SYSTEM_NOISE_PREFIXES):
        return True
    return len(q) > _SYSTEM_NOISE_MAX_LEN


def log_retrieval(
    query: str,
    results: list[dict],
    *,
    latency_ms: int,
    gate: dict | None = None,
    trace_id: str | None = None,
    lanes: list[str] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    principal=None,
    persist_query: bool = True,
) -> str | None:
    """
    在检索出口记录一次事件。失败仅记日志，绝不抛给主链路。
    返回事件 id（供后续回填 used_ids）；失败返回 None。

    session_id：来源链（v022 票据 05）——带 session 的检索才算「真实会话
    读」，写活性判据此计数；不透传则留空（如实 NULL，不猜测）。

    principal：归属（票 .scratch/readside-gaps/10）——`query_text` 是用户
    问过什么，比记忆正文更直接暴露意图。落 `principal.user_id` 后读侧
    才能按归属收窄；不透传则留 NULL（后台巡检/内部调用）。
    request_id：回执链（ADR-0049）——一次注入调用的整体标识，由注入侧
    （shell_hook context / 中间件）生成透传；不透传留空（pending 状态照记）。
    persist_query：原话是否落库（票 11，ADR-0084 §2.5 增补）——gate 拦截轮
    传 False：该轮从未进入检索、无 eval/弱标注价值，原话留存是纯负担，写入
    时即空 `query_text`/`query_norm_hash`（哈希对低熵原文是可验证口令，与
    `mark_vetoed` 同源论证）。`is_system_noise`/`estimated_tokens` 等标记
    **以原查询在抹除前照算**——调用方若图省事直接传空串 query，这两个标记
    会静默失真（实现陷阱有回归锁）。
    """
    try:
        event_id = new_id("rev")
        result_ids = [r["memory"]["id"] for r in results if isinstance(r, dict) and "memory" in r]
        result_scores = [r["score"] for r in results if isinstance(r, dict) and "score" in r]
        intent = (gate or {}).get("intent") if isinstance(gate, dict) else None
        from lantai.observability.recall_report import (
            _scenes_from_results,
            _tokens_from_results,
            estimate_tokens,
        )

        with db.get_session() as s:
            s.add(
                RetrievalEvent(
                    id=event_id,
                    user_id=getattr(principal, "user_id", None),
                    tenant_id=getattr(principal, "tenant_id", None),
                    trace_id=trace_id or "",
                    query_text=query if persist_query else "",
                    query_norm_hash=_norm_hash(query) if persist_query else "",
                    lane=",".join(lanes) if lanes else "",
                    session_id=(session_id or "").strip() or None,
                    request_id=(request_id or "").strip() or None,
                    receipt_status="pending",
                    intent_bucket=intent if isinstance(intent, str) else None,
                    param_snapshot_hash=snapshot_hash(default_snapshot()),
                    result_ids=result_ids,
                    result_scores=result_scores,
                    used_ids=[],
                    latency_ms=int(latency_ms),
                    zero_result=not result_ids,
                    is_system_noise=is_system_noise(query),
                    # 票 49：截断事实是查询的确定函数——原查询在抹除前照算
                    # （同 is_system_noise/estimated_tokens 既有范式），长查询
                    # 尾部 gram 静默丢失自此在事件面可见
                    fts_terms_truncated=bm25_terms_truncated(query),
                    scene_ids=_scenes_from_results(results),
                    estimated_tokens=(estimate_tokens(query) + _tokens_from_results(results)),
                )
            )
            s.commit()
        return event_id
    except Exception:
        logger.exception("retrieval event log failed (non-fatal)")
        return None


def _backfill_owner_ok(ev, principal) -> bool:
    """回执写入口的归属判据（票 `.scratch/mcp-identity-gaps/01c`）。

    口径与读侧收敛**逐字一致**：`user_id == viewer OR IS NULL`。NULL 属主
    是 v022 之前的历史行（真实库 918 条检索事件里只有 68 条带 session_id），
    若把 NULL 当"他人资源"挡掉，单人部署的回执链整体失效——那是把老行
    全判越权，属"宁 miss 不脏写"的反面（把该写的写没了）。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return True
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    return ev.user_id is None or ev.user_id == viewer


def _touch_keepalive(mem_ids: list[str], *, principal=None, event_id: str = "") -> int:
    """注入回执的保活回写：刷新 `last_used_at`、累加 `injection_count`（票 03 P0-4 病灶 2）。

    注入次数与显式反馈分账（巡检 2026-11-03 P0，改法①）：v0.66 最初把回执记进
    `use_count`，可 Hermes 每轮注入都回执、却从不发「有用」反馈——考功
    （`use_count >= 3` 且采纳率 <= 0.2 → 重要度 0.1）与三省（`use_count >= 5` 且
    helpful=0）于是把天天在用的记忆判成「高频低效」。现在回执只写
    `injection_count`，`use_count` 保持「显式反馈次数」原义；遗忘侧用
    `forgetting._usage_signal`（两者之和），保活效果与 v0.66 等价。

    病灶：Hermes 插件注入记忆后，经 `POST /retrieval/backfill` / MCP / shell_hook
    注入回执通道把「真正被用进回答」的 id 回写给检索事件，但 `MemoryItem` 上的
    `use_count` / `last_used_at` 从未被刷新——衰减公式的输入只有 `last_used_at`，
    于是天天被注入使用的记忆在它眼里仍是「65 天没人用过」，被 `apply_forgetting`
    归档。保活因此必须落在这条回写链上（而不是给记忆加永久免疫标记）。

    边界（都是本票的硬约束，不是顺手加的）：
      · 只动**本次事件召回过**（`RetrievalEvent.result_ids`）且**首次被回执**的 id：
        夹带外来 id 刷不到别人的计数，重放同一回执也不会把 `injection_count` 累加；
      · 只动 `status == "active"` 的行——保活不是复活（archived 没有 undo 入口）；
      · 带身份时逐条过 `acl.ensure_can_delete`：这两个字段是遗忘的**输入**，
        非属主不得代刷（同 `evolution/reflector.record_usage` 口径，宁 miss 不脏写）；
      · 不碰 `use_count` / `helpful_count`（显式反馈，走 reflector），不碰 `importance` /
        `decay_score`（改它们等于间接操控演化结果）。
    失败非致命：回执已经先落库，回写炸了不许把 acked 拖回 pending。
    """
    if not mem_ids:
        return 0
    touched = 0
    try:
        now = utcnow()
        with db.get_session() as s:
            for mid in mem_ids:
                item = s.get(MemoryItem, mid)
                if not item or item.status != "active":
                    continue
                if principal is not None:
                    from lantai.core.acl import ensure_can_delete

                    try:
                        ensure_can_delete(
                            principal,
                            resource_user_id=item.user_id,
                            resource_tenant_id=item.tenant_id,
                            lane=item.lane,
                        )
                    except Exception:
                        logger.warning(
                            "receipt keepalive skipped (not owner): mem=%s ev=%s",
                            mid,
                            loggable(event_id, limit=128),
                        )
                        continue
                # 票 23：注入计数原子 UPDATE（coalesce+1 对当前已提交值求值，
                # 两事件并发回执同一条记忆各 +1 都落库，红测 F2a）+ status
                # 写时重验（保活不是复活）。sync=False 下 ORM 属性不参与写。
                touched += atomic_update(
                    s,
                    MemoryItem,
                    mid,
                    {
                        "injection_count": func.coalesce(MemoryItem.injection_count, 0) + 1,
                        "last_used_at": now,
                    },
                    MemoryItem.status == "active",
                )
            s.commit()
    except Exception:
        logger.exception("receipt keepalive writeback failed (non-fatal)")
    return touched


def backfill_used_ids(
    event_id: str, used_ids: list[str], request_id: str | None = None, *, principal=None
) -> None:
    """宿主回执：哪些被召回的记忆真正被用进回答（整体覆盖语义）。

    回执链一等化（ADR-0049）：回执成功 → receipt_status="acked" + receipt_at 落定。
    request_id 提供时与事件列核对——不一致如实记日志（不拒绝、不改状态；
    宁 miss 不脏写，回执归属以 event_id 为准）。

    保活回写（票 `.scratch/v0.66-next-window-2026-11/03`）：回执成功后把本轮新增的
    used_ids 刷回 `MemoryItem.injection_count` / `last_used_at`，见 `_touch_keepalive`
    （不再写 `use_count`：那是考功/三省只认的显式反馈次数）。

    归属（票 `.scratch/mcp-identity-gaps/01c`）：此前**一个身份都不取**——
    A 拿 B 的 `event_id` 就能把 B 的回执从 `pending` 改成 `acked` 并覆盖成
    A 给的值。这不是读泄漏，是**脏写**：回执链是 ADR-0049 弱标注的地基，
    被污染后 dry-run 的 weak_hit_rate 算的就是假数。

    越权时**不抛异常**：宿主在生成链路上，抛错会打断主流程。如实记
    warning 后返回（不写），调用方从 `used_count` 之外没有别的信号，
    所以日志是唯一可观测处——这正是"宁 miss 不脏写"的形态。

    vetoed 是终态（ADR-0084）：被否决的一轮从未发给模型，迟到的回执
    不得把它拖回 acked、更不得回写保活计数——否则假注入计数从后门回来。
    """
    try:
        bump: list[str] = []
        with db.get_session() as s:
            # 票 23：回执覆盖写改认领式条件 UPDATE（WHERE receipt_status=扫描值），
            # 败方 rowcount=0 → 回滚后重读最新状态重算增量（重放同一回执不再双计
            # 保活，红测 F2b；vetoed 终态不再被迟到处回执拉回 acked，红测 F2c）。
            for _attempt in range(3):
                ev = s.get(RetrievalEvent, event_id)
                if not ev:
                    return
                # 票 50：event_id/request_id 均来自 REST 回执请求体（宿主可控，
                # 可含换行伪造日志行）——本函数五处插值统一过 loggable
                ev_log = loggable(event_id, limit=128)
                if ev.receipt_status == "vetoed":
                    logger.info("receipt backfill ignored (vetoed is terminal): ev=%s", ev_log)
                    return
                if request_id and ev.request_id and request_id != ev.request_id:
                    logger.warning(
                        "receipt request_id mismatch (kept by event_id): ev=%s ev_req=%s got=%s",
                        ev_log,
                        loggable(ev.request_id, limit=128),
                        loggable(request_id, limit=128),
                    )
                if not _backfill_owner_ok(ev, principal):
                    logger.warning("receipt backfill refused (owner mismatch): ev=%s", ev_log)
                    return
                # 覆盖写之前先取「本轮新增的 used_ids」与「本事件召回过的 id」：
                # 保活回写只认这两者的交集（重放同一回执不累加、夹带外来 id 不计）。
                already = set(ev.used_ids or [])
                recalled = set(ev.result_ids or [])
                now = utcnow()
                scanned_status = ev.receipt_status
                claimed = atomic_update(
                    s,
                    RetrievalEvent,
                    event_id,
                    {
                        "used_ids": list(used_ids),
                        "receipt_status": "acked",
                        "receipt_at": now,
                    },
                    RetrievalEvent.receipt_status == scanned_status,
                )
                if claimed:
                    s.commit()
                    bump = [mid for mid in used_ids if mid not in already and mid in recalled]
                    break
                s.rollback()
                logger.info("receipt backfill raced; retrying on fresh state: ev=%s", ev_log)
            else:
                logger.warning(
                    "receipt backfill abandoned after retries (non-fatal): ev=%s", ev_log
                )
                return
        # 回执先落库，再单开事务做保活回写（票 03 病灶 2）：回写失败不能倒过来把
        # acked 拖回 pending（ADR-0049 弱标注地基完整性优先）；顺序执行也避免在同一
        # 引擎上套嵌两个 Session——内存库的 StaticPool 共用一条 DBAPI 连接，套嵌会
        # 让两边的未提交状态互相看见。
        _touch_keepalive(bump, principal=principal, event_id=event_id)
    except Exception:
        logger.exception("retrieval used_ids backfill failed (non-fatal)")


def mark_vetoed(event_id: str) -> bool:
    """宿主否决了一轮（ADR-0084，v0.67 票 01）：该轮检索事件作废，返回是否发生转移。

    被否决的一轮从未发给模型（DLP 拦密码/密钥等），检索日志却把原话留在了
    `query_text`/`query_norm_hash` 里——DLP 拦它是不让它到模型，兰台不能自己
    把它落库。这里抹原文：`query_text`/`query_norm_hash` 清空（归一化哈希对
    低熵原文就是可验证口令，留着等于没抹）、清 `used_ids`、置
    `receipt_status="vetoed"`（新终态）+ `receipt_at`。

    不做 `injection_count` 反冲：延后回执方案下否决轮从未回执（插件侧
    `_on_session_end` 知道未被否决才发回执），假计数从根上不产生；对已回执
    行做并发反冲会误伤同窗内合法回执——宁 miss 不脏写。

    行本身保留：vetoed 单列计数可观测（守门命中了多少轮），`result_ids`/
    `latency_ms` 等无原文元数据不动。

    返回值语义：找到且此前不是 vetoed（发生了状态转移）→ True；
    已 vetoed（幂等重放，无转移）或缺失 id → False。
    """
    try:
        with db.get_session() as s:
            ev = s.get(RetrievalEvent, event_id)
            if not ev or ev.receipt_status == "vetoed":
                return False
            ev.query_text = ""
            ev.query_norm_hash = ""
            ev.used_ids = []
            ev.receipt_status = "vetoed"
            ev.receipt_at = utcnow()
            s.add(ev)
            s.commit()
            return True
    except Exception:
        logger.exception("retrieval veto marking failed (non-fatal)")
        return False


def mark_abandoned(event_id: str) -> bool:
    """硬墙超时后的孤儿事件收尾（巡检 2026-11-07 12:00），返回是否发生转移。

    shell_hook 的 QUERY 通道超过 `SHELL_HOOK_TIMEOUT` 时，主线程先把画像兜底回给
    宿主——**不带 event_id**；daemon worker 不可取消，跑完仍会 `log_retrieval`
    落一条带原话的事件。宿主手里没有这个 id：回执发不了，这一轮若被否决
    （ADR-0084）`retrieval_veto` 也发不了，原话就永久留在库里。

    这里就地归结：宿主并未得到本轮检索结果，事件应为 missed（与
    `mark_missed_receipts` 300s 后的结论一致，只是不必等）；同时按
    `mark_vetoed` 的口径抹掉 `query_text`/`query_norm_hash`/`used_ids`——无法确认
    宿主是否否决了这一轮时，宁可不留。行本身保留：`latency_ms`/`result_ids`
    等无原文元数据照旧可观测（超时轮有多慢）。

    只动 pending：已 vetoed/acked/missed 的不改写（理论上宿主拿不到 id，
    不会先一步回执或否决；这是防御性的宁 miss 不脏写）。缺失 id 或非 pending → False。
    """
    if not event_id:
        return False
    try:
        with db.get_session() as s:
            ev = s.get(RetrievalEvent, event_id)
            if not ev or ev.receipt_status != "pending":
                return False
            ev.query_text = ""
            ev.query_norm_hash = ""
            ev.used_ids = []
            ev.receipt_status = "missed"
            ev.receipt_at = utcnow()
            s.add(ev)
            s.commit()
            return True
    except Exception:
        logger.exception("retrieval abandon marking failed (non-fatal)")
        return False


RECEIPT_MISS_TIMEOUT_SECONDS = 300


def mark_missed_receipts(timeout_seconds: int = RECEIPT_MISS_TIMEOUT_SECONDS, now=None) -> int:
    """回执超时判定（ADR-0049）：pending 且超龄 → missed，返回置位数。

    missed 是事实不是错误（宁 miss 不脏写）：宿主未回执的事件如实置位，
    使「回执缺失」可观测、可统计，而不是永远 pending。幂等：已置位不重算。

    原话同事务抹除（ADR-0084 §2.6，v0.68 票 10）：与 `mark_vetoed` 同源论证——
    `query_norm_hash` 带索引，对低熵原文就是可验证口令，留着等于没抹；missed 行
    的宿主是否真用过这轮从未得到确认，原话不该无代价永留。只抹
    `query_text`/`query_norm_hash`，**不清 `used_ids`**（弱标注源，backfill 只拒
    vetoed，missed→acked 仍合法）、不回冲保活链（`_touch_keepalive` 只认
    「首次被回执」的增量，missed 行本就没刷过，无假计数可回冲）。
    """
    from datetime import timedelta

    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=timeout_seconds)
    try:
        with db.get_session() as s:
            rows = s.exec(
                select(RetrievalEvent).where(
                    RetrievalEvent.receipt_status == "pending",
                    RetrievalEvent.created_at < cutoff,
                )
            ).all()
            for ev in rows:
                ev.receipt_status = "missed"
                ev.receipt_at = moment
                ev.query_text = ""
                ev.query_norm_hash = ""
                s.add(ev)
            if rows:
                s.commit()
            return len(rows)
    except Exception:
        logger.exception("receipt miss marking failed (non-fatal)")
        return 0


def receipt_traceability_report() -> dict:
    """可追溯率统计出口（ADR-0049；票 06 宿主冒烟与自证复用）。

    口径（票据 04 交付 4）：acked 事件中，used_ids 非空且每个 used_id 均能
    回溯到现存 MemoryItem 行的比例（missed/pending/vetoed 不入分子）。全部
    计数如实分列，不混计；无 acked 样本时 rate 如实返回 None（不编造）。
    vetoed 单列（ADR-0084）：守门命中轮数可观测，不计入任何回执质量分母。
    """
    try:
        with db.get_session() as s:
            status_rows = s.exec(
                select(RetrievalEvent.receipt_status, RetrievalEvent.used_ids)
            ).all()
            acked_used: list[list[str]] = []
            pending = missed = vetoed = acked_plain = 0
            for status, used_ids in status_rows:
                if status == "acked":
                    if used_ids:
                        acked_used.append(list(used_ids))
                    else:
                        acked_plain += 1
                elif status == "missed":
                    missed += 1
                elif status == "vetoed":
                    vetoed += 1
                else:
                    pending += 1
        traceable = 0
        if acked_used:
            with db.get_session() as s2:
                for used in acked_used:
                    existing = [uid for uid in used if s2.get(MemoryItem, uid) is not None]
                    if used and len(existing) == len(used):
                        traceable += 1
        acked_total = len(acked_used) + acked_plain
        return {
            "pending": pending,
            "acked": acked_total,
            "acked_with_used_ids": len(acked_used),
            "missed": missed,
            "vetoed": vetoed,
            "traceable": traceable,
            # 口径：acked 事件中可完整回溯的比例（used_ids 空的 acked 不入分子）
            "traceability_rate": (round(traceable / len(acked_used), 4) if acked_used else None),
        }
    except Exception:
        logger.exception("receipt traceability report failed (non-fatal)")
        return {}
