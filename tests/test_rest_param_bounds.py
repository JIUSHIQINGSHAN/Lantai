"""票 42：limit/top_k 边界裸奔组——负值 limit 直通 SQL 的五种语义并存。

病灶（立案基线 `d2d784fe`，行号以现树为准）：SQLite ``LIMIT -1`` = 无上限、
``LIMIT 0`` = 空集。REST 面整型/浮点参数一路裸传 SQL 或 Python 切片——
写副作用类（考功/知命全库写）、LLM 成本类（候选整批送外呼）、payload 放大
类（checkpoint 完整行快照）三族无界，``/conflicts`` 还是先 ``.all()`` 全表
捞回再 ``rows[:limit]`` 切片（``limit=-5`` 语义是「去掉末尾 5 条」），
``/cognitive/context`` 的 ``top_k<=0`` 每切面静默空。本票把裸奔组对齐模范组
（``Query(ge=1, le=N)``，取值依据在票面 Answer 登记表）、conflicts 的 limit
下推 SQL、并上线 AST 一致性哨兵（裸数值参数即红，白名单 fail-closed 登记
service 层双层校验组与跨票归属）。

红测形态：TestClient 真路由 + 真服务链 + 真内存 SQLite（同
``test_rest_error_semantics.api_env`` 先例）。考功/知命周期为纯库内计算
无 LLM（``kaogong.py``/``zhiming.py`` 全文仅 import fence 做出闸包裹），
``limit=-1`` 驱动全库写的副作用可直接断言；哨兵为工单明示形态（扫描
routes_*.py 签名，非被测链路，无 mock 问题）。cognitive 路由的
``Depends(get_session)`` 是 import 期绑定对象，模块属性 patch 覆盖不到，
用 ``dependency_overrides`` 打同一函数对象。
"""

import ast
import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import ConflictEvent, MemoryCandidate, MemoryCheckpoint, MemoryItem


def _principal(user_id: str, *, role: str = "user"):
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=None, role=role)


@pytest.fixture()
def api_env():
    """真内存 SQLite + 真 app（依赖覆写身份），禁 mock 被测链路。"""
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
    ):
        yield session_factory


@contextlib.contextmanager
def _client(principal) -> TestClient:
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _seed_memory(session_factory, mem_id: str, user_id: str, content: str = "考功种子") -> None:
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mem_id,
                memory_type="semantic",
                key=f"k-{mem_id}",
                content=content,
                lane="work",
                user_id=user_id,
                status="active",
            )
        )
        s.commit()


def _seed_checkpoints(session_factory, mem_id: str, count: int = 3) -> None:
    with session_factory() as s:
        for i in range(count):
            s.add(
                MemoryCheckpoint(
                    id=f"ck-t42-{i}",
                    memory_id=mem_id,
                    version=i + 1,
                    before={"v": i},
                    after={"v": i + 1},
                )
            )
        s.commit()


# ── 病灶 1：写副作用类（limit=-1 驱动全库考功/巡检写）──


def test_kaogong_eval_negative_limit_writes_whole_db_then_422(api_env):
    """limit=-1 修复前对全库记忆执行考功并落库（红跑实测 200 + evaluated==3），
    修复后边界 422。"""
    for i in range(3):
        _seed_memory(api_env, f"m-kg-{i}", user_id="alice")
    with _client(_principal("alice")) as c:
        r = c.post("/kaogong/eval", json={"limit": -1})
    assert r.status_code == 422, f"负 limit 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


def test_lifecycle_audit_negative_limit_then_422(api_env):
    for i in range(3):
        _seed_memory(api_env, f"m-zm-{i}", user_id="alice")
    with _client(_principal("alice")) as c:
        r = c.post("/lifecycle/audit", json={"auto_apply": True, "limit": -1})
    assert r.status_code == 422, f"负 limit 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


# ── 病灶 3：payload 放大类（checkpoint 是完整行快照）──


