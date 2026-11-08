"""票 66：MCP 面参数钳制缺口家族——红测。

每位点真 handler/service/engine 链直调传恶意值（真内存库，不 mock 被测链路），
修复前 500/毒化/无限（红），修复后 ValueError（MCP dispatch → -32602）或
REST 422（绿）。合法参数行为零变化（现状锁臂如实申报）。

病灶与对齐界（双面同钳，REST 既有界为真源）：
- 测候 create required_days/min_samples：REST Field [1,90]/[1,1000]，MCP 裸 int
- 测候 record_sample latency/score_delta：inf/NaN 直落 REAL 列永久毒化累计值
- 测候 inspect mock_days_observed：str → TypeError 炸裁决比较，越界 int 伪造毕业
- 起居注 days/limit：REST Query [1,30]/[1,200]，MCP 裸 int()；date 裸 strptime
- 佐证 max_depth/limit：REST [1,5]/[1,20]，MCP 裸 int() 无上界（BFS 扩散面）
- 卷宗 focus limit：DossierFocusReq [1,100]，MCP 裸 int()（负值=无限）
- 青鸟 inbox limit：REST [1,100]，MCP 裸 int()（负值=无限拉取）
- 法门 limit：REST [1,20]/[1,200]，MCP 裸 int()（负切片语义反转）
- 目次 max_entries：REST [1,20]，MCP 裸 int()（负切片语义反转）
- REST 起居注 date 路由：strptime ValueError → 500（票 41 同病：可校验输入炸 500）
- REST 测候 sample：pydantic float 无 allow_inf_nan=False，JSON Infinity 可入库
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cli.mcp import (
    handle_cehou_observe,
    handle_dossier_focus,
    handle_famen_skill,
    handle_muci_catalog,
    handle_qijuzhu_chronicle,
    handle_qingniao_messenger,
    handle_zuozheng_evidence,
)
from lantai.models.tables import MemoryItem, SkillCrystal


@pytest.fixture()
def mem_db():
    """真 SQLite 内存库 + get_session 指向它（不 mock 计算逻辑）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    import lantai.storage.fts as fts

    with eng.connect() as conn:
        fts.init_fts(conn.connection.driver_connection)

    original = db_module.get_session

    def session_factory() -> Session:
        return Session(eng)

    db_module.get_session = session_factory
    yield eng
    db_module.get_session = original


def _create_window(window_id: str, **kw) -> dict:
    params = {
        "create": True,
        "window_id": window_id,
        "target_type": "param",
        "target_id": "t-1",
    }
    params.update(kw)
    return handle_cehou_observe(params)


# ─────────────────────────────────────────────
# 测候 cehou：观察窗闸门参数 + 样本数值面
# ─────────────────────────────────────────────


class TestCehouParamGaps:
    def test_mock_days_str_rejected_and_window_survives(self, mem_db):
        """str mock_days 修复前 TypeError（-32603 兜底）；修复后 ValueError
        （-32602），且拒绝发生在任何行状态变更之前——后续正常巡检不炸。"""
        _create_window("w-mock")
        with pytest.raises(ValueError):
            handle_cehou_observe({"window_id": "w-mock", "mock_days_observed": "5"})
        # 毒化臂：被拒后同窗正常巡检（修复前 str 已炸穿，本断言不可达）
        res = handle_cehou_observe({"window_id": "w-mock"})
        assert res.get("state") == "observing"

    def test_score_delta_nonfinite_rejected(self, mem_db):
        """1e400→inf / NaN 直落累计值修复前提交成功（永久毒化），修复后拒绝。"""
        _create_window("w-poison")
        with pytest.raises(ValueError):
            handle_cehou_observe(
                {
                    "record_sample": True,
                    "window_id": "w-poison",
                    "score_delta": 1e400,
                }
            )
        with pytest.raises(ValueError):
            handle_cehou_observe(
                {
                    "record_sample": True,
                    "window_id": "w-poison",
                    "score_delta": float("nan"),
                }
            )

    def test_latency_negative_and_nonfinite_rejected(self, mem_db):
        """负耗时与 inf 耗时都是无意义样本，修复前照单全收。"""
        _create_window("w-lat")
        with pytest.raises(ValueError):
            handle_cehou_observe(
                {"record_sample": True, "window_id": "w-lat", "latency_ms": -1.0}
            )
        with pytest.raises(ValueError):
            handle_cehou_observe(
                {"record_sample": True, "window_id": "w-lat", "latency_ms": 1e400}
            )

    def test_required_days_zero_rejected(self, mem_db):
        """0/负 required_days 修复前建窗成功——days>=0 恒真，观察窗闸门形同虚设。"""
        with pytest.raises(ValueError):
            _create_window("w-zero", required_days=0)
        with pytest.raises(ValueError):
            _create_window("w-neg", required_days=-3)
        with pytest.raises(ValueError):
            _create_window("w-szero", min_samples=0)

    def test_valid_params_unchanged(self, mem_db):
        """现状锁：合法参数全链行为零变化（create→sample→inspect→毕业判定路径）。"""
        res = _create_window("w-ok", required_days=7, min_samples=10)
        assert res.get("window_id") == "w-ok" or res.get("state") == "observing"
        rec = handle_cehou_observe(
            {
                "record_sample": True,
                "window_id": "w-ok",
                "success": True,
                "latency_ms": 120.5,
                "score_delta": 0.5,
            }
        )
        assert rec.get("success") is True
        ins = handle_cehou_observe({"window_id": "w-ok", "mock_days_observed": 3})
        assert ins.get("state") == "observing"
        assert ins.get("days_observed") == 3


