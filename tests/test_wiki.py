"""记忆 Wiki 测试（借鉴 TencentDB Agent Memory LLM-Wiki ingest-v2 窄版落点）。

- slugify / render_scene_page / render_skill_page / render_wiki_index /
  render_overview_fallback：纯函数冒烟（不 mock）
- run_wiki_update_once：真实 SQLite + 真实 tmp_path 落盘（LLM 综述关闭/失败兜底）
- MCP wiki_read：真实 tmp_path 集成（下钻取页）
"""
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401
from lantai.models.tables import MemoryItem, MemoryScene

REPO_ROOT = Path(__file__).parent.parent
MCP_PATH = REPO_ROOT / "scripts" / "mcp_server.py"


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（wiki 全链路测试用）。"""
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

    from contextlib import contextmanager

    @contextmanager
    def _patch_session(session_factory):
        import lantai.storage.db as dbm
        original = dbm.get_session
        dbm.get_session = session_factory
        try:
            yield
        finally:
            dbm.get_session = original

    with _patch_session(lambda: Session(engine)):
        yield lambda: Session(engine), engine


def test_slugify():
    """纯函数冒烟：CJK/字母数字保留；空白折叠；非法输入兜底。"""
    from lantai.services.wiki_service import slugify
    assert slugify("部署上线") == "部署上线"
    assert slugify("Rust CLI 工具") == "Rust-CLI-工具"
    assert slugify("a  b") == "a-b"
    assert slugify("") == "page"
    assert slugify("..") == "page"
    assert slugify(None) == "page"


def test_render_scene_page_shape():
    """纯函数冒烟：frontmatter + 成员 + 相关场景 wikilink。"""
    from lantai.services.wiki_service import render_scene_page
    scene = MemoryScene(id="s1", name="部署", summary="上线流程总结", heat=3,
                        member_count=2, centroid=[1.0, 0.0, 0.0])
    members = [
        MemoryItem(id="m1", memory_type="semantic", key="k1",
                   content="用户喜欢 Python", lane="preference"),
        MemoryItem(id="m2", memory_type="semantic", key="k2",
                   content="部署用 systemd", lane="fact"),
    ]
    text = render_scene_page(scene, members, [("测试", 0.82)])
    assert text.startswith("---\ntype: scene\n")
    assert "title: 部署" in text
    assert "member_count: 2" in text
    assert "# 部署" in text
    assert "- [preference] k1: 用户喜欢 Python" in text
    assert "- [fact] k2: 部署用 systemd" in text
    assert "[[测试]]（相似度 0.82）" in text


def test_render_skill_page_shape():
    """纯函数冒烟：Skill 资产 → 描述 + 步骤 + 标签。"""
    from lantai.services.wiki_service import render_skill_page
    item = MemoryItem(
        id="sk1", memory_type="skill", key="h", content="技能",
        structure={"name": "记忆检索", "description": "三步检索",
                   "steps": ["查索引", "下钻", "回写"]},
        tags=["retrieval"])
    text = render_skill_page(item)
    assert text.startswith("---\ntype: skill\n")
    assert "title: 记忆检索" in text
    assert "## 步骤" in text
    assert "1. 查索引" in text and "3. 回写" in text
    assert "## 标签" in text and "retrieval" in text


def test_render_wiki_index_groups_and_sorts():
    """纯函数冒烟：按类型分组、同组按标题排序、输出稳定。"""
    from lantai.services.wiki_service import render_wiki_index
    briefs = [
        {"slug": "b", "title": "B 场景", "type": "scene", "description": "d1"},
        {"slug": "a", "title": "A 场景", "type": "scene", "description": ""},
        {"slug": "sk", "title": "技能", "type": "skill", "description": "s"},
    ]
    text = render_wiki_index(briefs)
    assert text.startswith("# 记忆 Wiki 索引")
    assert "共 3 页。" in text
    assert "## 场景" in text
    assert text.index("A 场景") < text.index("B 场景")
    assert "## 技能" in text
    assert "[A 场景](pages/a.md)" in text
    assert "[技能](pages/sk.md)" in text


def test_render_overview_fallback_wikilinks():
    """纯函数冒烟：确定性综述含统计与 [[wikilink]] 下钻。"""
    from lantai.services.wiki_service import render_overview_fallback
    briefs = [
        {"slug": "s1", "title": "部署", "type": "scene", "description": ""},
        {"slug": "s2", "title": "检索", "type": "scene", "description": ""},
        {"slug": "sk", "title": "记忆检索", "type": "skill", "description": "三步"},
    ]
    stats = {"scene_count": 2, "skill_count": 1, "member_count": 12,
             "top_scenes": [("部署", 5), ("检索", 3)]}
    text = render_overview_fallback(briefs, stats)
    assert "共 2 个场景、1 个技能、覆盖 12 条记忆" in text
    assert "[[部署]]（heat 5）" in text
    assert "[[记忆检索]]（三步）" in text


def test_run_wiki_update_once_writes_files(mem_db, monkeypatch, tmp_path):
    """集成：真实 SQLite + tmp_path——场景/技能 → 页面 + index + overview。"""
    import lantai.services.wiki_service as ws
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="s1", name="部署上线", summary="发布流程", heat=5,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(MemoryItem(id="m1", memory_type="semantic", key="k1",
                         content="发布用 systemd 单元", lane="fact",
                         status="active", scene_id="s1"))
        s.add(MemoryItem(id="sk1", memory_type="skill", key="h", content="技能",
                         status="active",
                         structure={"name": "记忆检索", "description": "三步",
                                    "steps": ["查", "下钻", "回写"]}))
        s.commit()
    monkeypatch.setattr(ws.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ws.settings, "WIKI_OVERVIEW_LLM", False)

    res = ws.run_wiki_update_once()
    assert res["ok"] is True
    assert res["pages"] == 2
    assert res["overview"] == "fallback"
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "overview.md").exists()
    page = tmp_path / "pages" / "部署上线.md"
    assert page.exists()
    assert "发布用 systemd 单元" in page.read_text(encoding="utf-8")
    index_text = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "部署上线" in index_text and "记忆检索" in index_text
    overview_text = (tmp_path / "overview.md").read_text(encoding="utf-8")
    assert "[[部署上线]]" in overview_text


def test_run_wiki_removes_stale_pages(mem_db, monkeypatch, tmp_path):
    """增量维护：场景删除后对应页自动清除（页随场景增删）。"""
    import lantai.services.wiki_service as ws
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="s1", name="旧场景", summary="", heat=1,
                          member_count=0, centroid=[1.0, 0.0, 0.0]))
        s.commit()
    monkeypatch.setattr(ws.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ws.settings, "WIKI_OVERVIEW_LLM", False)
    ws.run_wiki_update_once()
    assert (tmp_path / "pages" / "旧场景.md").exists()

    with session_factory() as s:
        s.add(MemoryScene(id="s2", name="新场景", summary="", heat=2,
                          member_count=0, centroid=[1.0, 0.0, 0.0]))
        s.delete(s.get(MemoryScene, "s1"))
        s.commit()
    res = ws.run_wiki_update_once()
    assert res["stale_removed"] == 1
    assert not (tmp_path / "pages" / "旧场景.md").exists()
    assert (tmp_path / "pages" / "新场景.md").exists()


def test_run_wiki_llm_overview_uses_llm(mem_db, monkeypatch, tmp_path):
    """LLM 综述：chat_json 被调用且输出入 overview（外部 LLM 允许 mock）。"""
    import lantai.services.wiki_service as ws
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="s1", name="场景甲", summary="s", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(MemoryItem(id="m1", memory_type="semantic", key="k", content="c",
                         lane="fact", status="active", scene_id="s1"))
        s.commit()
    monkeypatch.setattr(ws.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    from unittest.mock import patch
    with patch("lantai.llm.client.chat_json",
               return_value={"overview": "LLM 叙事综述 [[场景甲]]"}) as m:
        res = ws.run_wiki_update_once()
    m.assert_called_once()
    assert res["overview"] == "llm"
    overview_text = (tmp_path / "overview.md").read_text(encoding="utf-8")
    assert "LLM 叙事综述" in overview_text


def test_run_wiki_llm_failure_falls_back(mem_db, monkeypatch, tmp_path):
    """LLM 综述失败 → 确定性兜底（零侵入降级）。"""
    import lantai.services.wiki_service as ws
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="s1", name="场景甲", summary="s", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(MemoryItem(id="m1", memory_type="semantic", key="k", content="c",
                         lane="fact", status="active", scene_id="s1"))
        s.commit()
    monkeypatch.setattr(ws.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    from unittest.mock import patch
    with patch("lantai.llm.client.chat_json",
               side_effect=RuntimeError("llm down")):
        res = ws.run_wiki_update_once()
    assert res["overview"] == "fallback"
    assert (tmp_path / "overview.md").exists()


def test_mcp_wiki_read(mem_db, monkeypatch, tmp_path):
    """MCP 集成：wiki_read 按 slug 下钻取页；缺参/不存在 → 错误码。"""
    import lantai.services.wiki_service as ws
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(MemoryScene(id="s1", name="部署", summary="s", heat=1,
                          member_count=1, centroid=[1.0, 0.0, 0.0]))
        s.add(MemoryItem(id="m1", memory_type="semantic", key="k", content="c",
                         lane="fact", status="active", scene_id="s1"))
        s.commit()
    monkeypatch.setattr(ws.settings, "WIKI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ws.settings, "WIKI_OVERVIEW_LLM", False)
    ws.run_wiki_update_once()

    spec = importlib.util.spec_from_file_location("mcp_server_wiki", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    resp = mod.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "wiki_read",
                                  "arguments": {"slug": "部署"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["slug"] == "部署"
    assert "type: scene" in payload["content"]

    resp2 = mod.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "wiki_read", "arguments": {}}})
    assert resp2["error"]["code"] == -32602

    resp3 = mod.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "wiki_read",
                                   "arguments": {"slug": "不存在"}}})
    assert resp3["error"]["code"] == -32603