def test_checkpoint_negative_limit_returns_all_rows_then_422(api_env):
    """红跑实测：3 条快照在库，limit=-1 修复前 200 且三条全回（放大实证）。"""
    _seed_memory(api_env, "m-ck", user_id="alice")
    _seed_checkpoints(api_env, "m-ck", count=3)
    with _client(_principal("alice")) as c:
        r = c.get("/checkpoint", params={"memory_id": "m-ck", "limit": -1})
    assert r.status_code == 422, f"负 limit 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


def test_candidates_pending_negative_limit_then_422(api_env):
    with api_env() as s:
        for i in range(3):
            s.add(
                MemoryCandidate(
                    id=f"cand-t42-{i}",
                    document_id="doc-t42",
                    user_id="alice",
                    status="pending_review",
                    summary=f"票42种子候选{i}",
                )
            )
        s.commit()
    with _client(_principal("alice")) as c:
        r = c.get("/candidates/pending", params={"limit": -1})
    assert r.status_code == 422, f"负 limit 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


# ── 病灶 4：conflicts 全表捞 + Python 切片 ──


def test_conflicts_negative_limit_becomes_422(api_env):
    """修复前 ``limit=-5`` 是「去掉末尾 5 条」的切片语义（200），修复后 422。"""
    with _client(_principal("alice")) as c:
        r = c.get("/conflicts", params={"limit": -5})
    assert r.status_code == 422, f"负 limit 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


def test_conflicts_limit_two_returns_two_after_pushdown(api_env):
    """下推不变量锁：``limit=2`` 在切片与 SQL 下推两种实现下都返回 2 条
    （现状即绿，如实申报——红测只锁行为不锁实现）。"""
    _seed_memory(api_env, "m-cf", user_id="alice")
    with api_env() as s:
        for i in range(3):
            s.add(
                ConflictEvent(
                    id=f"cf-t42-{i}",
                    memory_id="m-cf",
                    incoming_ref=f"冲突源{i}",
                )
            )
        s.commit()
    with _client(_principal("alice")) as c:
        r = c.get("/conflicts", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()["events"]) == 2


def test_conflicts_visible_filter_invariant_after_pushdown(api_env):
    """下推不变量锁：alice 的冲突对 bob 零可见、对 admin 全可见——可见性
    过滤从内存切片改为 ``in_`` 下推后行为等价（现状即绿）。"""
    _seed_memory(api_env, "m-cf2", user_id="alice")
    with api_env() as s:
        s.add(ConflictEvent(id="cf-t42-a", memory_id="m-cf2", incoming_ref="alice 的冲突"))
        s.commit()
    with _client(_principal("bob")) as c:
        r_bob = c.get("/conflicts", params={"limit": 50})
    with _client(_principal("root", role="admin")) as c:
        r_admin = c.get("/conflicts", params={"limit": 50})
    assert r_bob.status_code == 200
    assert [e["id"] for e in r_bob.json()["events"] if e["id"] == "cf-t42-a"] == []
    assert r_admin.status_code == 200
    assert "cf-t42-a" in [e["id"] for e in r_admin.json()["events"]]


# ── 病灶 5：/cognitive/context top_k ≤ 0 每切面静默空 ──


def test_cognitive_context_top_k_zero_then_422(api_env):
    """top_k=0 修复前 200 且每切面静默空（``len(target) >= top_k`` 恒真），
    修复后 422。"""
    from lantai.storage.db import get_session

    with _client(_principal("alice")) as c:
        c.app.dependency_overrides[get_session] = api_env
        try:
            r = c.get("/cognitive/context", params={"task": "部署知识", "top_k": 0})
        finally:
            c.app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 422, f"top_k=0 必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


# ── 病灶 2：浮点无界（越界置信度带静默空批）──


def test_pisha_batch_conf_out_of_range_then_422(api_env):
    with _client(_principal("alice")) as c:
        r = c.post("/pisha/batch", json={"min_conf": 1.5, "max_conf": 2.0})
    assert r.status_code == 422, f"越界置信度必须边界拒绝，实际 {r.status_code} {r.text[:200]}"


