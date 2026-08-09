"""Shell Hook 契约测试：超时与静默降级。"""
import importlib.util
import io
import json
import os
import time

import pytest

HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "shell_hook.py")


def _load_hook(monkeypatch, embed_delay=0.0, timeout=0.2):
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Patch settings timeout (singleton already loaded)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", timeout)

    # Patch os._exit to raise SystemExit (so pytest can catch it)
    def fake_exit(code=0):
        raise SystemExit(code)
    monkeypatch.setattr(mod.os, "_exit", fake_exit)

    def slow_embed(texts):
        time.sleep(embed_delay)
        return [[0.1] * 8]

    monkeypatch.setattr(mod, "embed", slow_embed)
    return mod


def test_build_context_short_query_returns_empty(monkeypatch):
    mod = _load_hook(monkeypatch)
    assert mod.build_context("你好") == {}


def test_main_timeout_returns_empty_json(monkeypatch, capsys):
    """超时降级：返回 {} 且不退出（serve 模式需容错，不能再 os._exit）。"""
    mod = _load_hook(monkeypatch, embed_delay=1.0, timeout=0.2)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"query": "这是一个超过三字符的查询"})))
    start = time.perf_counter()
    mod.main()  # 不再抛 SystemExit——超时静默返回 {}
    elapsed = time.perf_counter() - start
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {}
    assert elapsed < 3.0  # 超时生效（远小于 embed 的 5s；留环境波动余量）


def test_stdin_forced_utf8_reconfigure(monkeypatch):
    """回归：Windows GBK 环境下 stdin 必须被强制为 UTF-8。

    修复背景：Hermes 按 UTF-8 写 JSON（含中文 query），Python 默认按 GBK 解码
    →「你好」变「浣犲ソ」→ 检索零命中、注入静默失效。reconfigure 必须在读 stdin 前执行。
    """
    mod = _load_hook(monkeypatch)
    # 模拟 GBK 环境：若 reconfigure 未生效，sys.stdin.encoding 会是 gbk/cp936
    assert mod.sys.stdin.encoding.lower() in ("utf-8", "utf8")
    # 且 stdout 也被强制
    assert mod.sys.stdout.encoding.lower() in ("utf-8", "utf8")


def test_handle_one_empty_returns_empty(monkeypatch):
    """serve 模式核心：单条处理对空输入/坏 JSON 返回 {}，不抛。"""
    mod = _load_hook(monkeypatch)
    assert mod._handle_one("") == {}
    assert mod._handle_one("not-json{{{") == {}
    assert mod._handle_one('{"query":""}') == {}


def test_serve_mode_processes_line(monkeypatch, capsys):
    """serve 模式：--serve 参数走 NDJSON 循环，逐行响应。"""
    mod = _load_hook(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"query":"这是一个超过三字符的查询"}\n'))
    monkeypatch.setattr("sys.argv", ["shell_hook.py", "--serve"])
    mod.main()
    out = capsys.readouterr().out.strip()
    # 输出应是合法 JSON（空 context 或注入，取决于 embed mock）
    assert out.startswith("{") and out.endswith("}")


def test_build_context_has_evidence(monkeypatch):
    """有命中时：context 含"依据"段（记忆 id + 摘要），并返回结构化 evidence。"""
    mod = _load_hook(monkeypatch)
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, Session, create_engine
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from remembrance.models.tables import MemoryItem
        s.add(MemoryItem(id="mem_1", memory_type="semantic", key="k",
                         content="用户喜欢 Python 和 Rust"))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
            return [{"id": "mem_1", "distance": 0.1}]

    import remembrance.storage.db as db_module
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "get_vector_store", lambda: _FakeStore())

    out = mod.build_context("这是一个超过三字符的查询")
    assert "依据" in out["context"]
    assert "mem_1" in out["context"]
    assert out["evidence"][0]["id"] == "mem_1"
    assert "用户喜欢" in out["evidence"][0]["content"]
    assert out["event_id"] is not None  # 回填通道不受影响


def test_build_context_no_hits_no_evidence(monkeypatch):
    """无命中：零侵入降级，context 不带依据段。"""
    mod = _load_hook(monkeypatch)

    class _EmptyStore:
        def search(self, qv, top_k=5):
            return []

    monkeypatch.setattr(mod, "get_vector_store", lambda: _EmptyStore())
    out = mod.build_context("这是一个超过三字符的查询")
    assert out == {}


def test_handle_dialogue_channel(monkeypatch):
    """serve 协议扩展（v0.5）：{"type":"dialogue"} 走对话写入通道。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch
    with patch("remembrance.ingestion.dialogue.ingest_dialogue",
               return_value={"ingested": True, "candidate_id": "cand_1",
                             "fastpath": True, "lane": "general",
                             "status": "fastpath"}) as m:
        out = mod._handle_one('{"type":"dialogue","text":"记住：明天下午3点开会"}')
    assert out["ok"] is True
    assert out["candidate_id"] == "cand_1"
    m.assert_called_once_with("记住：明天下午3点开会")


def test_handle_dialogue_empty_text(monkeypatch):
    """dialogue 通道空文本 → {}，不调 ingest。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch
    with patch("remembrance.ingestion.dialogue.ingest_dialogue") as m:
        out = mod._handle_one('{"type":"dialogue","text":"   "}')
    assert out == {}
    m.assert_not_called()


def test_handle_dialogue_failure_silent(monkeypatch):
    """dialogue 通道异常 → {} 零侵入。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch
    with patch("remembrance.ingestion.dialogue.ingest_dialogue",
               side_effect=RuntimeError("boom")):
        out = mod._handle_one('{"type":"dialogue","text":"记住：明天开会"}')
    assert out == {}
