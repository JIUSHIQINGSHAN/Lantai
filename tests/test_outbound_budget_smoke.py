"""票 48：出站面四病——arxiv feedparser 二次抓取原语 + LLM 无时长预算 + RSS 限长事后检查 + arxiv 零限长。

`.scratch/v0.68-next-window-2026-11/issues/48-outbound-http-budget-gaps.md`

**先说影响**：①`arxiv.py` 把 `r.text`（str）喂 `feedparser.parse`——安装版
feedparser 对 str 且 scheme ∈ (http/https/ftp/file/feed) 会**自己用 urllib
发起第二次抓取**（无 timeout、无私网校验、file:// 在白名单，官方 docstring
明写勿传不可信 str）；②LLM 客户端未传 timeout/max_retries——SDK 默认
600s×2 次重试叠加 tenacity 3 连击，单次 chat_json 最坏 ≈90 分钟占死 ingest
job 与 4 许可并发闸；③`fetch_with_safety` 5MB 限长是**事后检查**——非流式
先把响应体完整读入内存再判 len，恶意源超时窗内可灌数十 GB（同进程 OOM）；
④arxiv `r.text` 全量入内存零限长 + `max_results` 取自 source config 任意大。

**mock 边界声明**（AGENTS.md 纪律）：`socket.getaddrinfo` 替身 = 网络边界
（test_ingest_adapter_smoke 先例）；arxiv 臂 `fetch_with_safety` 打桩 =
外部网络取源（票 69 RSS 臂豁免口径）；`feedparser.parse` 包一层类型捕获
wrapper、`httpx.get` 替身 = 观测边界（票面 TDD 1 明示允许面）；被测本体
（URL 组装/钳制/bytes 喂给/预算单发/流式断连）全部真实执行。流式臂本地
真 HTTP 服务器真 tracemalloc，`validate_fetch_url` 打桩仅因回环被真 guard
拒（回环拒绝另有过往臂 test_ssrf.py 实证）——本臂只测限长内存形状。
"""

from __future__ import annotations

import socket
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import feedparser
import pytest

from lantai.core.settings import settings

_ATOM = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom">'
    b"<title>arXiv Query</title><id>urn:arxiv</id>"
    b"<entry><id>urn:arxiv:id-1</id><title>t1</title>"
    b"<summary>paper body one</summary>"
    b"<author><name>A. Author</name></author>"
    b'<link href="https://arxiv.org/abs/1" rel="alternate" type="text/html"/>'
    b"</entry></feed>"
)


def _stub_dns(monkeypatch) -> None:
    """export.arxiv.org 解析替身（网络边界，先例同 test_ingest_adapter_smoke）。"""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port or 80))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


class TestArxivFeedparserPrimitive:
    """病灶 1：feedparser 对 str 输入自发起第二次抓取；生产缝必须喂 bytes。"""

    def test_feedparser_str_fetches_bytes_does_not(self):
        """真 feedparser 直测（危害文档锁，两态皆绿）：str 触发网络调用、bytes 不发。"""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            result: dict = {}

            def _parse_str():
                try:
                    result.update(feed=feedparser.parse(f"http://127.0.0.1:{port}/x.xml"))
                except Exception as exc:  # 本地监听不回话，urllib 侧炸掉即证据
                    result.update(err=repr(exc))

            t = threading.Thread(target=_parse_str, daemon=True)
            t.start()
            srv.settimeout(6)
            conn, _ = srv.accept()  # str 输入 → feedparser 自抓取连接抵达（红标行为本证）
            conn.close()
            t.join(6)
            assert "feed" in result or "err" in result, "feedparser 对 str URL 未发起第二次抓取？"

            feedparser.parse(_ATOM)  # bytes 输入：不发连接
            srv.settimeout(1.5)
            with pytest.raises(socket.timeout):
                srv.accept()  # bytes 形态零网络调用（对照）
        finally:
            srv.close()

    def test_arxiv_feeds_bytes_https_and_clamps_max_results(self, monkeypatch):
        """生产缝：fetch 后必须以 bytes 喂 feedparser；https + max_results 钳 ≤100。"""
        from lantai.ingestion import arxiv as arxiv_mod

        _stub_dns(monkeypatch)

        # 观测缝 = arxiv.fetch_with_safety（票 48 修复后的统一抓取出口；
        # 红态证据在 af5c9a15：修复前走 arxiv.httpx.get 且喂 str）
        fws_urls: list[str] = []
        monkeypatch.setattr(
            arxiv_mod, "fetch_with_safety", lambda url, **kw: (fws_urls.append(url), _ATOM)[1]
        )

        real_parse = arxiv_mod.feedparser.parse
        fed_types: list[type] = []

        def capturing_parse(data):
            fed_types.append(type(data))
            return real_parse(data)

        monkeypatch.setattr(arxiv_mod.feedparser, "parse", capturing_parse)

        docs = arxiv_mod.ArxivAdapter().fetch({"query": "cat:cs.IR", "max_results": 10**6})
        assert len(docs) == 1, docs
        # 病灶 1 断根：bytes 直喂，feedparser 永不进 URL 分支
        assert fed_types == [bytes], f"feedparser 收到非 bytes 实参：{fed_types}"
        # 病灶 1 顺带：明文 http → https
        assert fws_urls and all(u.startswith("https://export.arxiv.org") for u in fws_urls), (
            fws_urls
        )
        # 病灶 4：max_results 任意大整数钳制 ≤100
        assert all("max_results=100" in u for u in fws_urls), fws_urls

        fws_urls.clear()
        docs_lo = arxiv_mod.ArxivAdapter().fetch({"query": "cat:cs.AI", "max_results": 0})
        assert len(docs_lo) == 1
        # 下界同样钳（0/负数 → 1）
        assert fws_urls and all("max_results=1" in u for u in fws_urls), fws_urls


