"""票 66:MCP/REST 面参数钳制缺口家族——str 毒化 / NaN 直落 / 负值切片反转 / 裸 int。

红测纪律:真实 handler 链直调,不 mock 被测链路。MCP 面走 exec 副本完整 JSON-RPC
帧(错误码 -32602 vs -32603 的映射一并锁定——修复前 str 类型撞 TypeError 会落
-32603 internal error,修复后必须落 -32602 invalid params);REST 面走 TestClient。
边界值与各 REST 孪生面对齐(routes_cehou/routes_zuozheng/routes_famen/
routes_catalog/routes_qingniao/routes_qijuzhu 的 Query/Field 已立界限为单一真源)。
"""

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

MCP_PATH = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"

pytestmark = pytest.mark.mcp_full_surface()


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mcp_env(monkeypatch):
    """内存 SQLite 真实建表 + db.get_session 换内存库(同 test_mcp.mcp_env 范式)。

    本文件七类工具(cehou/qijuzhu/zuozheng/dossier/qingniao/muci/famen)全是
    纯库认知引擎,不触 embed/向量库——故除内存库外零 mock。
    """
    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    yield session_factory, engine


@pytest.fixture()
def qingniao_mailbox():
    """青鸟信箱是模块级 dict(v0.54 既定形态)——测前清空、测后复原防跨测试污染。"""
    from lantai.cognition.qingniao import _QINGNIAO_MAILBOX

    _QINGNIAO_MAILBOX.clear()
    yield _QINGNIAO_MAILBOX
    _QINGNIAO_MAILBOX.clear()


def _call_tool(mod, name, args):
    """正常调用:断言无 error 并解出 content JSON。"""
    resp = mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    assert "error" not in resp, resp.get("error")
    return json.loads(resp["result"]["content"][0]["text"])


def _err_code(mod, name, args):
    """恶意值调用:断言确有 error 并返回其 JSON-RPC 错误码。"""
    resp = mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    assert "error" in resp, f"恶意值 {args!r} 未被拒绝: {resp.get('result')}"
    return resp["error"]["code"]


def _seed_memory(s, mid, content, lane="fact", status="active", user_id="default"):
    from lantai.models.tables import MemoryItem

    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"k-{mid}",
            content=content,
            lane=lane,
            status=status,
            user_id=user_id,
            importance=0.5,
            decay_score=1.0,
            decay_class="episodic",
            use_count=0,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(hours=1),
        )
    )


# ── 位点 1/2:测候 cehou——str mock_days 毒化、NaN/inf 直落、required_days=0 绕闸 ──


def _cehou_create(mod, window_id, **overrides):
    args = {
        "create": True,
        "window_id": window_id,
        "target_type": "param",
        "target_id": "t1",
        "required_days": 7,
        "min_samples": 10,
    }
    args.update(overrides)
    return _call_tool(mod, "cehou_observe", args)


def test_cehou_mock_days_str_rejected_as_invalid_params(mcp_env):
    """str "5" 不得撞 TypeError 落 -32603:类型拒绝必须落 -32602 invalid params。"""
    session_factory, _ = mcp_env
    mod = _load_mcp()
    _cehou_create(mod, "w-str")
    code = _err_code(mod, "cehou_observe", {"window_id": "w-str", "mock_days_observed": "5"})
    assert code == -32602


def test_cehou_mock_days_negative_rejected_and_no_state_write(mcp_env):
    """负值 mock_days 修复前会带毒提交 days_observed=-1;拒绝后同一视窗必须干净。"""
    session_factory, _ = mcp_env
    mod = _load_mcp()
    _cehou_create(mod, "w-neg")
    code = _err_code(mod, "cehou_observe", {"window_id": "w-neg", "mock_days_observed": -1})
    assert code == -32602
    # 毒化臂:被拒调用不得落任何状态(先验后写),后续正常巡检不受染
    from lantai.models.tables import CehouWindow

    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-neg")).one()
        assert row.days_observed == 0
    out = _call_tool(mod, "cehou_observe", {"window_id": "w-neg"})
    assert out["days_observed"] == 0


