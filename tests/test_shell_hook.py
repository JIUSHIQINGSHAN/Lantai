"""Shell Hook 契约测试：超时与静默降级。"""

import importlib.util
import io
import json
import os
import threading
import time

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


@pytest.fixture(scope="module", autouse=True)
def _warm_jieba_once():
    """jieba 冷启动（~0.9-1.4s）是进程级一次性成本，不属于 hook 超时语义。

    真实部署由 shell_hook._prewarm 启动时预热（票
    `.scratch/v0.65-next-window-2026-11/01` Slice 4）；测试进程对齐该形态，
    否则首测把模型加载算进超时断言（假阳性：3.6s > 3.0 阈值）。
    """
    try:
        import jieba

        jieba.lcut("预热")
    except Exception:
        pass


def _load_hook(monkeypatch, embed_delay=0.0, timeout=0.2):
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Patch settings timeout (singleton already loaded)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", timeout)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")

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
    mod = _load_hook(monkeypatch, timeout=0.2)

    # 闸门强制放行：排除 relevance_check 判 False → _persona_only() 返回 {} 的
    # 歧义——墙失效走正常路径时输出同样可能是 {}，`json.loads(out) == {}` 会
    # 把「降级」与「正常空结果」混为一谈。
    monkeypatch.setattr(mod, "relevance_check", lambda q, **kw: {"needs_memory": True})
    # intent 强制 fact_lookup：否则 conftest 假 chat_json 让 hybrid_search 在
    # intent 短路零命中（不调 embed）——「超时」只剩 jieba 冷启动的自然耗时来
    # 撞墙（热身后墙永不触发）。强制走检索路径后慢替身必被调、墙必触发。
    monkeypatch.setattr(
        "lantai.retrieval.intent.chat_json",
        lambda *a, **k: {"intent": "fact_lookup", "reason": "test"},
    )

    # 超时前提要真实：conftest autouse 假 embed 是瞬回的，build_context 会在
    # 0.2s 墙内跑完（v0.68 票 01 起完成路径的 event_id 可见，旧断言恰好遮住
    # 这一点）——把 hybrid 实际调用的 embed 换成慢替身，墙才真正触发降级。
    def _slow_hybrid_embed(texts, timeout=None):
        time.sleep(1.2)
        return [[0.1] * 8]

    monkeypatch.setattr("lantai.retrieval.hybrid.embed", _slow_hybrid_embed)
    # 「超时后 worker 落孤儿事件并收尾」是 test_hook_timeout_orphan_event 的专责；
    # 本用例的 5s 慢替身 daemon 线程在用例结束后醒来仍会 _try_log 落事件，
    # 跨测试污染 orphan 用例的事件计数——这里关掉落库。
    monkeypatch.setattr(mod, "_try_log", lambda *a, **k: None)
    threads_before = set(threading.enumerate())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"query": "这是一个超过三字符的查询"})))
    start = time.perf_counter()
    mod.main()  # 不再抛 SystemExit——超时静默返回 {}
    elapsed = time.perf_counter() - start
    out = capsys.readouterr().out.strip()
    # 降级签名：正常路径（含零命中分支）返回必带 event_id，{} 只可能来自
    # 无画像的 _query_fallback——墙失效走正常路径时这条红。
    assert json.loads(out) == {}
    # 双向钉死超时墙：下限——墙真的等了（≥0.2s，失效成「根本不等」时红）；
    # 上限——没等满 5s 慢替身（daemon 线程超时即弃，不拖测试）。
    assert 0.2 <= elapsed < 3.0, f"超时墙未按 0.2s 预算生效: {elapsed:.2f}s"

    # 收尾：等被放弃的 daemon worker 跑完（join 期间 _try_log patch 仍生效，
    # 零泄漏；否则 worker 在后续用例醒来落孤儿事件，污染 orphan 用例计数）。
    for t in set(threading.enumerate()) - threads_before:
        t.join(10)


def test_stdin_forced_utf8_reconfigure(monkeypatch):
    """回归：Windows GBK 环境下 stdin/stdout 必须被强制为 UTF-8，stderr 同钉
    且 errors=replace（票 30 A5：logger.exception 的异常 repr 嵌 GBK 外字符时
    不再崩日志丢行）。

    修复背景：Hermes 按 UTF-8 写 JSON（含中文 query），Python 默认按 GBK 解码
    →「你好」变「浣犲ソ」→ 检索零命中、注入静默失效。reconfigure 必须在读 stdin 前执行。
    """

    class FakeStream:
        def __init__(self):
            self.encoding = "gbk"
            self.errors = None

        def reconfigure(self, encoding=None, errors=None):
            self.encoding = encoding
            self.errors = errors

    fake_stdin = FakeStream()
    fake_stdout = FakeStream()
    fake_stderr = FakeStream()
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("sys.stderr", fake_stderr)
    _load_hook(monkeypatch)
    assert fake_stdin.encoding == "utf-8"
    assert fake_stdout.encoding == "utf-8"
    assert fake_stderr.encoding == "utf-8"
    assert fake_stderr.errors == "replace"


