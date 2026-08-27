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
    class FakeStream:
        def __init__(self):
            self.encoding = "gbk"

        def reconfigure(self, encoding=None):
            self.encoding = encoding

    fake_stdin = FakeStream()
    fake_stdout = FakeStream()
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    _load_hook(monkeypatch)
    assert fake_stdin.encoding == "utf-8"
    assert fake_stdout.encoding == "utf-8"


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
        from lantai.models.tables import MemoryItem
        s.add(MemoryItem(id="mem_1", memory_type="semantic", key="k",
                         content="用户喜欢 Python 和 Rust"))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
            return [{"id": "mem_1", "distance": 0.1}]

    import lantai.storage.db as db_module
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
    with patch("lantai.ingestion.dialogue.ingest_dialogue",
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
    with patch("lantai.ingestion.dialogue.ingest_dialogue") as m:
        out = mod._handle_one('{"type":"dialogue","text":"   "}')
    assert out == {}
    m.assert_not_called()


def test_handle_dialogue_failure_silent(monkeypatch):
    """dialogue 通道异常 → {} 零侵入。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch
    with patch("lantai.ingestion.dialogue.ingest_dialogue",
               side_effect=RuntimeError("boom")):
        out = mod._handle_one('{"type":"dialogue","text":"记住：明天开会"}')
    assert out == {}

# ── 召回预算 + 记忆工具指南（借鉴 TencentDB Agent Memory auto-recall）────────

def test_truncate_codepoints_applies_suffix(monkeypatch):
    """纯函数冒烟：超长截断并附后缀；短文本原样返回。"""
    mod = _load_hook(monkeypatch)
    long_text = "用户喜欢 Python" + "很长的内容" * 20
    out = mod._truncate_codepoints(long_text, 80, mod._RECALL_TRUNCATION_SUFFIX)
    assert len(out) <= 80 + len(mod._RECALL_TRUNCATION_SUFFIX)
    assert out.endswith(mod._RECALL_TRUNCATION_SUFFIX)
    assert "用户喜欢 Python" in out
    assert mod._truncate_codepoints("短文本", 20, "suffix") == "短文本"


def test_truncate_codepoints_emoji_safe(monkeypatch):
    """纯函数冒烟：emoji/中文混合按码点截断，不产生半个字符（可无损解码）。"""
    mod = _load_hook(monkeypatch)
    text = "🎉" * 30 + "中文" * 30
    out = mod._truncate_codepoints(text, 25, mod._RECALL_TRUNCATION_SUFFIX)
    out.encode("utf-8")  # 若切开代理对，这里 encode/decode 会损坏
    assert len(out) >= 1
    # 截断后的可见字符（不含后缀）应严格 ≤ 25 码点
    body = out[: -len(mod._RECALL_TRUNCATION_SUFFIX)]
    assert len(list(body)) <= 25


def test_apply_recall_budget_total_cap(monkeypatch):
    """纯函数冒烟：总预算不足时丢弃剩余行并正确计数。"""
    mod = _load_hook(monkeypatch)
    lines = ["a" * 50, "b" * 50, "c" * 50, "d" * 50]
    budgeted, dropped = mod._apply_recall_budget(lines, 110)
    assert dropped == 2  # 第 1 行 50 + 换行 1 + 第 2 行 50 = 101 ≤ 110；第 3 行越界
    assert budgeted == lines[:2]


def test_apply_recall_budget_all_fit(monkeypatch):
    """纯函数冒烟：预算充足时零丢弃，顺序不变。"""
    mod = _load_hook(monkeypatch)
    lines = ["a" * 10, "b" * 10]
    budgeted, dropped = mod._apply_recall_budget(lines, 1000)
    assert dropped == 0
    assert budgeted == lines


def test_build_tools_guide_content(monkeypatch):
    """纯函数冒烟：指南含工具名与搜索次数上限；截断时附截断提示。"""
    mod = _load_hook(monkeypatch)
    guide_truncated = mod._build_tools_guide(True)
    assert "search" in guide_truncated and "3 次" in guide_truncated
    assert "已截断" in guide_truncated
    guide_plain = mod._build_tools_guide(False)
    assert "已截断" not in guide_plain
    assert "add" in guide_plain


def test_format_memory_entry_shape(monkeypatch):
    """纯函数冒烟：注入行格式与 evidence 内容一致（同源截断）。"""
    mod = _load_hook(monkeypatch)
    line, content = mod._format_memory_entry("用户喜欢 Python 和 Rust", 0.92, 200, "suffix")
    assert line == "- [0.92] 用户喜欢 Python 和 Rust"
    assert content == "用户喜欢 Python 和 Rust"


def test_build_context_budget_and_guide(monkeypatch):
    """集成冒烟：多条长记忆 → 单条截断 + 总预算丢弃 + 注入末尾附工具指南。"""
    mod = _load_hook(monkeypatch)
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, Session, create_engine
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem
        for i in range(5):
            s.add(MemoryItem(id=f"mem_{i}", memory_type="semantic", key=f"k{i}",
                             content=f"记忆{i}：" + "很长的事实内容" * 20))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
            return [{"id": f"mem_{i}", "distance": 0.1 + i * 0.1} for i in range(5)]

    import lantai.storage.db as db_module
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_CHARS_PER_MEMORY", 50)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_TOTAL_CHARS", 120)

    out = mod.build_context("这是一个超过三字符的查询")
    assert "【记忆使用指南】" in out["context"]
    assert "已截断" in out["context"]
    assert len(out["evidence"]) < 5  # 总预算丢弃了部分记忆
    assert all(len(e["content"]) <= 50 for e in out["evidence"])
    assert out["event_id"] is not None