def test_cehou_mock_days_upper_bound_rejected_valid_value_regression(mcp_env):
    """mock 上界 365(REST required_days 界 90 的 4 倍余量)外拒绝;界内语义零变化。"""
    mod = _load_mcp()
    _cehou_create(mod, "w-big")
    assert _err_code(mod, "cehou_observe", {"window_id": "w-big", "mock_days_observed": 366}) == -32602
    out = _call_tool(mod, "cehou_observe", {"window_id": "w-big", "mock_days_observed": 365})
    assert out["days_observed"] == 365


def test_cehou_score_delta_nonfinite_rejected_no_state_write(mcp_env):
    """inf/NaN 修复前直落累加器写库;拒绝后样本数必须仍为 0(不静默丢毒值)。"""
    session_factory, _ = mcp_env
    mod = _load_mcp()
    _cehou_create(mod, "w-nan")
    code_nan = _err_code(
        mod,
        "cehou_observe",
        {"record_sample": True, "window_id": "w-nan", "success": True, "score_delta": float("nan")},
    )
    code_inf = _err_code(
        mod,
        "cehou_observe",
        {
            "record_sample": True,
            "window_id": "w-nan",
            "success": True,
            "score_delta": 1e400,
        },
    )
    assert code_nan == -32602
    assert code_inf == -32602
    from lantai.models.tables import CehouWindow

    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-nan")).one()
        assert row.sample_count == 0
        assert row.total_score_delta == 0.0
    # 正常参数回归臂:合法有限值行为零变化
    _call_tool(
        mod,
        "cehou_observe",
        {"record_sample": True, "window_id": "w-nan", "success": True, "score_delta": 0.5},
    )
    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-nan")).one()
        assert row.sample_count == 1


def test_cehou_score_delta_negative_finite_accepted(mcp_env):
    """score_delta 允许负有限值(表现退步是合法观测),只拒非有限值——不静默修正。"""
    session_factory, _ = mcp_env
    mod = _load_mcp()
    _cehou_create(mod, "w-negfin")
    _call_tool(
        mod,
        "cehou_observe",
        {"record_sample": True, "window_id": "w-negfin", "success": True, "score_delta": -0.3},
    )
    from lantai.models.tables import CehouWindow

    with session_factory() as s:
        row = s.exec(select(CehouWindow).where(CehouWindow.window_id == "w-negfin")).one()
        assert row.total_score_delta == pytest.approx(-0.3)


def test_cehou_required_days_zero_and_over_bound_rejected(mcp_env):
    """required_days=0 绕按天观察闸、91 超 REST 孪生面 [1,90]:均须 -32602。"""
    mod = _load_mcp()
    assert _err_code(
        mod, "cehou_observe", dict(_cehou_create_args("w-zero"), required_days=0)
    ) == -32602
    assert _err_code(
        mod, "cehou_observe", dict(_cehou_create_args("w-over"), required_days=91)
    ) == -32602
    assert _err_code(
        mod, "cehou_observe", dict(_cehou_create_args("w-negs"), min_samples=0)
    ) == -32602
    assert _err_code(
        mod, "cehou_observe", dict(_cehou_create_args("w-overs"), min_samples=1001)
    ) == -32602
    # 边界回归臂:界内极值(90 天 / 1000 样本)照常建窗
    out = _cehou_create(mod, "w-bound", required_days=90, min_samples=1000)
    assert out["required_days"] == 90
    assert out["min_samples"] == 1000


def _cehou_create_args(window_id):
    return {
        "create": True,
        "window_id": window_id,
        "target_type": "param",
        "target_id": "t1",
        "required_days": 7,
        "min_samples": 10,
    }


# ── 位点 3:起居注 qijuzhu——裸 int、负 days 静默换轨、日期类型 ──


def test_qijuzhu_days_out_of_bounds_rejected(mcp_env):
    """days=-1 修复前静默换轨成单日纪事、days=31 无界放行:均须 -32602,界 [1,30]。"""
    mod = _load_mcp()
    assert _err_code(mod, "qijuzhu_chronicle", {"days": -1}) == -32602
    assert _err_code(mod, "qijuzhu_chronicle", {"days": 31}) == -32602
    out = _call_tool(mod, "qijuzhu_chronicle", {"days": 30, "limit": 200})
    assert out["days"] == 30


