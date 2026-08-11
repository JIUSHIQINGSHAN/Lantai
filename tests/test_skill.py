"""Skill 资产化测试：proposer→promoter 结构沉淀 + shell_hook Skill 注入。

核心纯函数（_is_skill_item / _format_skill_entry）不 mock；
外部依赖（LLM 提取、embedding、向量存储）按测试纪律允许 mock。
"""
import importlib.util
import os

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal

HOOK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "shell_hook.py")


@pytest.fixture()
def mem_db():
    """内存 SQLite 真实建表（Skill 全链路测试用）。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with db_module_session_patch(session_factory):
        yield session_factory, engine


from contextlib import contextmanager


@contextmanager
def db_module_session_patch(session_factory):
    import lantai.storage.db as dbm
    original = dbm.get_session
    dbm.get_session = session_factory
    try:
        yield
    finally:
        dbm.get_session = original


def _load_hook(monkeypatch):
    spec = importlib.util.spec_from_file_location("shell_hook_skill", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", 0.2)
    return mod


def _skill_item(**kw):
    defaults = dict(
        id="mem_skill", memory_type="procedural", key="部署手册",
        content="上线部署流程",
        structure={"name": "部署手册", "description": "标准上线流程",
                   "steps": ["备份数据库", "发布代码", "验证健康"]},
        decay_class="procedural",
    )
    defaults.update(kw)
    return MemoryItem(**defaults)


def test_is_skill_item_true(monkeypatch):
    """纯函数冒烟：procedural + 步骤结构 → Skill。"""
    mod = _load_hook(monkeypatch)
    assert mod._is_skill_item(_skill_item()) is True


def test_is_skill_item_false_without_steps(monkeypatch):
    """纯函数冒烟：procedural 但无步骤结构 → 普通记忆。"""
    mod = _load_hook(monkeypatch)
    item = _skill_item(structure={})
    assert mod._is_skill_item(item) is False


def test_format_skill_entry_steps_numbered(monkeypatch):
    """纯函数冒烟：Skill 块含名称、描述与编号步骤。"""
    mod = _load_hook(monkeypatch)
    line, content = mod._format_skill_entry(
        _skill_item(), 0.92, 500, "suffix")
    assert line.startswith("## Skill: 部署手册 (score 0.92)")
    assert "标准上线流程" in line
    assert "1. 备份数据库" in line and "3. 验证健康" in line
    assert content == line  # evidence 与注入行同源


def test_format_skill_entry_truncation(monkeypatch):
    """纯函数冒烟：Skill 块超预算按码点截断并附后缀。"""
    mod = _load_hook(monkeypatch)
    line, _ = mod._format_skill_entry(
        _skill_item(), 0.92, 40, mod._RECALL_TRUNCATION_SUFFIX)
    assert line.endswith(mod._RECALL_TRUNCATION_SUFFIX)
    assert len(line) <= 40 + len(mod._RECALL_TRUNCATION_SUFFIX)


def test_propose_from_candidate_persists_structure(mem_db, monkeypatch):
    """写入侧：提取的 actions 随提案沉淀为 structure（不 mock 核心逻辑）。"""
    session_factory, _ = mem_db
    with session_factory() as s:
        cand = MemoryCandidate(
            id="cand_skill", document_id="doc_1", summary="上线部署步骤",
            claims=["按步骤部署"], actions=["备份数据库", "发布代码", "验证健康"],
            lane="general", status="new",
        )
        s.add(cand)
        s.commit()

    from unittest.mock import patch
    with patch("lantai.evolution.proposer.chat_json",
               return_value={"proposal_type": "add", "target_key": "部署手册",
                             "new_content": "上线部署步骤", "memory_type": "procedural",
                             "reason": "skill", "confidence": 0.9}):
        from lantai.evolution.proposer import propose_from_candidate
        prop = propose_from_candidate("cand_skill", {"decision": "promote_procedural"})

    assert prop.proposed_patch["structure"]["steps"] == [
        "备份数据库", "发布代码", "验证健康"]
    assert prop.proposed_patch["structure"]["name"] == "部署手册"
    assert prop.status == "pending"


def test_apply_proposal_persists_structure_and_procedural(mem_db, monkeypatch):
    """落库侧：structure 写入 MemoryItem，steps 存在 → procedural 永不衰减。"""
    session_factory, _ = mem_db
    with session_factory() as s:
        prop = MemoryProposal(
            id="prop_skill", proposal_type="add",
            evidence_ids=["doc_1"], reason="skill",
            proposed_patch={
                "memory_type": "procedural", "key": "部署手册",
                "content": "上线部署步骤", "lane": "general",
                "structure": {"name": "部署手册",
                              "steps": ["备份数据库", "发布代码", "验证健康"]},
            },
            confidence=0.9, status="pending",
        )
        s.add(prop)
        s.commit()

    from unittest.mock import Mock, patch
    with patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store",
               return_value=Mock(add=Mock(), delete=Mock())):
        from lantai.evolution.promoter import apply_proposal
        result = apply_proposal("prop_skill")

    assert result["ok"] is True
    with session_factory() as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "部署手册")).one()
        assert mem.structure["steps"] == ["备份数据库", "发布代码", "验证健康"]
        assert mem.decay_class == "procedural"


def test_build_context_injects_skill_block(mem_db, monkeypatch):
    """读取侧：Shell Hook 检索命中 Skill 记忆 → 注入 Skill 块而非平铺行。"""
    mod = _load_hook(monkeypatch)
    session_factory, _ = mem_db
    with session_factory() as s:
        s.add(_skill_item())
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
            return [{"id": "mem_skill", "distance": 0.1}]

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(mod, "get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr(mod, "embed", lambda texts: [[0.1] * 8])

    out = mod.build_context("上线部署怎么做")
    assert "## Skill: 部署手册" in out["context"]
    assert "1. 备份数据库" in out["context"]
    assert out["evidence"][0]["id"] == "mem_skill"