def test_handle_one_empty_returns_empty(monkeypatch):
    """serve 模式核心：单条处理对空输入/坏 JSON 返回 {}，不抛。"""
    mod = _load_hook(monkeypatch)
    assert mod._handle_one("") == {}
    assert mod._handle_one("not-json{{{") == {}
    assert mod._handle_one('{"query":""}') == {}


def test_handle_one_non_object_json_does_not_raise(monkeypatch):
    """非对象 JSON 帧静默降级为空（票 06 片 01 修复）。

    抽取前 `_handle_one` 对 `[1,2]`/`null`/`123`/`"str"` 会抛 AttributeError
    （对非 dict 调 `.get`）——serve 常驻 NDJSON 循环里一个畸形帧即打死进程。
    坏帧须降级不抛（宁 miss 不脏写）。
    """
    mod = _load_hook(monkeypatch)
    for raw in ("[1,2]", "null", "123", '"str"', "true"):
        assert mod._handle_one(raw) == {}, raw


def test_serve_mode_survives_malformed_frame(monkeypatch, capsys):
    """serve 模式遇畸形帧仍继续处理后续帧（进程不被打死）。"""
    mod = _load_hook(monkeypatch)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('null\n{"query":""}\nnot-json{{{\n{"type":"checkpoint"}\n'),
    )
    monkeypatch.setattr("sys.argv", ["shell_hook.py", "--serve"])
    mod.main()  # 不抛即通过；四个帧各自降级
    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    assert len(lines) == 4
    assert all(ln.startswith("{") for ln in lines)


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
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem

        s.add(
            MemoryItem(
                id="mem_1", memory_type="semantic", key="k", content="用户喜欢 Python 和 Rust"
            )
        )
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": "mem_1", "distance": 0.1}]

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _FakeStore())

    out = mod.build_context("这是一个超过三字符的查询")
    assert "依据" in out["context"]
    assert "mem_1" in out["context"]
    assert out["evidence"][0]["id"] == "mem_1"
    assert "用户喜欢" in out["evidence"][0]["content"]
    assert out["event_id"] is not None  # 回填通道不受影响


def test_build_context_no_hits_no_evidence(monkeypatch):
    """无命中：零侵入降级，context 不带依据段；event_id 照带出（v0.68 票 01）。"""
    mod = _load_hook(monkeypatch)

    class _EmptyStore:
        def search(self, qv, top_k=5, filters=None):
            return []

    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _EmptyStore())
    # event_id 断言要确定：检索事件落内存库（同文件 hit 例的手搓引擎风格）
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

    out = mod.build_context("这是一个超过三字符的查询")
    assert "依据" not in str(out.get("context") or "")
    assert out.get("event_id")  # 零命中事件也带 id——DLP 否决链的入口（v0.68 票 01）


def test_handle_dialogue_channel(monkeypatch):
    """serve 协议扩展（v0.5）：{"type":"dialogue"} 走对话写入通道。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch

    with patch(
        "lantai.ingestion.dialogue.ingest_dialogue",
        return_value={
            "ingested": True,
            "candidate_id": "cand_1",
            "fastpath": True,
            "lane": "general",
            "status": "fastpath",
        },
    ) as m:
        out = mod._handle_one('{"type":"dialogue","text":"记住：明天下午3点开会"}')
    assert out["ok"] is True
    assert out["candidate_id"] == "cand_1"
    m.assert_called_once_with("记住：明天下午3点开会", session_id="", turn=None)


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

    with patch("lantai.ingestion.dialogue.ingest_dialogue", side_effect=RuntimeError("boom")):
        out = mod._handle_one('{"type":"dialogue","text":"记住：明天开会"}')
    assert out == {}


# ── 来源链贯通 + 注入回执（P0 票02）────────────────────────────


def test_handle_dialogue_carries_session_and_turn(monkeypatch):
    """来源贯通：dialogue 行带 session_id/turn → 透传 ingest_dialogue。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch

    with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value={"ok": True}) as m:
        out = mod._handle_one(
            '{"type":"dialogue","text":"记住：开会","session_id":"sess_9","turn":7}'
        )
    assert out["ok"] is True
    m.assert_called_once_with("记住：开会", session_id="sess_9", turn=7)