def test_qijuzhu_limit_out_of_bounds_rejected(mcp_env):
    """limit=-1 修复前直落 events[:-1] 切片反转:须 -32602,界 [1,200]。"""
    mod = _load_mcp()
    assert _err_code(mod, "qijuzhu_chronicle", {"days": 3, "limit": -1}) == -32602
    assert _err_code(mod, "qijuzhu_chronicle", {"days": 3, "limit": 201}) == -32602
    _call_tool(mod, "qijuzhu_chronicle", {"days": 3, "limit": 200})


def test_qijuzhu_date_type_rejected(mcp_env):
    """date=123(非 str)修复前撞 strptime TypeError 落 -32603:须 -32602。

    格式坏串("2026-13-99")修复前已落 -32602(strptime ValueError 落 ValueError 槽),
    本臂为回归锁:格式解析单一真源仍在引擎 strptime,不回退。
    """
    mod = _load_mcp()
    assert _err_code(mod, "qijuzhu_chronicle", {"date": 123}) == -32602
    assert _err_code(mod, "qijuzhu_chronicle", {"date": "2026-13-99"}) == -32602


# ── 位点 4:佐证 zuozheng——max_depth 无上界放大 BFS 扩散面 ──


def test_zuozheng_max_depth_bounds_and_type(mcp_env):
    """max_depth=10**6 修复前直接放大 BFS;str "2" 被静默 int() 修正:均须 -32602,界 [1,5]。"""
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "z1", "佐证锚点记忆")
        s.commit()
    mod = _load_mcp()
    assert _err_code(mod, "zuozheng_evidence", {"memory_id": "z1", "max_depth": 10**6}) == -32602
    assert _err_code(mod, "zuozheng_evidence", {"memory_id": "z1", "max_depth": 6}) == -32602
    assert _err_code(mod, "zuozheng_evidence", {"memory_id": "z1", "max_depth": "2"}) == -32602
    # 边界回归臂:界内值行为零变化
    _call_tool(mod, "zuozheng_evidence", {"memory_id": "z1", "max_depth": 5})


# ── 位点 5:卷宗 dossier——focus limit 负值切片反转、REST list 无界 ──


def test_dossier_focus_limit_negative_rejected(mcp_env):
    """limit=-1 修复前 focused_memories[:-1] 静默丢最后一条:须 -32602,界 [1,100]。"""
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m1", "卷宗记忆一")
        _seed_memory(s, "m2", "卷宗记忆二")
        s.commit()
    mod = _load_mcp()
    created = _call_tool(mod, "dossier_create", {"title": "迁移卷宗", "memory_ids": ["m1", "m2"]})
    dossier_id = created["id"]
    assert _err_code(mod, "dossier_focus", {"dossier_id": dossier_id, "limit": -1}) == -32602
    assert _err_code(mod, "dossier_focus", {"dossier_id": dossier_id, "limit": 101}) == -32602
    # 正常参数回归臂:界内值照常返回全部绑定记忆
    out = _call_tool(mod, "dossier_focus", {"dossier_id": dossier_id, "limit": 100})
    assert len(out["focused_memories"]) == 2


def test_dossier_focus_limit_str_rejected(mcp_env):
    """limit="20" 修复前被 int() 静默修正:类型拒绝须 -32602(宁 miss 不脏写)。"""
    session_factory, _ = mcp_env
    with session_factory() as s:
        _seed_memory(s, "m3", "卷宗记忆三")
        s.commit()
    mod = _load_mcp()
    created = _call_tool(mod, "dossier_create", {"title": "类型卷宗", "memory_ids": ["m3"]})
    assert _err_code(mod, "dossier_focus", {"dossier_id": created["id"], "limit": "20"}) == -32602


# ── 位点 6:青鸟 qingniao——inbox limit 负值切片反转 ──


def test_qingniao_inbox_limit_negative_rejected(mcp_env, qingniao_mailbox):
    """limit=-1 修复前 matched[:-1] 静默丢最后一封信:须 -32602,界 [1,100]。"""
    mod = _load_mcp()
    for i in range(2):
        _call_tool(
            mod,
            "qingniao_messenger",
            {
                "sender_agent_id": "a1",
                "receiver_agent_id": "b1",
                "subject": f"信{i}",
                "content": "正文",
            },
        )
    assert _err_code(mod, "qingniao_messenger", {"receiver_agent_id": "b1", "limit": -1}) == -32602
    assert _err_code(mod, "qingniao_messenger", {"receiver_agent_id": "b1", "limit": 101}) == -32602
    # 正常参数回归臂:界内值照常取回全部信件
    out = _call_tool(mod, "qingniao_messenger", {"receiver_agent_id": "b1", "limit": 100})
    assert out["total"] == 2


