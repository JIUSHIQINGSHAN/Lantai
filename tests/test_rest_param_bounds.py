"""票 42:REST 面 limit/浮点参数边界裸奔组——422 拒绝 + conflicts SQL 下推 + 签名一致性哨兵。

红测纪律(AGENTS.md):真内存库 + 真 service 链(TestClient 直打路由、考功真落库),
不 mock 被测链路。界限与 MCP 孪生面对齐(mcp.py 的 [1,500] 家族为单一真源;
考功/知命 1000;checkpoint 列表 100;披沙批量 200)——修复前非法 limit 五种语义并存
(422 / 400 / 500 / Python 切片反转 / SQL 负值=不限),修复后统一 422 拒绝。

哨兵测试:AST 扫描 routes_*.py 全部 int/float 参数签名,ge+le 双界缺失即裸;
白名单逐条登记「他层已校验(注明 file:line 实证)」或「残余待立案(如实标注)」
——防新增路由带病上线(形态同票 26 双向对账哨兵思路)。
"""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

API_DIR = Path(__file__).parent.parent / "lantai" / "api"


@pytest.fixture()
def rest_env(monkeypatch):
    """REST 面内存库(同 test_param_boundary_clamps.rest_env 范式):db.get_session
    模块属性 patch + Depends(get_session) dependency_overrides 双机制挂同一引擎。
    本票涉及的全部 service 链都是纯库操作(考功/知命/冲突/底本/钩沉/磨勘),
    不触 embed/LLM——除内存库外零 mock。"""
    from fastapi import FastAPI

    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.api.routes_candidates import router as candidates_router
    from lantai.api.routes_cehou import router as cehou_router
    from lantai.api.routes_checkpoint import router as checkpoint_router
    from lantai.api.routes_conflicts import router as conflicts_router
    from lantai.api.routes_crystals import router as crystals_router
    from lantai.api.routes_edges import router as edges_router
    from lantai.api.routes_evolution import router as evolution_router
    from lantai.api.routes_gouchen import router as gouchen_router
    from lantai.api.routes_kaogong import router as kaogong_router
    from lantai.api.routes_mokan import router as mokan_router
    from lantai.api.routes_pisha import router as pisha_router
    from lantai.api.routes_sources import router as sources_router
    from lantai.api.routes_zhiming import router as zhiming_router
    from lantai.storage.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    def _override_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(db_module, "get_session", session_factory)

    app = FastAPI()
    for router in (
        kaogong_router,
        zhiming_router,
        candidates_router,
        pisha_router,
        checkpoint_router,
        crystals_router,
        evolution_router,
        sources_router,
        gouchen_router,
        mokan_router,
        conflicts_router,
        cehou_router,
        edges_router,
    ):
        app.include_router(router)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client, session_factory


def _seed_memory(
    s,
    mid,
    content,
    *,
    user_id="default",
    use_count=0,
    helpful_count=0,
    tier="working",
    importance=0.5,
):
    from lantai.models.tables import MemoryItem

    now = datetime.now(UTC)
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"k-{mid}",
            content=content,
            lane="fact",
            status="active",
            user_id=user_id,
            importance=importance,
            decay_score=1.0,
            decay_class="episodic",
            tier=tier,
            use_count=use_count,
            helpful_count=helpful_count,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(hours=1),
        )
    )


# ── 写副作用类:limit=-1 驱动全库考功写 ──