def test_handle_dialogue_missing_origin_stays_empty(monkeypatch):
    """宁 miss 不脏写：无 session/turn → 留空/None，不补 0 不猜。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch

    with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value={"ok": True}) as m:
        mod._handle_one('{"type":"dialogue","text":"x"}')
    m.assert_called_once_with("x", session_id="", turn=None)


def test_handle_dialogue_invalid_turn_dropped(monkeypatch):
    """非法 turn（bool/字符串/负数）→ None，不落脏值。"""
    mod = _load_hook(monkeypatch)
    from unittest.mock import patch

    for raw_turn in ["3", True, -1, 1.5]:
        with patch("lantai.ingestion.dialogue.ingest_dialogue", return_value={"ok": True}) as m:
            mod._handle_one(
                f'{{"type":"dialogue","text":"x","session_id":"s","turn":{json.dumps(raw_turn)}}}'
            )
        assert m.call_args.kwargs["turn"] is None, raw_turn


def test_handle_backfill_writes_used_ids(param_env, monkeypatch):
    """回执通道（不 mock 冒烟）：真实内存库 backfill → RetrievalEvent.used_ids 落库。"""
    mod = _load_hook(monkeypatch)
    sf, _engine = param_env
    from lantai.models.tables import RetrievalEvent
    from lantai.observability.retrieval_log import log_retrieval

    event_id = log_retrieval("部署怎么做", [{"score": 0.9, "memory": {"id": "m1"}}], latency_ms=5)
    assert event_id
    out = mod._handle_one(
        json.dumps({"type": "backfill", "event_id": event_id, "used_ids": ["m1"]})
    )
    assert out["ok"] is True
    with sf() as s:
        ev = s.get(RetrievalEvent, event_id)
        assert ev is not None
        assert ev.used_ids == ["m1"]


def test_handle_backfill_invalid_silent(monkeypatch):
    """回执通道：缺 event_id/used_ids 非列表 → {} 零侵入。"""
    mod = _load_hook(monkeypatch)
    assert mod._handle_one('{"type":"backfill"}') == {}
    assert mod._handle_one('{"type":"backfill","event_id":"rev_x","used_ids":"m1"}') == {}


def test_dialogue_to_candidate_real_db(param_env, monkeypatch):
    """不 mock 冒烟：NDJSON dialogue 带来源 → 候选落 session_id + provenance.origin_turn。"""
    mod = _load_hook(monkeypatch)
    sf, _engine = param_env
    out = mod._handle_one(
        '{"type":"dialogue","text":"记住：周五下午3点发布 v0.23","session_id":"sess_smoke","turn":2}'
    )
    assert out.get("ok") is True
    from sqlmodel import select

    from lantai.models.tables import MemoryCandidate

    with sf() as s:
        cand = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.session_id == "sess_smoke")
        ).first()
        assert cand is not None
        prov = cand.provenance or {}
        assert prov.get("origin_turn") == 2


def test_build_context_passes_session_to_log(param_env, monkeypatch):
    """检索埋点透传 session_id（写线活性判据：带 session 的读才算真实会话读）。

    放宽 SHELL_HOOK_TIMEOUT（watch 2026-11-07）：本测试验证的是透传语义，
    不该依赖机器速度——全量后段进程重载时 2s 硬墙偶发擦线，`_run_with_timeout`
    走 `_query_fallback` 降级（只回画像、不调 log_retrieval），mock 未被调用
    即报 AttributeError。超时降级行为本身由 test_hook_timeout_budget.py 专门
    验证，这里隔离墙宽不是遮蔽信号。
    """
    mod = _load_hook(monkeypatch)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 30.0)
    from unittest.mock import patch

    with patch("lantai.observability.retrieval_log.log_retrieval", return_value="rev_x") as m:
        mod._handle_one('{"query":"部署怎么做","session_id":"sess_log"}')
    assert m.call_args.kwargs.get("session_id") == "sess_log"


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
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem

        for i in range(5):
            s.add(
                MemoryItem(
                    id=f"mem_{i}",
                    memory_type="semantic",
                    key=f"k{i}",
                    content=f"记忆{i}：" + "很长的事实内容" * 20,
                )
            )
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": f"mem_{i}", "distance": 0.1 + i * 0.1} for i in range(5)]

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_CHARS_PER_MEMORY", 50)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_TOTAL_CHARS", 120)

    out = mod.build_context("这是一个超过三字符的查询")
    assert "【记忆使用指南】" in out["context"]
    assert "已截断" in out["context"]
    assert len(out["evidence"]) < 5  # 总预算丢弃了部分记忆
    assert all(len(e["content"]) <= 50 for e in out["evidence"])
    assert out["event_id"] is not None
