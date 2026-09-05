"""校雠两相位接线测试（ADR-0019）：余弦预筛 + 结构判类 + fastpath 带。

真实临时 SQLite + 真实 jieba 规则；仅 mock 外部：vector_store（外部存储）、
extractor chat_json（外部 LLM）、judge chat_json（外部 LLM）。
"""
import pytest
from sqlmodel import SQLModel, create_engine, Session
from sqlmodel.pool import StaticPool

from lantai.storage import db
from lantai.models.tables import MemoryItem, MemoryProposal
from lantai.core.ids import new_id
from lantai.models.schemas import AddMemoryReq
from lantai.services import memory_service


@pytest.fixture(name="env")
def env_fixture(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    db.engine = engine
    SQLModel.metadata.create_all(engine)

    def mock_search(content_or_vec, top_k=8, where=None):
        return getattr(mock_search, "results", [])

    monkeypatch.setattr("lantai.services.memory_service.vector_store.search", mock_search)
    # DD-01 修复后 _apply_dedup 先调 embed 再 search，需 mock embed 网络调用
    monkeypatch.setattr("lantai.services.memory_service.embed",
                        lambda texts: [[0.1] * 768 for _ in texts])
    monkeypatch.setattr(
        "lantai.parsing.extractor.chat_json",
        lambda *a, **kw: {"summary": "t", "claims": [], "methods": [],
                          "constraints": [], "actions": [], "topic": [],
                          "extractor_confidence": 0.5})
    monkeypatch.setattr(
        "lantai.llm.client.chat_json",
        lambda *a, **kw: {"relation": "update", "reason": "stub"})
    from types import SimpleNamespace
    yield SimpleNamespace(mock_search=mock_search)


def _seed(content: str, lane: str = "fact") -> str:
    with Session(db.engine) as s:
        mem = MemoryItem(
            id=new_id("mem"), memory_type="semantic", key=content[:20],
            content=content, lane=lane, status="active", importance=0.5)
        s.add(mem)
        s.commit()
        s.refresh(mem)
        return mem.id


def _req(content: str, lane: str = "fact") -> AddMemoryReq:
    return AddMemoryReq(title="x", content=content, lane=lane)


def test_prescreen_merge_short_circuits_without_extraction(env, monkeypatch):
    """提取路径：余弦 ≥ 0.95 → 直合，不调提取器（真重复零 LLM 成本）。"""
    mem_id = _seed("用户平时喜欢喝无糖咖啡")
    env.mock_search.results = [{"id": mem_id, "distance": 0.01}]  # sim=0.99
    called = {"n": 0}

    def boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("extractor must not be called at prescreen-merge")

    monkeypatch.setattr("lantai.services.memory_service.extract_candidate", boom)
    out = memory_service._create_candidate_with_extraction(_req("用户平时喜欢喝无糖咖啡"))
    assert out["dedup_action"] == "merge"
    assert out["target_memory_id"] == mem_id
    assert called["n"] == 0


def test_middle_band_structural_merge(env):
    """中带（0.65 ≤ sim < 0.95）：提取后结构判类 → 改写 merge。"""
    mem_id = _seed("用户平时喜欢喝无糖咖啡")
    env.mock_search.results = [{"id": mem_id, "distance": 0.1}]  # sim=0.9
    out = memory_service._create_candidate_with_extraction(_req("用户平时都爱喝无糖咖啡"))
    assert out["dedup_action"] == "merge"
    assert out["target_memory_id"] == mem_id


def test_middle_band_structural_update_proposal(env):
    """中带：值变更（日期）→ 结构判 update → 待审提案（有刹车）。"""
    mem_id = _seed("项目截止日期是3月15号")
    env.mock_search.results = [{"id": mem_id, "distance": 0.2}]  # sim=0.8
    out = memory_service._create_candidate_with_extraction(_req("项目截止日期推迟到4月1号"))
    assert out["dedup_action"] == "update"
    assert "proposal_id" in out
    with Session(db.engine) as s:
        prop = s.get(MemoryProposal, out["proposal_id"])
        assert prop is not None and prop.proposal_type == "update"


def test_middle_band_judge_failure_falls_back_to_insert(env, monkeypatch):
    """中带 judge 失败（LLM 故障）→ insert（宁 miss 不脏写，不吞不误写）。"""
    mem_id = _seed("项目使用Python开发")
    env.mock_search.results = [{"id": mem_id, "distance": 0.2}]  # sim=0.8 中带

    def boom(*a, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr("lantai.llm.client.chat_json", boom)
    out = memory_service._create_candidate_with_extraction(_req("项目改用Go语言重写"))
    assert "dedup_action" not in out
    assert "candidate_id" in out  # 正常建候选


def test_find_similar_direct_smoke(env):
    """find_similar 直调冒烟（零 mock）：真实 Session + 假 query_results 分带判定。

    核心决策函数直调（测试纪律）：fn 以 results 为入参，无需外部依赖。
    """
    from lantai.gate.dedup import find_similar
    mem_id = _seed("用户平时喜欢喝无糖咖啡")
    with Session(db.engine) as s:
        # fastpath：sim=0.95→merge；0.8→update；0.5→insert
        assert find_similar(s, [{"id": mem_id, "distance": 0.05}], fastpath=True)[0] == "merge"
        assert find_similar(s, [{"id": mem_id, "distance": 0.2}], fastpath=True)[0] == "update"
        assert find_similar(s, [{"id": mem_id, "distance": 0.5}], fastpath=True)[0] == "insert"
        # 提取路径：sim=0.98→merge；0.9→undecided（结构判别）；0.5→insert
        assert find_similar(s, [{"id": mem_id, "distance": 0.02}], fastpath=False)[0] == "merge"
        assert find_similar(s, [{"id": mem_id, "distance": 0.1}], fastpath=False)[0] == "undecided"
        assert find_similar(s, [{"id": mem_id, "distance": 0.5}], fastpath=False)[0] == "insert"
        # 幽灵 id（无 active 命中）→ insert
        assert find_similar(s, [{"id": "ghost", "distance": 0.1}])[0] == "insert"


def test_fastpath_merge_at_0_90(env):
    """fastpath 路径：sim=0.90 ≥ DEDUP_MERGE_THRESHOLD → merge（纯余弦）。"""
    mem_id = _seed("请记住我的名字是小明")
    env.mock_search.results = [{"id": mem_id, "distance": 0.1}]  # sim=0.9
    fp = {"topic": "t", "summary": "s", "claims": [], "methods": [],
          "constraints": [], "actions": [], "extractor_confidence": 1.0}
    out = memory_service._create_candidate_direct(_req("请记住我的名字是小明"), fp)
    assert out["dedup_action"] == "merge"
    assert out["target_memory_id"] == mem_id


def test_fastpath_update_proposal(env):
    """fastpath 路径：sim=0.85 ∈ [0.65, 0.90) → update 提案。"""
    mem_id = _seed("请记住我的名字是小明")
    env.mock_search.results = [{"id": mem_id, "distance": 0.15}]  # sim=0.85
    fp = {"topic": "t", "summary": "s", "claims": [], "methods": [],
          "constraints": [], "actions": [], "extractor_confidence": 1.0}
    out = memory_service._create_candidate_direct(_req("请记住我的名字是小红"), fp)
    assert out["dedup_action"] == "update"
    assert "proposal_id" in out


def test_fastpath_insert_passthrough(env):
    """fastpath 路径：sim < 0.65 → insert，正常建候选（fastpath 直写）。"""
    env.mock_search.results = [{"id": "ghost", "distance": 0.9}]  # sim=0.1 无 active 命中
    fp = {"topic": "t", "summary": "s", "claims": [], "methods": [],
          "constraints": [], "actions": [], "extractor_confidence": 1.0}
    out = memory_service._create_candidate_direct(_req("请记住我非常喜欢喝绿茶"), fp)
    assert "dedup_action" not in out
    assert out.get("fastpath") is True