def test_kaogong_eval_limit_out_of_bounds_rejected_without_writes(rest_env):
    """limit=-1 修复前 200 且全库考功落库(SQLite LIMIT -1 = 无上限);修复后 422 零写。

    写副作用实证:种子记忆 use_count=4/helpful=4(采纳率 100% ≥ 0.8 上考线),
    修复前负 limit 会把它考功晋升(tier→longterm、importance+0.1)——拒绝墙必须
    挡住写,且界内合法链路照常晋升(墙没杀掉功能)。
    """
    from lantai.models.tables import MemoryItem

    client, session_factory = rest_env
    with session_factory() as s:
        _seed_memory(s, "kg1", "考功高采纳记忆", use_count=4, helpful_count=4)
        s.commit()

    resp = client.post("/kaogong/eval", json={"limit": -1})
    assert resp.status_code == 422
    # 毒化臂:被拒调用不得落任何考功写
    with session_factory() as s:
        row = s.get(MemoryItem, "kg1")
        assert row.tier == "working"
        assert row.importance == pytest.approx(0.5)

    # 上界臂:1001 拒,1000(考功/知命族界)过
    assert client.post("/kaogong/eval", json={"limit": 1001}).status_code == 422
    ok = client.post("/kaogong/eval", json={"limit": 1000})
    assert ok.status_code == 200
    assert ok.json()["evaluated"] == 1
    # 合法链路真写仍可达:界内全量跑完,上考晋升真实落库
    with session_factory() as s:
        row = s.get(MemoryItem, "kg1")
        assert row.tier == "longterm"
        assert row.importance == pytest.approx(0.6)


def test_lifecycle_audit_limit_out_of_bounds_rejected(rest_env):
    """limit=-1 修复前 200 全库巡检(auto_apply=true 时批量状态机写);修复后 422。"""
    client, session_factory = rest_env
    with session_factory() as s:
        _seed_memory(s, "zm1", "知命巡检记忆")
        s.commit()

    resp = client.post("/lifecycle/audit", json={"limit": -1, "auto_apply": True})
    assert resp.status_code == 422
    assert client.post("/lifecycle/audit", json={"limit": 1001}).status_code == 422
    # 边界回归臂:界内极值照常巡检
    assert client.post("/lifecycle/audit", json={"limit": 1000}).status_code == 200


# ── LLM 成本放大类:候选整批送 LLM / 模糊带浮点无界 ──


def test_candidates_pending_limit_bounds(rest_env):
    """limit=-1 修复前 200 全量(裁决入口正是全表捞重灾区);修复后 422,界 [1,500]。"""
    from lantai.models.tables import MemoryCandidate

    client, session_factory = rest_env
    now = datetime.now(UTC)
    with session_factory() as s:
        for i in range(3):
            s.add(
                MemoryCandidate(
                    id=f"cand-{i}",
                    document_id="d1",
                    summary=f"待审候选{i}",
                    status="pending_review",
                    user_id="default",
                    extractor_confidence=0.4,
                    review_due_at=now + timedelta(days=i),
                )
            )
        s.commit()

    resp = client.get("/candidates/pending", params={"limit": -1})
    assert resp.status_code == 422
    two = client.get("/candidates/pending", params={"limit": 2})
    assert two.status_code == 200
    assert len(two.json()["candidates"]) == 2
    assert client.get("/candidates/pending", params={"limit": 501}).status_code == 422
    assert client.get("/candidates/pending", params={"limit": 500}).status_code == 200


def test_candidates_ai_triage_limit_bounds_empty_library(rest_env):
    """ai_triage limit 无界修复前 200(空库红臂:候选为空则不触 LLM,锁参数面本身)。

    界 [1,500] 与 MCP 孪生 triage_analyze(mcp.py:478)同源——正文送 LLM 的
    成本放大面,孪生面早已钳制,REST 面补齐。
    """
    client, _ = rest_env
    assert client.post("/candidates/ai_triage", params={"limit": 0}).status_code == 422
    assert client.post("/candidates/ai_triage", params={"limit": -1}).status_code == 422
    assert client.post("/candidates/ai_triage", params={"limit": 501}).status_code == 422
    ok = client.post("/candidates/ai_triage", params={"limit": 500})
    assert ok.status_code == 200
    assert ok.json() == {"total": 0, "recommendations": []}