def test_gouchen_dredge_out_of_range_then_422(api_env):
    with _client(_principal("alice")) as c:
        r_limit = c.post("/gouchen/dredge", json={"limit": -1})
        r_conf = c.post("/gouchen/dredge", json={"min_sunken_score": 5.0})
    assert r_limit.status_code == 422, f"实际 {r_limit.status_code} {r_limit.text[:200]}"
    assert r_conf.status_code == 422, f"实际 {r_conf.status_code} {r_conf.text[:200]}"


def test_mokan_audit_negative_limit_then_422(api_env):
    with _client(_principal("alice")) as c:
        r = c.get("/mokan/audit", params={"limit": -1})
    assert r.status_code == 422, f"实际 {r.status_code} {r.text[:200]}"


# ── 修复目标 5：一致性哨兵（AST 扫描签名，白名单 fail-closed）──

ROUTES_DIR = Path("lantai/api")
_BOUND_KWARGS = {"ge", "gt", "le", "lt"}
_BUILDER_NAMES = {"Field", "Query", "Path", "Body"}

# 修复后仍裸的位点——每条必须带理由；键失效（位点消失/改名）即红（fail-closed，
# 同票 09 文档哨兵惯例）。两类：service/ops 层已有数值真源（双层校验），与
# 跨票归属（别票在案，此处只登记不修）。
_WHITELIST: dict[tuple[str, str, str], str] = {
    # service 层双层校验（票面对照组：模范真源）
    (
        "routes_memory.py",
        "list_memories_route",
        "limit",
    ): "memory_service.build_memories_page 1<=limit<=100 双层校验",
    (
        "routes_memory.py",
        "list_memories_route",
        "offset",
    ): "SQLite 负 OFFSET 语义=0，分页偏移非放大面",
    (
        "routes_memory.py",
        "search_archived_memories_route",
        "limit",
    ): "同 list_memories_route（build_memories_page）",
    ("routes_memory.py", "search_archived_memories_route", "offset"): "同 offset（SQLite 负值=0）",
    (
        "routes_graph.py",
        "graph_route",
        "limit",
    ): "路由体 validate_graph_limit [1,500]（ops/graph.py 单一真源）",
    (
        "routes_retrieval.py",
        "get_recent_events",
        "limit",
    ): "service recent_retrieval_events [1,100] 校验 + ValueError→400",
    (
        "routes_recall_chain.py",
        "recall_chain_route",
        "max_depth",
    ): "ops.validate_chain_params [1,5]",
    ("routes_recall_chain.py", "recall_chain_route", "branch"): "ops.validate_chain_params [1,10]",
    (
        "routes_recall_chain.py",
        "recall_chain_route",
        "min_score",
    ): "ops.validate_chain_params [0.0,1.0]",
    (
        "routes_recall_chain.py",
        "recall_chain_route",
        "total_max",
    ): "ops.validate_chain_params [1,50]",
    (
        "routes_scenes.py",
        "list_scenes",
        "limit",
    ): "scene_service.list_scenes [1,500] 校验（service 层单一真源）",
    # 图遍历有 visited 判活兜底（立案轮 6 判净），max_depth 无 payload 放大；
    # 链读面同源钳制归票 39 族跟进
    (
        "routes_edges.py",
        "decision_provenance_route",
        "max_depth",
    ): "图遍历 visited 兜底；链读面同源钳归票 39 族跟进",
    ("routes_edges.py", "event_timeline_route", "max_depth"): "同上",
    ("routes_edges.py", "causal_chain_route", "max_depth"): "同上",
    ("routes_edges.py", "EdgeReq", "confidence"): "边置信度语义参数 [0,1]；数值边界家族归票 68",
    # 跨票归属登记（已知裸，别票在案）
    (
        "routes_cehou.py",
        "SampleRequest",
        "latency_ms",
    ): "测候样本数值语义参数（非分页/预算面）；数值钳制族归票 66/68",
    ("routes_cehou.py", "SampleRequest", "score_delta"): "同上（score_delta 语义可负）",
    (
        "routes_search.py",
        "GraphExpandReq",
        "top_k",
    ): "票 40 子项 6 已实锤登记（GraphExpandReq 无界）",
    ("routes_search.py", "GraphExpandReq", "max_hops"): "同上",
    ("routes_search.py", "GraphExpandReq", "min_edge_conf"): "同上（概率浮点）",
    (
        "routes_evolution.py",
        "EpisodeFeedbackReq",
        "lam",
    ): "episode 只登记不接权重（察窗纪律）；数值钳制归票 68",
    ("routes_evolution.py", "EpisodeFeedbackReq", "gamma"): "同上",
}