# ── 位点 7:目次/法门——负切片下界 ──


def test_muci_max_entries_bounds(mcp_env):
    """max_entries=-1/21 修复前直落 sec_items[:-1] 或无界放行:须 -32602,界 [1,20]。"""
    mod = _load_mcp()
    assert _err_code(mod, "muci_catalog", {"max_entries": -1}) == -32602
    assert _err_code(mod, "muci_catalog", {"max_entries": 21}) == -32602
    assert _err_code(mod, "muci_catalog", {"max_entries": "3"}) == -32602
    _call_tool(mod, "muci_catalog", {"max_entries": 20})


def test_famen_limit_bounds(mcp_env):
    """limit=-1/201 修复前直落 assets[:-1]:须 -32602,界 [1,200](REST 双面并集)。"""
    mod = _load_mcp()
    assert _err_code(mod, "famen_skill", {"limit": -1}) == -32602
    assert _err_code(mod, "famen_skill", {"limit": 201}) == -32602
    assert _err_code(mod, "famen_skill", {"limit": "5"}) == -32602
    _call_tool(mod, "famen_skill", {"limit": 200})


# ── 位点 8(REST):context top_k 无界 + qijuzhu 日期 500 面 + dossier list 无界 ──


@pytest.fixture()
def rest_env(monkeypatch):
    """REST 面内存库:Depends(get_session) 走 dependency_overrides,服务层直调走
    db 模块属性 patch——两机制都挂同一引擎,面面俱到。"""
    from fastapi import FastAPI

    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.api.routes_cognitive import router as cognitive_router
    from lantai.api.routes_dossiers import router as dossiers_router
    from lantai.api.routes_qijuzhu import router as qijuzhu_router
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
    app.include_router(cognitive_router)
    app.include_router(qijuzhu_router)
    app.include_router(dossiers_router)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client, session_factory


def test_rest_context_top_k_out_of_bounds_rejected(rest_env):
    """top_k=-1 修复前静默返回全空切面(len>=-1 恒真):须 422,界 [1,100]。"""
    client, session_factory = rest_env
    with session_factory() as s:
        _seed_memory(s, "rule_t66", "顶空规则记忆", lane="rule")
        s.commit()
    resp = client.get("/cognitive/context", params={"task": "部署", "top_k": -1})
    assert resp.status_code == 422
    resp = client.get("/cognitive/context", params={"task": "部署", "top_k": 0})
    assert resp.status_code == 422
    resp = client.get("/cognitive/context", params={"task": "部署", "top_k": 101})
    assert resp.status_code == 422
    resp = client.get("/cognitive/context/prompt", params={"task": "部署", "top_k": -1})
    assert resp.status_code == 422
    # 正常参数回归臂:界内值切面照常出内容
    resp = client.get("/cognitive/context", params={"task": "部署", "top_k": 12})
    assert resp.status_code == 200
    assert any(m["id"] == "rule_t66" for m in resp.json()["rules"])


def test_rest_qijuzhu_bad_date_422_not_500(rest_env):
    """坏日期修复前 strptime ValueError 冒泡成 500:须 422 错误回执。"""
    client, _ = rest_env
    resp = client.get("/qijuzhu/dates/2026-13-99")
    assert resp.status_code == 422
    # 正常参数回归臂:合法日期行为零变化(空库返回纪事结构)
    resp = client.get("/qijuzhu/dates/2026-01-15")
    assert resp.status_code == 200


def test_rest_dossier_list_limit_out_of_bounds_rejected(rest_env):
    """GET /dossiers?limit=-1 修复前无界直通(负值=无限):须 422,界 [1,200]。"""
    client, _ = rest_env
    resp = client.get("/dossiers", params={"limit": -1})
    assert resp.status_code == 422
    resp = client.get("/dossiers", params={"limit": 201})
    assert resp.status_code == 422
    resp = client.get("/dossiers", params={"limit": 200})
    assert resp.status_code == 200