def test_batch_refine_and_pisha_float_and_limit_bounds(rest_env):
    """min_conf/max_conf 浮点无界 + limit 负值:修复前 200(空库不触 LLM),修复后 422。

    浮点界 [0,1] 对齐票 42 修复目标 2;limit [1,200] 对齐候选批量族。
    """
    client, _ = rest_env
    assert client.post("/candidates/batch_refine", params={"min_conf": -0.5}).status_code == 422
    assert client.post("/candidates/batch_refine", params={"max_conf": 1.5}).status_code == 422
    assert client.post("/candidates/batch_refine", params={"limit": 0}).status_code == 422
    ok = client.post("/candidates/batch_refine")
    assert ok.status_code == 200

    assert client.post("/pisha/batch", json={"min_conf": -0.5}).status_code == 422
    assert client.post("/pisha/batch", json={"max_conf": 2.0}).status_code == 422
    assert client.post("/pisha/batch", json={"limit": -1}).status_code == 422
    assert client.post("/pisha/batch", json={}).status_code == 200


# ── 只读 payload 放大类:checkpoint 完整行快照无限放大 ──


def test_checkpoint_list_limit_bounded(rest_env):
    """limit=-1 修复前 200 全量(checkpoint 的 before/after 是完整行快照,放大尤甚);
    修复后 422,界 [1,100];界内语义(version 降序截断)不变。"""
    from lantai.models.tables import MemoryCheckpoint

    client, session_factory = rest_env
    with session_factory() as s:
        _seed_memory(s, "cp1", "底本锚点记忆")
        for v in (1, 2, 3):
            s.add(
                MemoryCheckpoint(
                    id=f"ckpt-{v}",
                    memory_id="cp1",
                    version=v,
                    before={"content": f"v{v - 1}" if v > 1 else ""},
                    after={"content": f"v{v}"},
                )
            )
        s.commit()

    resp = client.get("/checkpoint", params={"memory_id": "cp1", "limit": -1})
    assert resp.status_code == 422
    two = client.get("/checkpoint", params={"memory_id": "cp1", "limit": 2})
    assert two.status_code == 200
    assert [c["version"] for c in two.json()["checkpoints"]] == [3, 2]
    assert client.get("/checkpoint", params={"memory_id": "cp1", "limit": 101}).status_code == 422
    assert client.get("/checkpoint", params={"memory_id": "cp1", "limit": 100}).status_code == 200


def test_checkpoint_cleanup_max_sessions_bounds(rest_env):
    """max_sessions=-1 修复前 500(service ValueError 未译,票 41 语义病同族);
    修复后 422(路由层先拒,service 层 >=1 校验保持为后盾),界 [1,500]。"""
    client, _ = rest_env
    assert client.post("/checkpoint/cleanup", params={"max_sessions": -1}).status_code == 422
    assert client.post("/checkpoint/cleanup", params={"max_sessions": 0}).status_code == 422
    assert client.post("/checkpoint/cleanup", params={"max_sessions": 501}).status_code == 422
    # 缺省(走 CHECKPOINT_MAX_SESSIONS 配置)照常
    assert client.post("/checkpoint/cleanup").status_code == 200


# ── conflicts:负值 Python 切片反转 + SQL 下推 ──