class TestLlmBudget:
    """病灶 2：SDK 默认 600s×2 重试 × tenacity 3 连击 ≈90 分钟 → 显式立法。"""

    def test_get_client_explicit_timeout_and_retries(self, monkeypatch):
        from lantai.core.settings import settings as _settings
        from lantai.llm import client as client_mod

        # conftest 会话把 base_url 指向 http://127.0.0.1:9 黑洞（过不了
        # validate_api_url 的 https 关）——本臂给一个 allowlist 内合法端点
        monkeypatch.setattr(_settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setattr(client_mod, "_client_instance", None)
        c = client_mod.get_client()
        assert c.timeout == _settings.LLM_TIMEOUT, f"timeout 未立法：{c.timeout!r}（SDK 默认 600s）"
        assert c.max_retries == _settings.LLM_MAX_RETRIES, (
            f"max_retries 未立法：{c.max_retries!r}（SDK 默认 2，与 tenacity 乘法放大）"
        )
        # SDK 层零重试是立法核心：应用层 tenacity 已有界，双层重试是乘法放大
        assert _settings.LLM_MAX_RETRIES == 0

    def test_chat_json_budget_mode_single_shot(self, monkeypatch):
        """预算模式（timeout 传导）单发不重试——embed D-04 同范式。

        conftest autouse 把 chat_json 模块属性顶成替身——本臂直测真实内部缝
        `_chat_once`（conftest 不触碰；红态该缝不存在即病：预算面未立法）。
        """
        from lantai.llm import client as client_mod

        once = getattr(client_mod, "_chat_once", None)
        assert once is not None, "预算单发缝 _chat_once 不存在（chat_json 无 timeout 传导）"

        calls = {"n": 0}

        class _FakeCompletions:
            def create(self, **kw):
                calls["n"] += 1
                raise RuntimeError("upstream hang")

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

            def with_options(self, **kw):
                return self

        monkeypatch.setattr(client_mod, "get_client", lambda: _FakeClient())
        with pytest.raises(RuntimeError):
            once("s", "u", timeout=1.0)
        assert calls["n"] == 1, f"预算模式仍重试：{calls['n']} 次调用（预算 × 重试 = 预算蒸发）"

    def test_extract_candidate_passes_ingest_deadline(self, monkeypatch):
        """ingest 提取链传导 per-call deadline（票面修复目标）。"""
        from lantai.core.settings import settings as _settings
        from lantai.parsing import extractor as extractor_mod

        seen: dict = {}

        def fake_chat_json(system, user, **kw):
            seen.update(kw)
            return {"summary": "s"}

        monkeypatch.setattr(extractor_mod, "chat_json", fake_chat_json)
        extractor_mod.extract_candidate("t", "c")
        assert seen.get("timeout") == _settings.LLM_INGEST_TIMEOUT, (
            f"提取链未传 per-call deadline：{seen}"
        )


class TestFetchStreamingCap:
    """病灶 3：限长从事后检查改流式断连——峰值内存必须有界。"""

    def test_stream_cap_aborts_and_peak_memory_bounded(self, monkeypatch):
        from lantai.ingestion import safety as safety_mod

        monkeypatch.setattr(safety_mod, "validate_fetch_url", lambda url: url)

        class _SlowDrip(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(10 * 1024 * 1024))
                self.end_headers()
                chunk = b"x" * 65536
                for _ in range(160):
                    try:
                        self.wfile.write(chunk)
                    except Exception:
                        return
                    time.sleep(0.01)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowDrip)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            tracemalloc.start()
            try:
                with pytest.raises(ValueError, match="too large"):
                    safety_mod.fetch_with_safety(f"http://127.0.0.1:{srv.server_address[1]}/big")
            finally:
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            # 修复前：非流式全量入内存 → peak ≥10MB（红）；修复后：超限即断，
            # 峰值 ≈ 上限 + 块缓冲
            assert peak < settings.SSRF_MAX_BYTES + 2 * 1024 * 1024, (
                f"限长仍是事后检查：峰值 {peak / 1048576:.1f}MB（10MB 响应全量入内存实锤）"
            )
        finally:
            srv.shutdown()
            srv.server_close()

    def test_small_body_passthrough_unchanged(self, monkeypatch):
        """对照锁：上限内响应体逐字返回，流式化不改语义。"""
        from lantai.ingestion import safety as safety_mod

        monkeypatch.setattr(safety_mod, "validate_fetch_url", lambda url: url)

        body = b"<rss><channel><title>ok</title></channel></rss>"

        class _Small(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Small)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = safety_mod.fetch_with_safety(f"http://127.0.0.1:{srv.server_address[1]}/small")
            assert out == body
        finally:
            srv.shutdown()
            srv.server_close()