# ─────────────────────────────────────────────
# 起居注 qijuzhu：days/limit/date 面
# ─────────────────────────────────────────────


class TestQijuzhuParamGaps:
    def test_days_out_of_bounds_rejected(self, mem_db):
        """days=0/-3/31 修复前静默错形（0=今日窗、负=未来窗、31 超 REST 上界），
        修复后 ValueError（对齐 REST Query [1,30]）。"""
        for bad in (0, -3, 31):
            with pytest.raises(ValueError):
                handle_qijuzhu_chronicle({"days": bad})

    def test_limit_out_of_bounds_rejected(self, mem_db):
        """limit=-1 修复前负切片/无限语义，修复后 ValueError（对齐 REST [1,200]）。"""
        for bad in (-1, 0, 201):
            with pytest.raises(ValueError):
                handle_qijuzhu_chronicle({"days": 7, "limit": bad})

    def test_bad_date_clear_message(self, mem_db):
        """裸 strptime 修复前报「time data ... does not match」（调用方无从自查），
        修复后 ValueError 带 YYYY-MM-DD 指引。"""
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            handle_qijuzhu_chronicle({"date": "2026-13-99"})

    def test_valid_params_unchanged(self, mem_db):
        """现状锁：合法 days/limit/缺省 date 行为零变化。"""
        res = handle_qijuzhu_chronicle({"days": 7, "limit": 50})
        assert isinstance(res, dict)


# ─────────────────────────────────────────────
# 佐证 zuozheng：max_depth/limit 面
# ─────────────────────────────────────────────


class TestZuozhengParamGaps:
    def _seed_memory(self, mem_db, mid: str) -> None:
        with Session(mem_db) as s:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"key-{mid}",
                    content=f"{mid} 的正文断言",
                    namespace="default",
                    status="active",
                    tier="working",
                    importance=0.9,
                    confidence=1.0,
                    reason="",
                    role="OBSERVATION",
                    lane="general",
                    domain="general",
                    version=1,
                )
            )
            s.commit()

    def test_max_depth_unbounded_rejected(self, mem_db):
        """max_depth=10**6 修复前直通 BFS 扩散面，修复后 ValueError（对齐 REST [1,5]）。"""
        self._seed_memory(mem_db, "mem-zz")
        with pytest.raises(ValueError):
            handle_zuozheng_evidence({"memory_id": "mem-zz", "max_depth": 10**6})
        with pytest.raises(ValueError):
            handle_zuozheng_evidence({"memory_id": "mem-zz", "max_depth": 0})

    def test_limit_out_of_bounds_rejected(self, mem_db):
        """limit 越界修复后 ValueError（对齐 REST [1,20]）。"""
        self._seed_memory(mem_db, "mem-zz2")
        for bad in (0, 21):
            with pytest.raises(ValueError):
                handle_zuozheng_evidence({"claim": "某断言", "limit": bad})

    def test_valid_params_unchanged(self, mem_db):
        """现状锁：默认 max_depth/limit 行为零变化。"""
        self._seed_memory(mem_db, "mem-zz3")
        res = handle_zuozheng_evidence({"memory_id": "mem-zz3"})
        assert isinstance(res, dict)


# ─────────────────────────────────────────────
# 卷宗 dossier / 青鸟 qingniao：limit 面
# ─────────────────────────────────────────────


class TestDossierQingniaoParamGaps:
    def test_dossier_focus_limit_bounded(self, mem_db):
        """focus limit=-1 修复前 focused_memories[:-1] 负切片（绑定 1 条记忆时
        静默返 0 条），修复后 ValueError（对齐 DossierFocusReq [1,100]）。
        红证：必须先建真卷宗——不存在的 dossier_id 走 service 的 not-found
        ValueError，会让本臂在修复前假绿。"""
        from lantai.services.dossier_service import create_dossier_service

        dossier = create_dossier_service(
            title="票66红测卷宗", goal="验证 limit 边界", principal=None
        )
        did = dossier["id"] if isinstance(dossier, dict) and "id" in dossier else dossier.get(
            "dossier_id"
        )
        for bad in (-1, 0):
            with pytest.raises(ValueError):
                handle_dossier_focus({"dossier_id": did, "limit": bad})

    def test_qingniao_limit_bounded(self, mem_db):
        """inbox limit=-1 修复前无限拉取（SQLite LIMIT -1 语义），修复后
        ValueError（对齐 REST [1,100]）。"""
        with pytest.raises(ValueError):
            handle_qingniao_messenger(
                {"receiver_agent_id": "agent-b", "limit": -1}
            )
        with pytest.raises(ValueError):
            handle_qingniao_messenger({"receiver_agent_id": "agent-b", "limit": 0})