def test_conflicts_limit_sql_pushdown_and_bounds(rest_env):
    """limit=-5 修复前 200 且 rows[:-5] 切片反转(「去掉末尾5条」语义);修复后 422。

    下推后语义锁定:DEV viewer(default)只认自己记忆上的冲突;无主记忆上的
    事件(r4,时间最新)对 viewer 不可见——若下推把 limit 放在可见性过滤之前,
    r4 会顶替进结果,这条断言就红。limit=2 仍是时间降序取前 2。
    """
    from lantai.models.tables import ConflictEvent, MemoryItem

    client, session_factory = rest_env
    base = datetime.now(UTC)
    with session_factory() as s:
        _seed_memory(s, "cm-a", "甲的记忆", user_id="default")
        _seed_memory(s, "cm-b", "乙的记忆", user_id="default")
        _seed_memory(s, "cm-n", "无主记忆", user_id=None)
        for i, (mid, ref) in enumerate(
            [("cm-a", "r1"), ("cm-b", "r2"), ("cm-b", "r3"), ("cm-n", "r4")]
        ):
            s.add(
                ConflictEvent(
                    id=f"ce-{i}",
                    memory_id=mid,
                    incoming_ref=ref,
                    rule_name="mutex",
                    status="open",
                    created_at=base - timedelta(minutes=10 - i),
                )
            )
        s.commit()
        assert s.get(MemoryItem, "cm-a") is not None

    assert client.get("/conflicts", params={"limit": -5}).status_code == 422
    two = client.get("/conflicts", params={"limit": 2})
    assert two.status_code == 200
    events = two.json()["events"]
    # r4 挂在无主记忆上、时间最新——可见性过滤必须先于 limit 生效
    assert [e["incoming_ref"] for e in events] == ["r3", "r2"]
    full = client.get("/conflicts", params={"limit": 500})
    assert [e["incoming_ref"] for e in full.json()["events"]] == ["r3", "r2", "r1"]
    assert client.get("/conflicts", params={"limit": 501}).status_code == 422


def test_conflicts_pushdown_keeps_owner_scope(rest_env):
    """对照臂(下推前即绿):SQL 下推不得改可见性语义——非 admin 只见自己记忆上的
    冲突、NULL 属主不可见(宁 miss 不脏写)、admin 全权、principal=None 收敛 default。"""
    from lantai.core.auth import Principal
    from lantai.models.tables import ConflictEvent
    from lantai.services.conflict_service import list_conflict_events

    _, session_factory = rest_env
    base = datetime.now(UTC)
    with session_factory() as s:
        _seed_memory(s, "sc-a", "甲的记忆", user_id="a")
        _seed_memory(s, "sc-b", "乙的记忆", user_id="b")
        _seed_memory(s, "sc-n", "无主记忆", user_id=None)
        for i, mid in enumerate(["sc-a", "sc-b", "sc-b", "sc-n"]):
            s.add(
                ConflictEvent(
                    id=f"sce-{i}",
                    memory_id=mid,
                    incoming_ref=f"ref-{i}",
                    rule_name="mutex",
                    status="open",
                    created_at=base - timedelta(minutes=10 - i),
                )
            )
        s.commit()

    viewer_b = list_conflict_events(limit=10, principal=Principal(user_id="b"))
    assert [e["incoming_ref"] for e in viewer_b["events"]] == ["ref-2", "ref-1"]

    admin = list_conflict_events(limit=10, principal=Principal(user_id="x", role="admin"))
    assert len(admin["events"]) == 4

    # principal=None 收敛 "default":a/b 的行不可见,无主行也不可见
    internal = list_conflict_events(limit=10, principal=None)
    assert internal["events"] == []


# ── 钩沉/磨勘/只读列表 ──


def test_gouchen_bounds(rest_env):
    """min_sunken_score 无界修复前 200(阈值 1.5 = 永真沉没、-0.1 = 永不沉没);
    limit 负值在引擎里是 [:limit] 切片反转。修复后 422。"""
    client, _ = rest_env
    assert client.post("/gouchen/dredge", json={"min_sunken_score": 1.5}).status_code == 422
    assert client.post("/gouchen/dredge", json={"min_sunken_score": -0.1}).status_code == 422
    assert client.post("/gouchen/dredge", json={"limit": 0}).status_code == 422
    assert client.post("/gouchen/dredge", json={}).status_code == 200
    snap = {"anchor_time": "2026-01-01T00:00:00Z"}
    assert client.get("/gouchen/snapshot", params={**snap, "limit": -1}).status_code == 422
    assert client.get("/gouchen/snapshot", params={**snap, "limit": 500}).status_code == 200