def _has_bound(call: ast.Call) -> bool:
    return any(kw.arg in _BOUND_KWARGS for kw in call.keywords)


def _builder_with_bound(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _BUILDER_NAMES
        and _has_bound(node)
    )


def _annot_is_plain_num(ann: ast.expr | None) -> bool:
    return isinstance(ann, ast.Name) and ann.id in ("int", "float")


def _annotated_bound(ann: ast.expr) -> bool:
    """Annotated[int, Field(ge=...)] 形态即有约束。"""
    if (
        isinstance(ann, ast.Subscript)
        and isinstance(ann.value, ast.Name)
        and ann.value.id == "Annotated"
    ):
        sl = ann.slice
        elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        return any(isinstance(el, ast.Call) and _has_bound(el) for el in elts)
    return False


def _router_decorated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for d in node.decorator_list:
        if (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
        ):
            return True
        if (
            isinstance(d, ast.Attribute)
            and isinstance(d.value, ast.Name)
            and d.value.id == "router"
        ):
            return True
    return False


def _scan_param_bound_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(ROUTES_DIR.glob("routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if not isinstance(stmt, ast.AnnAssign):
                        continue
                    if not _annot_is_plain_num(stmt.annotation) or _annotated_bound(
                        stmt.annotation
                    ):
                        continue
                    key = (path.name, node.name, stmt.target.id)
                    if key in _WHITELIST:
                        continue
                    if stmt.value is None:
                        violations.append(f"{key[0]}::{key[1]}::{key[2]} (required)")
                        continue
                    if _builder_with_bound(stmt.value):
                        continue
                    violations.append(f"{key[0]}::{key[1]}::{key[2]} (pydantic field)")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _router_decorated(node):
                    continue
                args = node.args.args
                defaults = node.args.defaults
                for arg in args:
                    if not _annot_is_plain_num(arg.annotation):
                        continue
                    if (path.name, node.name, arg.arg) in _WHITELIST:
                        continue
                    idx_from_end = args[::-1].index(arg)
                    if idx_from_end >= len(defaults):
                        violations.append(f"{path.name}::{node.name}::{arg.arg} (required)")
                        continue
                    default = defaults[::-1][idx_from_end]
                    if isinstance(default, ast.Constant):
                        violations.append(f"{path.name}::{node.name}::{arg.arg} (bare-default)")
                        continue
                    if _builder_with_bound(default):
                        continue
                    violations.append(f"{path.name}::{node.name}::{arg.arg} (builder-no-bound)")
    return violations


def test_param_bound_sentinel_zero_violations_and_whitelist_valid():
    """裸数值参数即红；白名单键必须被扫描面上的实体消费（fail-closed 防漂移）。"""
    violations = _scan_param_bound_violations()
    assert violations == [], "裸 int/float 参数必须带数值约束或登记白名单:\n" + "\n".join(
        violations
    )
    consumed: set[tuple[str, str, str]] = set()
    for path in sorted(ROUTES_DIR.glob("routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        consumed.add((path.name, node.name, stmt.target.id))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args:
                    consumed.add((path.name, node.name, arg.arg))
    stale = [k for k in _WHITELIST if k not in consumed]
    assert stale == [], f"白名单键失效（位点已消失/改名），请清理: {stale}"
