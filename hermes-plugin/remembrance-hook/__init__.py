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
# v0.5：会话缓冲——session_id → user_message 列表（on_session_end flush 用）
_session_buffers: dict[str, list[str]] = {}


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
            logger.info("remembrance-hook: shell_hook --serve 子进程已启动 (pid=%s)", _proc.pid)
            return _proc
        except OSError as exc:
            logger.warning("remembrance-hook: spawn failed: %s", exc)
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
                logger.info("remembrance-hook: 子进程就绪 (pid=%s)", proc.pid)
                return True
            except json.JSONDecodeError:
                __import__("time").sleep(0.5)
        return False
    except (OSError, AssertionError):
        return False


def _call_hook(query: str) -> str | None:
    """向常驻 serve 子进程发一行注入请求，读一行响应（带锁，防并发交错）。"""
    proc = _ensure_proc()
    if proc is None:
        return None
    if not _wait_ready():
        logger.warning("remembrance-hook: 子进程未就绪，跳过注入")
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
        ctx = data.get("context", "") if isinstance(data, dict) else ""
        return str(ctx) if ctx else None
    except (json.JSONDecodeError, OSError, ValueError, AssertionError) as exc:
        logger.warning("remembrance-hook: call failed: %r", exc)
        # 子进程可能已死，标记让下次自动重启
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False
        return None


def _call_dialogue(text: str) -> None:
    """向 serve 子进程发对话写入请求（on_session_end flush 用，失败静默）。"""
    proc = _ensure_proc()
    if proc is None:
        return
    if not _wait_ready():
        return
    try:
        with _lock:
            line = (json.dumps({"type": "dialogue", "text": text},
                               ensure_ascii=False) + "\n").encode("utf-8")
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
        logger.warning("remembrance-hook: dialogue call failed: %r", exc)
        global _proc, _proc_ready
        _proc = None
        _proc_ready = False


# ── 会话缓冲（v0.5 对话写通道原料）──────────────────────────────

def _buffer_turn(session_id: str, user_message: str) -> None:
    """累积一轮 user_message 到会话缓冲（有界，防长期会话膨胀）。"""
    if not session_id:
        return
    msg = (user_message or "").strip()
    if not msg:
        return
    with _lock:
        buf = _session_buffers.setdefault(session_id, [])
        buf.append(msg)
        total = sum(len(m) for m in buf)
        while len(buf) > _SESSION_BUFFER_MAX_MSGS or total > _SESSION_BUFFER_MAX_CHARS:
            dropped = buf.pop(0)
            total -= len(dropped)


def _flush_session(session_id: str) -> None:
    """清空并提交某会话的缓冲消息（on_session_end 调用）。"""
    with _lock:
        msgs = _session_buffers.pop(session_id, [])
    for text in msgs:
        _call_dialogue(text)


def _on_pre_llm_call(**kwargs) -> dict | None:
    """pre_llm_call 回调：检索注入 + 会话缓冲。"""
    query = kwargs.get("user_message") or ""
    session_id = kwargs.get("session_id") or ""
    if not isinstance(query, str):
        return None
    q = query.strip()
    if len(q) < _MIN_QUERY_CHARS:
        return None
    # v0.5：无论是否注入，都把用户消息累积为对话写通道原料
    _buffer_turn(session_id, q)
    # 短句且无触发词 → 不注入（与 gate 语义一致，省子进程开销）
    if len(q) <= 15 and not any(w in q for w in _TRIGGER_WORDS):
        return None
    ctx = _call_hook(q)
    if not ctx:
        return None
    return {"context": ctx}


def _on_session_end(**kwargs) -> None:
    """on_session_end 回调：每轮对话结束 flush 会话缓冲 → 对话写通道。"""
    session_id = kwargs.get("session_id") or ""
    if session_id:
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
        logger.debug("remembrance-hook: warmup failed (non-fatal)", exc_info=True)


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
    threading.Thread(target=_warmup, daemon=True, name="remembrance-hook-warmup").start()
    logger.info("remembrance-hook: pre_llm_call 注入 + on_session_end 对话写入已注册（预热中）")