def test_readonly_payload_limits(rest_env):
    """磨勘/提案/结晶/来源候选四只读面:limit=-1 修复前 200 全量,修复后 422(界 [1,500])。"""
    client, _ = rest_env
    assert client.get("/mokan/audit", params={"limit": -1}).status_code == 422
    assert client.get("/proposals", params={"limit": -1}).status_code == 422
    assert client.get("/crystals", params={"limit": -1}).status_code == 422
    assert client.get("/candidates", params={"status": "new", "limit": -1}).status_code == 422
    # 界内回归臂
    assert client.get("/mokan/audit", params={"limit": 500}).status_code == 200
    assert client.get("/proposals", params={"limit": 500}).status_code == 200
    assert client.get("/crystals", params={"limit": 500}).status_code == 200
    assert client.get("/candidates", params={"status": "new", "limit": 500}).status_code == 200


# ── 测候 mock_days:票 66 MCP 孪生臂的 REST 面补齐 ──


def test_cehou_mock_days_rest_twin_bounds(rest_env):
    """mock_days_observed=-1 修复前 200 且 row.days_observed 直接被写负值(cehou.py:348
    整列赋值);修复后 422 零染。上界 365 对齐票 66 MCP 臂(REST required_days 界 90
    的 4 倍余量,同一孪生契约)。"""
    from lantai.models.tables import CehouWindow

    client, session_factory = rest_env
    created = client.post(
        "/cehou/windows",
        json={"window_id": "w-t42", "target_type": "param", "target_id": "t1"},
    )
    assert created.status_code == 200

    assert (
        client.post(
            "/cehou/inspect", json={"window_id": "w-t42", "mock_days_observed": -1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/cehou/inspect", json={"window_id": "w-t42", "mock_days_observed": 366}
        ).status_code
        == 422
    )
    # 毒化臂:被拒调用不得写视窗状态
    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-t42")).one()
        assert row.days_observed == 0
        assert row.state == "observing"

    ok = client.post("/cehou/inspect", json={"window_id": "w-t42", "mock_days_observed": 365})
    assert ok.status_code == 200
    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-t42")).one()
        assert row.days_observed == 365
        assert row.state == "observing"


# ── 边写面 confidence + 格物投影孪生 ──


def test_edges_confidence_and_project_impact_bounds(rest_env):
    """confidence 越界修复前 200 且落库(写面毒值);修复后 422。
    project-impact max_depth 对齐票 39 反事实面单一真源 [1,COUNTERFACTUAL_MAX_DEPTH_LIMIT]。"""
    from lantai.models.tables import MemoryEdge

    client, session_factory = rest_env
    with session_factory() as s:
        _seed_memory(s, "e1", "边起点记忆")
        _seed_memory(s, "e2", "边终点记忆")
        s.commit()

    base_edge = {"source_memory_id": "e1", "target_memory_id": "e2", "relation": "supports"}
    assert client.post("/edges", json={**base_edge, "confidence": 1.5}).status_code == 422
    assert client.post("/edges", json={**base_edge, "confidence": -0.2}).status_code == 422
    ok = client.post("/edges", json={**base_edge, "confidence": 0.9})
    assert ok.status_code == 200
    with session_factory() as s:
        edges = s.exec(select(MemoryEdge)).all()
        assert len(edges) == 1
        assert edges[0].confidence == pytest.approx(0.9)

    assert (
        client.post("/edges/project-impact", json={"memory_id": "e1", "max_depth": 0}).status_code
        == 422
    )
    assert (
        client.post("/edges/project-impact", json={"memory_id": "e1", "max_depth": 11}).status_code
        == 422
    )
    assert (
        client.post(
            "/edges/project-impact", json={"memory_id": "e1", "min_confidence": 1.5}
        ).status_code
        == 422
    )
    assert client.post("/edges/project-impact", json={"memory_id": "e1"}).status_code == 200


# ── 一致性哨兵:routes_*.py 数值参数签名全量扫描 ──


def _numeric_annotation(ann: ast.expr) -> bool:
    """注解是否含 int/float(含 `int | None` 形态)。"""
    return any(isinstance(node, ast.Name) and node.id in ("int", "float") for node in ast.walk(ann))


def _has_ge_and_le(call: ast.expr | None) -> bool:
    """默认值是否为带 ge+le 双界的 Query(...)/Field(...) 调用。"""
    if not isinstance(call, ast.Call):
        return False
    if not (isinstance(call.func, ast.Name) and call.func.id in ("Query", "Field")):
        return False
    kws = {k.arg for k in call.keywords if k.arg}
    return "ge" in kws and "le" in kws


def _collect_bare_numeric_params() -> set[tuple[str, str, str]]:
    """扫描 routes_*.py:返回无 ge+le 双界的数值参数 (文件名, 属主, 参数名) 集合。

    覆盖两类签名:路由 handler 的参数(默认值须 Query 双界)与 pydantic 模型
    字段(默认值须 Field 双界)。下划线开头的函数是模块内部 helper,非路由面,跳过。
    """
    bare: set[tuple[str, str, str]] = set()
    for path in sorted(API_DIR.glob("routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.annotation, (ast.Name, ast.BinOp, ast.Subscript))
                        and _numeric_annotation(stmt.annotation)
                    ):
                        call = stmt.value if isinstance(stmt.value, ast.Call) else None
                        if not _has_ge_and_le(call):
                            bare.add((path.name, node.name, stmt.target.id))
            elif isinstance(node, ast.FunctionDef):
                if node.name.startswith("_"):
                    continue
                for arg in node.args.args:
                    if arg.annotation is None or not _numeric_annotation(arg.annotation):
                        continue
                    idx = node.args.args.index(arg)
                    defaults = node.args.defaults
                    off = len(node.args.args) - len(defaults)
                    default = defaults[idx - off] if idx >= off else None
                    if not _has_ge_and_le(default if isinstance(default, ast.Call) else None):
                        bare.add((path.name, node.name, arg.arg))
    return bare


# 白名单分两类,逐条人工核证(2026-11-09,票 42 实现轮):
# ① 他层已校验——service/ops 层有同语义界,签名裸但有真源(注明 file:line);
# ② 残余待立案——真实裸奔但非本票病族/需另行立案,如实登记不粉饰。
WHITELIST: dict[tuple[str, str, str], str] = {
    # ── ① 他层已校验(逐一实证)──
    ("routes_graph.py", "graph_route", "limit"): "ops/graph.py:26 validate_graph_limit [1,500]",
    ("routes_memory.py", "list_memories_route", "limit"): (
        "memory_service.py:790 build_memories_page [1,100],ValueError→422"
    ),
    ("routes_memory.py", "list_memories_route", "offset"): "memory_service.py:792 >=0",
    ("routes_recall_chain.py", "recall_chain_route", "max_depth"): (
        "ops/recall_chain.py:28 validate_chain_params [1,5]"
    ),
    ("routes_recall_chain.py", "recall_chain_route", "branch"): ("ops/recall_chain.py:30 [1,10]"),
    ("routes_recall_chain.py", "recall_chain_route", "min_score"): ("ops/recall_chain.py:32 [0,1]"),
    ("routes_recall_chain.py", "recall_chain_route", "total_max"): (
        "ops/recall_chain.py:38 [1,50]"
    ),
    ("routes_retrieval.py", "get_recall_report", "days"): (
        "observability/recall_report.py:77 [1,365],ValueError→400"
    ),
    ("routes_retrieval.py", "get_recent_events", "limit"): (
        "observability/recall_report.py:161 [1,100]"
    ),
    ("routes_scenes.py", "list_scenes", "limit"): "scene_service.py:375 [1,500]",
    # ── ② 残余待立案(真实裸奔,非本票病族或需专项)──
    ("routes_cehou.py", "SampleRequest", "latency_ms"): (
        "残余:票 66 MCP 臂拒非有限值,REST 孪生待收口(负有限值合法,ge/le 表达不了,"
        "需 allow_inf_nan 语义)"
    ),
    ("routes_cehou.py", "SampleRequest", "score_delta"): (
        "残余:同 latency_ms——负有限值合法(退步观测),非有限值拒绝待 REST 收口"
    ),
    ("routes_cognitive.py", "cognitive_summary", "max_rules"): "残余:渲染帽非 SQL/LLM 直传",
    ("routes_cognitive.py", "cognitive_summary", "max_failures"): "残余:渲染帽",
    ("routes_cognitive.py", "cognitive_summary", "max_probes"): "残余:渲染帽",
    ("routes_cognitive.py", "cognitive_summary", "char_budget"): "残余:渲染帽(提示预算)",
    ("routes_dialogue.py", "DialogueIngestReq", "turn"): "残余:序号参数已有 ge=0 下界,无上界语义",
    ("routes_edges.py", "decision_provenance_route", "max_depth"): (
        "残余:格物读路径遍历深度,票 39 家族孪生待收口"
    ),
    ("routes_edges.py", "event_timeline_route", "max_depth"): "残余:同上",
    ("routes_edges.py", "causal_chain_route", "max_depth"): "残余:同上",
    ("routes_evolution.py", "EpisodeFeedbackReq", "lam"): "残余:RL 超参写面,语义待查",
    ("routes_evolution.py", "EpisodeFeedbackReq", "gamma"): "残余:RL 超参写面",
    ("routes_memory.py", "search_archived_memories_route", "limit"): (
        "残余:chenfeng.search_archived 无校验实证(stmt.limit 直传),负值=SQLite 无上限"
    ),
    ("routes_memory.py", "search_archived_memories_route", "offset"): (
        "残余:同上(offset 负值 SQLite 语义未查)"
    ),
    ("routes_obsidian.py", "verbatim_search_route", "top_k"): (
        "残余:MCP 孪生已钳 [1,100](mcp.py:1369),REST 待收口"
    ),
    ("routes_param_advice.py", "list_suggestions", "offset"): "残余:分页偏移已有 ge=0",
    ("routes_param_advice.py", "list_overrides", "offset"): "残余:分页偏移已有 ge=0",
    ("routes_scenes.py", "rebuild_scenes", "threshold"): "残余:聚类阈值(None=配置默认),批处理面",
    ("routes_scenes.py", "assign_unassigned", "limit"): (
        "残余:消化期批处理面,MCP scenes_list 已钳 [1,500] 而此面未钳"
    ),
    ("routes_scenes.py", "assign_unassigned", "threshold"): "残余:聚类阈值",
    ("routes_scratchpad.py", "DistillScratchpadReq", "max_chars"): (
        "残余:票 68 位点 9 已登记——引擎硬编码 1000 忽略该参(死参)"
    ),
    ("routes_search.py", "GraphExpandReq", "top_k"): (
        "残余:双面皆裸,结构性帽 max_expanded=10(graph_retriever.py:49)兜底"
    ),
    ("routes_search.py", "GraphExpandReq", "max_hops"): "残余:同上(结构性帽兜底)",
    ("routes_search.py", "GraphExpandReq", "min_edge_conf"): "残余:双面皆裸",
    ("routes_terminal.py", "ChatReq", "top_k"): "残余:终局面 top_k",
    ("routes_terminal.py", "terminal_graph", "limit"): "残余:终局面 limit",
    ("routes_work_items.py", "list_work_items_route", "offset"): "残余:分页偏移已有 ge=0",
}


def test_rest_numeric_param_signature_sentinel():
    """哨兵:routes_*.py 全部数值参数必须 Query/Field ge+le 双界,或登记白名单。

    新增路由带裸数值参数 → 本测试红(白名单外逐个点名)。白名单是经核证的
    登记簿,不是豁免口:①类注明他层校验真源,②类如实标注「残余待立案」。
    """
    bare = _collect_bare_numeric_params()
    offenders = bare - set(WHITELIST)
    assert not offenders, (
        "裸数值参数(无 ge+le 双界且未登记白名单)——新路由带病上线或既有面被改坏:\n"
        + "\n".join(f"  {f} :: {owner} :: {param}" for f, owner, param in sorted(offenders))
    )
