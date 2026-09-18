"""兰台记忆注入 + 对话自动写入插件（serve/桌面模式专用）。

背景：Hermes 桌面版走 ``serve`` 命令，不在 _AGENT_COMMANDS 集合
（{None, chat, acp, rl}）里，因此 shell hooks（pre_llm_call）不会被注册。
本插件通过 Python 插件通道注册 pre_llm_call / on_session_end 回调，等效实现：

  - pre_llm_call：每轮对话前检索兰台记忆 → 注入 user message
    → 记录检索事件；同时把 user_message 累积到会话缓冲（v0.5 对话写通道原料）
  - on_session_end：每轮对话结束触发 → 缓冲 flush 给 shell_hook 对话通道
    → ingest_dialogue（fastpath 直通 / 提取建候选 / 闲聊入待审队列）

实现策略：常驻子进程跑 ``shell_hook.py --serve``（NDJSON 循环），
消除冷启动开销（chromadb/jieba 只加载一次，热处理亚秒级）。
回调只写一行请求、读一行响应。

安全边界：
- 子进程启动失败/失活 → 静默降级返回 None（Hermes 忽略 None）
- 单次请求 5 秒硬超时（防卡死）；对话写入 30 秒超时（含 LLM 提取）
- 任何异常绝不抛出（插件不能拖慢/搞崩 Hermes）
- 会话缓冲有界（条数/总字符上限，防长期会话内存膨胀）
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────
_VENV_PY = r"C:/Users/Asus/Desktop/记忆/.venv-audit/Scripts/python.exe"
_SHELL_HOOK = r"C:/Users/Asus/Desktop/记忆/scripts/shell_hook.py"
_REQUEST_TIMEOUT = 5.0  # 注入请求超时（秒）
_DIALOGUE_TIMEOUT = 30.0  # 对话写入请求超时（秒，含 LLM 提取）
_MIN_QUERY_CHARS = 2
# 触发词（与 gate 语义对齐：短句无触发词不注入，省开销）
_TRIGGER_WORDS = ("记得", "上次", "回忆", "帮我查", "之前", "忘记", "以前", "曾经")
# 会话缓冲上限（防膨胀）：条数与总字符
_SESSION_BUFFER_MAX_MSGS = 200
_SESSION_BUFFER_MAX_CHARS = 200_000

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_proc_ready = False  # 子进程是否已通过就绪探测
# v0.5：会话缓冲——session_id → [{"text": str, "turn": int}]（on_session_end flush 用）
# turn 为该会话内的 1-based 缓冲序号（P0 票02：随来源链透传给 ingest_dialogue）
_session_buffers: dict[str, list[dict]] = {}
# v0.15（ADR-0022）：已注入底本（checkpoint）的会话集——每会话首轮注入一次
_checkpoint_injected: set[str] = set()
_CHECKPOINT_INJECTED_MAX = 50  # 防会话集无限膨胀（只影响首轮注入标记）

# 块来源规则前缀/句式（宁 miss：命中才填，拿不准留空）
_NEXT_PREFIXES = ("接下来", "下一步", "然后", "待办")
_DECISION_MARKERS = ("决定了", "就按", "采用", "改为")
_NOTE_MARKERS = ("别忘了", "记得", "待办", "提醒我")


def _ensure_proc() -> subprocess.Popen | None:
    """确保常驻 shell_hook --serve 子进程存活（带锁防并发双起）。"""
    global _proc, _proc_ready
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return _proc
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        try:
            _proc = subprocess.Popen(
                [_VENV_PY, _SHELL_HOOK, "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=False,
            )
            _proc_ready = False
            logger.info("lantai-hook: shell_hook --serve 子进程已启动 (pid=%s)", _proc.pid)
            return _proc
        except OSError as exc:
            logger.warning("lantai-hook: spawn failed: %s", exc)
            _proc = None
            return None


def _wait_ready(timeout: float = 15.0) -> bool:
    """冷启动探测：子进程加载 chromadb/jieba 约 10-15s。

    就绪判定：发一个空查询（build_context 对空串秒回 {}），能收到
    合法 JSON 即视为就绪。失败不阻塞——返回 False 由调用方决定重试。
    """
    global _proc_ready
    if _proc_ready:
        return True
    proc = _proc
    if proc is None:
        return False
    deadline = __import__("time").time() + timeout
    try:
        while __import__("time").time() < deadline:
            if proc.poll() is not None:
                return False  # 子进程已退出
            with _lock:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(b'{"query":""}\n')
                proc.stdin.flush()
                buf = bytearray()
                while True:
                    ch = proc.stdout.read(1)
                    if not ch or ch == b"\n":
                        break
                    buf += ch
            # Windows 下 print 输出 \r\n，剥离 \r 再解析
            try:
                json.loads(buf.decode("utf-8", errors="replace").strip().rstrip("\r"))
                _proc_ready = True
                logger.info("lantai-hook: 子进程就绪 (pid=%s)", proc.pid)
                return True
            except json.JSONDecodeError:
                __import__("time").sleep(0.5)
        return False
    except (OSError, AssertionError):
        return False


def _call_hook(query: str) -> dict | None:
    """向常驻 serve 子进程发一行注入请求，读一行响应（带锁，防并发交错）。

    返回完整响应 dict（context/evidence/event_id）——event_id + evidence 供
    注入回执（P0 票02）；无有效响应返回 None。
    """
    proc = _ensure_proc()
    if proc is None:
        return None
    if not _wait_ready():
        logger.warning("lantai-hook: 子进程未就绪，跳过注入")
        return None
    try:
        with _lock:
            line = (json.dumps({"query": query}, ensure_ascii=False) + "\n").encode("utf-8")
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()
            # 逐字节读一行，防 readline 因编码问题截断
            buf = bytearray()
            while True:
                ch = proc.stdout.read(1)
                if not ch or ch == b"\n":
                    break
                buf += ch
        out = buf.decode("utf-8", errors="replace").strip().rstrip("\r")
        if not out:
            return None
        data = json.loads(out)
        if isinstance(data, dict) and data.get("context"):
            return data
        return None
    except (json.JSONDecodeError, OSError, ValueError, AssertionError) as exc:
        logger.warning("lantai-hook: call failed: %r", exc)
        # 子进程可能已死，标记让下次自动重启
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False
        return None


def _call_dialogue(text: str, session_id: str = "", turn: int | None = None) -> None:
    """向 serve 子进程发对话写入请求（on_session_end flush 用，失败静默）。

    session_id/turn：来源链显式透传（P0 票02），落候选 session_id 与
    provenance.origin_turn；缺失留空/None，不补 0 不猜。
    """
    proc = _ensure_proc()
    if proc is None:
        return
    if not _wait_ready():
        return
    try:
        with _lock:
            line = (
                json.dumps(
                    {"type": "dialogue", "text": text, "session_id": session_id, "turn": turn},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()
            buf = bytearray()
            while True:
                ch = proc.stdout.read(1)
                if not ch or ch == b"\n":
                    break
                buf += ch
    except (OSError, ValueError, AssertionError) as exc:
        logger.warning("lantai-hook: dialogue call failed: %r", exc)
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False


def _call_backfill(event_id: str, used_ids: list[str]) -> None:
    """注入回执（P0 票02）：检索上下文注入成功后按 event_id 回填 used_ids（弱标注）。

    现有机制：RetrievalEvent.used_ids + backfill_used_ids（MCP backfill /
    POST /retrieval/backfill 同源）。失败静默——回执缺失只影响弱标注覆盖，不侵入注入。
    """
    proc = _ensure_proc()
    if proc is None or not event_id or not used_ids:
        return
    if not _wait_ready():
        return
    try:
        with _lock:
            line = (
                json.dumps(
                    {"type": "backfill", "event_id": event_id, "used_ids": used_ids},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()
            buf = bytearray()
            while True:
                ch = proc.stdout.read(1)
                if not ch or ch == b"\n":
                    break
                buf += ch
    except (OSError, ValueError, AssertionError) as exc:
        logger.warning("lantai-hook: backfill call failed: %r", exc)
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False


def _call_checkpoint() -> str | None:
    """向 serve 子进程发底本注入请求（会话首轮，失败静默降级）。"""
    proc = _ensure_proc()
    if proc is None:
        return None
    if not _wait_ready():
        return None
    try:
        with _lock:
            line = (json.dumps({"type": "checkpoint"}, ensure_ascii=False) + "\n").encode("utf-8")
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()
            buf = bytearray()
            while True:
                ch = proc.stdout.read(1)
                if not ch or ch == b"\n":
                    break
                buf += ch
        out = buf.decode("utf-8", errors="replace").strip().rstrip("\r")
        if not out:
            return None
        data = json.loads(out)
        ctx = data.get("context", "") if isinstance(data, dict) else ""
        return str(ctx) if ctx else None
    except (json.JSONDecodeError, OSError, ValueError, AssertionError) as exc:
        logger.warning("lantai-hook: checkpoint call failed: %r", exc)
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False
        return None


def _call_checkpoint_write(session_id: str, blocks: dict) -> None:
    """向 serve 子进程发底本写入请求（on_session_end 落快照，失败静默）。"""
    proc = _ensure_proc()
    if proc is None:
        return
    if not _wait_ready():
        return
    try:
        with _lock:
            line = (
                json.dumps(
                    {"type": "checkpoint_write", "session_id": session_id, "blocks": blocks},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()
            buf = bytearray()
            while True:
                ch = proc.stdout.read(1)
                if not ch or ch == b"\n":
                    break
                buf += ch
    except (OSError, ValueError, AssertionError) as exc:
        logger.warning("lantai-hook: checkpoint write failed: %r", exc)
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False


def build_session_blocks(messages: list[str]) -> dict:
    """五段块构建（ADR-0022 纯函数，宁 miss 不脏写：命中才填）。

    - cp_active_intent（在做）= 末条 user 消息
    - cp_next_action（下一步）= 末条以 接下来/下一步/然后/待办 开头
    - cp_key_decisions（决策）= 末条含 决定/就按/采用/改为 声明句式
    - cp_open_notes（待办）= 末条含 别忘了/记得/待办/提醒我
    - cp_current_work（工作区）= 无可靠信号，恒空
    """
    msgs = [m for m in (messages or []) if isinstance(m, str) and m.strip()]
    if not msgs:
        return {}
    last = msgs[-1].strip()
    blocks = {"cp_active_intent": last[:600]}
    if any(last.startswith(p) for p in _NEXT_PREFIXES):
        blocks["cp_next_action"] = last[:600]
    if any(m in last for m in _DECISION_MARKERS):
        blocks["cp_key_decisions"] = last[:600]
    if any(m in last for m in _NOTE_MARKERS):
        blocks["cp_open_notes"] = last[:600]
    return blocks


# ── 会话缓冲（v0.5 对话写通道原料）──────────────────────────────


def _buffer_turn(session_id: str, user_message: str) -> None:
    """累积一轮 user_message 到会话缓冲（有界，防长期会话膨胀）。

    turn = 该会话内 1-based 缓冲序号（P0 票02 来源链）；同一消息重复投递
    会占用新序号——如实反映「说了两遍」，不做去重猜测。
    """
    if not session_id:
        return
    msg = (user_message or "").strip()
    if not msg:
        return
    with _lock:
        buf = _session_buffers.setdefault(session_id, [])
        buf.append({"text": msg, "turn": len(buf) + 1})
        total = sum(len(m["text"]) for m in buf)
        while len(buf) > _SESSION_BUFFER_MAX_MSGS or total > _SESSION_BUFFER_MAX_CHARS:
            dropped = buf.pop(0)
            total -= len(dropped["text"])


def _flush_session(session_id: str) -> None:
    """清空并提交某会话的缓冲消息（on_session_end 调用），逐条携带来源。"""
    with _lock:
        entries = _session_buffers.pop(session_id, [])
    for entry in entries:
        _call_dialogue(entry["text"], session_id=session_id, turn=entry["turn"])


def _on_pre_llm_call(**kwargs) -> dict | None:
    """pre_llm_call 回调：底本首轮注入 + 检索注入 + 会话缓冲 + 注入回执。"""
    query = kwargs.get("user_message") or ""
    session_id = kwargs.get("session_id") or ""
    if not isinstance(query, str):
        return None
    q = query.strip()
    # v0.5：无论是否注入，都把用户消息累积为对话写通道原料
    _buffer_turn(session_id, q)
    # ADR-0022：会话首轮先注入底本（与查询长度/触发词无关，每会话一次）
    ck = None
    if session_id and session_id not in _checkpoint_injected:
        _mark_checkpoint_injected(session_id)
        ck = _call_checkpoint()
    if len(q) < _MIN_QUERY_CHARS:
        return {"context": ck} if ck else None
    # 短句且无触发词 → 不注入检索（与 gate 语义一致，省子进程开销）
    if len(q) <= 15 and not any(w in q for w in _TRIGGER_WORDS):
        return {"context": ck} if ck else None
    data = _call_hook(q)
    if not data:
        return {"context": ck} if ck else None
    ctx = str(data.get("context") or "")
    if ck:
        ctx = ck + "\n\n" + ctx
    # 注入回执（P0 票02）：上下文已拼进本轮回包 = 弱标注「已注入」；
    # 按 event_id 回填 evidence 记忆 id。回执失败静默（缺失只影响弱标注覆盖）。
    event_id = data.get("event_id")
    used_ids = [
        e.get("id") for e in (data.get("evidence") or []) if isinstance(e, dict) and e.get("id")
    ]
    if event_id and used_ids:
        _call_backfill(str(event_id), used_ids)
    return {"context": ctx}


def _mark_checkpoint_injected(session_id: str) -> None:
    """登记已注入底本的会话（有界：超容量清空重建，只影响首轮标记）。"""
    with _lock:
        if len(_checkpoint_injected) >= _CHECKPOINT_INJECTED_MAX:
            _checkpoint_injected.clear()
        _checkpoint_injected.add(session_id)


def _on_session_end(**kwargs) -> None:
    """on_session_end 回调：落底本五段块 + flush 会话缓冲 → 对话写通道。"""
    session_id = kwargs.get("session_id") or ""
    if not session_id:
        return
    with _lock:
        entries = list(_session_buffers.get(session_id, []))
    blocks = build_session_blocks([e["text"] for e in entries])
    if blocks:
        _call_checkpoint_write(session_id, blocks)
    _flush_session(session_id)


def _warmup() -> None:
    """后台预热：Hermes 启动时即拉起 serve 子进程并等待就绪。

    在独立线程里完整跑完就绪探测（chromadb/jieba 冷启动约 10-15s），
    确保用户首次对话时子进程已热。绝不阻塞 Hermes 插件加载。
    """
    try:
        _ensure_proc()
        _wait_ready(timeout=40)
    except Exception:
        logger.debug("lantai-hook: warmup failed (non-fatal)", exc_info=True)


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
    threading.Thread(target=_warmup, daemon=True, name="lantai-hook-warmup").start()
    logger.info("lantai-hook: pre_llm_call 注入 + on_session_end 对话写入已注册（预热中）")