# ─────────────────────────────────────────────
# 法门 famen / 目次 muci：负切片面
# ─────────────────────────────────────────────


class TestFamenMuciParamGaps:
    def _seed_skill(self, mem_db, name: str) -> None:
        with Session(mem_db) as s:
            s.add(
                SkillCrystal(
                    id=f"sc-{name}",
                    skill_name=name,
                    procedure=json.dumps([{"step": f"{name} 的步骤"}], ensure_ascii=False),
                    trigger_rule="当提到 " + name,
                    status="approved",
                    user_id="default",
                )
            )
            s.commit()

    def test_famen_negative_limit_rejected(self, mem_db):
        """limit=-1 修复前 assets[:-1] 切掉末尾（红证：两条技能只剩一条，
        静默错形非报错），修复后 ValueError（对齐 REST [1,200]）。"""
        self._seed_skill(mem_db, "skill-a")
        self._seed_skill(mem_db, "skill-b")
        for bad in (-1, 0):
            with pytest.raises(ValueError):
                handle_famen_skill({"limit": bad})

    def test_muci_negative_max_entries_rejected(self, mem_db):
        """max_entries=-1 修复前 sec_items[:-1] 静默错形（红证：单条目板块
        full 模式返 0 条），修复后 ValueError（对齐 REST [1,20]）。"""
        with Session(mem_db) as s:
            s.add(
                MemoryItem(
                    id="mem-muci-1",
                    memory_type="semantic",
                    key="key-muci",
                    content="目次板块正文一",
                    namespace="default",
                    status="active",
                    tier="working",
                    importance=0.9,
                    confidence=1.0,
                    reason="",
                    role="OBSERVATION",
                    lane="general",
                    domain="general",
                    version=1,
                )
            )
            s.commit()
        with pytest.raises(ValueError):
            handle_muci_catalog({"detail_level": "full", "max_entries": -1})


# ─────────────────────────────────────────────
# REST 面：起居注 date 500 病 + 测候非有限 float
# ─────────────────────────────────────────────


@pytest.fixture()
def rest_client(mem_db):
    """TestClient + 真 SQLite（get_session 模块补丁已由 mem_db 挂好）+
    身份 override（FastAPI Depends(get_session) 是 import 期绑定，模块补丁
    够不着它——票 42 同款双层缝）。"""
    from lantai.api.app import app
    from lantai.core.auth import get_current_user
    from lantai.core.auth import Principal
    from lantai.storage.db import get_session

    def _factory() -> Session:
        return Session(mem_db)

    def _user():
        return Principal(
            user_id="default",
            tenant_id=None,
            allowed_lanes=["general", "fact"],
            role="user",
        )

    app.dependency_overrides[get_session] = _factory
    app.dependency_overrides[get_current_user] = _user
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_current_user, None)


class TestRestFaces:
    def test_rest_qijuzhu_bad_date_422_not_500(self, rest_client):
        """GET /qijuzhu/dates/{bad} 修复前 strptime ValueError 冒泡成 500
        （票 41 同病），修复后 422。"""
        resp = rest_client.get("/qijuzhu/dates/2026-13-99")
        assert resp.status_code == 422, f"修复前实际 {resp.status_code}"

    def test_rest_cehou_sample_nonfinite_422(self, rest_client):
        """POST /cehou/sample 携带 JSON Infinity 修复前 200 且毒值入库
        （pydantic float 无 allow_inf_nan=False + json.loads 放行 Infinity），
        修复后 422。"""
        resp = rest_client.post(
            "/cehou/sample",
            content=json.dumps(
                {"window_id": "w-rest", "success": True, "score_delta": "Infinity"},
            ).replace('"Infinity"', "Infinity"),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422, f"修复前实际 {resp.status_code}"

    def test_rest_cehou_mock_days_bounded(self, rest_client):
        """POST /cehou/inspect mock_days=10**9 修复前 200 且一检即毕业
        （10**9 >= required_days 恒真），修复后 422（对齐引擎 [0,36500]）。"""
        resp = rest_client.post(
            "/cehou/inspect",
            json={"window_id": "w-rest-2", "mock_days_observed": 10**9},
        )
        assert resp.status_code == 422, f"修复前实际 {resp.status_code}"

    def test_rest_cehou_create_zero_days_422(self, rest_client):
        """现状锁：REST create required_days=0 已被 pydantic ge=1 拦（422）。"""
        resp = rest_client.post(
            "/cehou/windows",
            json={
                "window_id": "w-rest-3",
                "target_type": "param",
                "target_id": "t-1",
                "required_days": 0,
            },
        )
        assert resp.status_code == 422
