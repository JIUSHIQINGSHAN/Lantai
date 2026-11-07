"""票 19：全局动作路由零身份/丢身份——五条路由 `require_admin` 守卫 + 注册层结构锁。

**病灶**（`.scratch/v0.68-next-window-2026-11/issues/19-global-action-routes-no-identity.md`）：
`app.py` CORE_ROUTERS 的 router 级 AUTH 只做**认证**（无凭证 401），四条全局动作
路由「已认证、未授权」——任何持 key 者（含受限 Bearer user 与 DEV MODE）可：
直跑全表演化周期（`POST /evolve/run`）、以系统身份驱动任意全库 worker
（`POST /workers/{name}/run` 与 `POST /monitor/workers/{name}/run` 双挂载，
runner 以 SYSTEM_VIEWER 执行）、读全表考功审计报告（两个 report 路由，
`details[].fenced_content` 含记忆**明文全文**）。

**修复语义**：五条路由挂 `require_admin`（严格 `role=="admin"`，与既有 admin
面 routes_admin 同口径）。已知契约（`docs/compose/spec/p0-auth-entry.md:68`
Out-of-Scope P1 登记）：admin 仅 env key（`ApiKey` 表无 role 列，库内 Bearer
与 DEV MODE 恒 user）→ 挂守卫后这些部署形态对五路由 403，与 admin 面现状
一致；角色真正可赋值是已登记的延后项，不在本票范围。

**替身面声明**：身份**来源**可替换（`get_current_user` override——只替换
「凭证→Principal」的传输层，被测的 `require_admin` 守卫本体恒为真实现）；
admin 臂与 Bearer 臂走**真鉴权链**（monkeypatch `settings.API_KEY` + 真头）。
被测的守卫、路由分发、worker/报告 service 层零 mock。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.auth import Principal, get_current_user
from lantai.core.settings import settings
from lantai.storage.fts import init_fts

# 六条全局动作路由（票 19 病灶 4 条 + 立案外同族姊妹 /kaogong/report + 复查
# 补刀 /ingest/run——与 /workers/ingest/run 触发同一 run_ingest_once，平行
# URL 不挂闸即整体架空守卫）
GUARDED = [
    ("/evolve/run", "POST"),
    ("/workers/{worker_name}/run", "POST"),
    ("/monitor/workers/{worker_name}/run", "POST"),
    ("/ingest/run", "POST"),
    ("/evolution/kaogong/report", "GET"),
    ("/kaogong/report", "GET"),
]

ADMIN_KEY = "k19-admin-test-key"


@pytest.fixture
def engine():
    """真内存 SQLite + 真 FTS5 表（ApiKey/worker 触发臂的落库面）。"""
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed_bearer_user(engine, monkeypatch) -> str:
    """真鉴权链受限 user：库内 Bearer key（auth.py B 路）→ role="user"。
    DB 会话 patch 走 monkeypatch（自动还原）。返回 raw key。"""
    from lantai.core.auth import create_api_key, hash_key
    from lantai.models.tables import ApiKey
    from lantai.storage import db as db_module

    raw, key = create_api_key("k19-restricted-user", ["general"])
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    with Session(engine) as s:
        s.add(
            ApiKey(
                id=key.id,
                key_hash=hash_key(raw),
                user_id=key.user_id,
                allowed_lanes=["general"],
            )
        )
        s.commit()
    return raw


def _patch_db(monkeypatch, engine):
    from lantai.storage import db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))


def _as_override(principal, fn):
    """只替换身份**来源**（凭证→Principal 传输层）；require_admin 守卫本体不动。"""
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── 注册层结构锁：路由注册不能绕过身份守卫 ────────────────────────

# 五条路由的 endpoint 函数名（路由器懒挂载形态下以 endpoint 名为锚；
# methods 断言防方法漂移）。阴性对照 = list_proposals_route（普通路由）。
TARGET_ENDPOINTS = {
    "evolve_run_route": "POST",
    "run_worker_route": "POST",
    "monitor_run_worker": "POST",
    "ingest_run_route": "POST",
    "kaogong_report_route": "GET",
    "kaogong_report_endpoint": "GET",
}
NEGATIVE_CONTROL = "list_proposals_route"


def _all_api_routes() -> dict[str, object]:
    """遍历 app 的路由注册面，返回 {endpoint 名: route}。

    双形态兼容：本仓 pin 的 fastapi 走懒挂载（fastapi.routing._IncludedRouter
    包 original_router）；fastapi ≥0.136 include_router 直接扁平化（app.routes
    里是裸 APIRoute）。两形态都枚举，防止依赖版本漂移把结构锁变成空表假红。
    """
    from lantai.api.app import app

    out: dict[str, object] = {}
    for wrapped in app.routes:
        router = getattr(wrapped, "original_router", None)
        for route in getattr(router, "routes", []) or []:
            name = getattr(getattr(route, "endpoint", None), "__name__", None)
            if name:
                out[name] = route
        # 扁平形态回退：wrapped 自身就是 APIRoute（有 dependant/endpoint）
        name = getattr(getattr(wrapped, "endpoint", None), "__name__", None)
        if name and hasattr(wrapped, "dependant") and name not in out:
            out[name] = wrapped
    return out


def _dep_calls(route) -> list:
    return [d.call for d in route.dependant.dependencies]


class TestRouteRegistrationGuardLock:
    def test_five_global_action_routes_carry_admin_guard(self):
        """五条全局动作路由的**注册依赖链**必须含 require_admin——挂载层
        结构锁：新增/移动路由漏挂守卫即红（不只测 handler 函数体）。"""
        from lantai.core.auth import require_admin

        routes = _all_api_routes()
        missing = [name for name in TARGET_ENDPOINTS if name not in routes]
        assert not missing, f"全局动作路由未挂载（防改名静默丢锁）：{missing}"

        unguarded = []
        for name, method in TARGET_ENDPOINTS.items():
            route = routes[name]
            if method not in getattr(route, "methods", set()):
                unguarded.append(f"{name} 方法漂移：实际 {sorted(route.methods)}")
            if require_admin not in _dep_calls(route):
                unguarded.append(f"{name} 注册依赖链缺 require_admin（可绕过守卫）")
        assert not unguarded, f"守卫缺失/漂移：{unguarded}"

    def test_guard_detection_discriminates(self):
        """阴性对照：检测机制本身必须能区分——普通路由不含 require_admin。
        （防「恒真断言」假绿：锁了等于没锁。）"""
        from lantai.core.auth import require_admin

        routes = _all_api_routes()
        route = routes.get(NEGATIVE_CONTROL)
        assert route is not None, "阴性对照路由不存在"
        assert require_admin not in _dep_calls(route), (
            "阴性对照失真：普通路由也被判成 admin 面——结构锁检测机制失效"
        )


# ── HTTP 真链：身份矩阵 ──────────────────────────────────────────


class TestWorkerTriggerRoutesAdminOnly:
    def test_work_items_worker_route_admin_422_user_403(self, engine, monkeypatch):
        """判别对：admin + 未知 worker 名 → 422（过了守卫、抵达 service 的
        ValueError）；受限 user + **合法** worker 名 → 403（守卫在 service
        **之前**拦下——service 本会接受该名字）。两臂合起来钉住
        「守卫位于动作分发入口之前」。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        raw = _seed_bearer_user(engine, monkeypatch)

        from lantai.api.app import app

        with TestClient(app) as c:
            r_admin = c.post(
                "/workers/definitely-not-a-worker/run",
                headers={"X-API-Key": ADMIN_KEY},
            )
            assert r_admin.status_code == 422, (
                f"admin + 未知名应过守卫抵达 service（422），实际 {r_admin.status_code}：{r_admin.text}"
            )
            r_user = c.post("/workers/ingest/run", headers={"Authorization": f"Bearer {raw}"})
            assert r_user.status_code == 403, (
                f"受限 Bearer user 触发全局 worker 应 403，实际 {r_user.status_code}：{r_user.text}"
            )
            assert "Admin privileges required" in r_user.json()["detail"]

    def test_monitor_worker_route_same_guard(self, engine, monkeypatch):
        """双挂载同守卫（routes_monitor.py:78 与 routes_work_items.py:80 同一
        service 两处挂载，各有独立 UI 消费——只守一处另一处仍开放）。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)
        user_p = Principal(user_id="k19-some-user", role="user")

        from lantai.api.app import app

        with TestClient(app) as c:
            r_admin = c.post(
                "/monitor/workers/definitely-not-a-worker/run",
                headers={"X-API-Key": ADMIN_KEY},
            )
            assert r_admin.status_code == 422, (
                f"admin + 未知名应过守卫抵达 service（422），实际 {r_admin.status_code}"
            )
            r_user = _as_override(user_p, lambda: c.post("/monitor/workers/ingest/run"))
            assert r_user.status_code == 403, (
                f"弃 ctx 形态路由（monitor）对非 admin 应 403，实际 {r_user.status_code}"
            )

    def test_admin_real_run_ingest_worker(self, engine, monkeypatch):
        """admin 真触发臂（现状行为不变锁）：真 run_ingest_once 空库零 source
        → ok:True。守卫不能把 admin 面的功能修废。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)

        from lantai.api.app import app

        with TestClient(app) as c:
            r = c.post("/workers/ingest/run", headers={"X-API-Key": ADMIN_KEY})
            assert r.status_code == 200, f"admin 真 worker 触发应 200：{r.status_code} {r.text}"
            assert r.json()["ok"] is True


class TestIngestRunRouteAdminOnly:
    def test_admin_runs_bearer_user_403(self, engine, monkeypatch):
        """复查补刀（票 19 阻塞项 1）：`POST /ingest/run` 与 `/workers/ingest/run`
        触发**同一** `run_ingest_once`——零身份形态不修，守卫被平行 URL 整体
        架空。admin → 200 真跑；受限 Bearer user → 403。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        raw = _seed_bearer_user(engine, monkeypatch)

        from lantai.api.app import app

        with TestClient(app) as c:
            r_admin = c.post("/ingest/run", headers={"X-API-Key": ADMIN_KEY})
            assert r_admin.status_code == 200, (
                f"admin 真摄取触发应 200：{r_admin.status_code} {r_admin.text}"
            )
            assert r_admin.json()["ok"] is True
            r_user = c.post("/ingest/run", headers={"Authorization": f"Bearer {raw}"})
            assert r_user.status_code == 403, (
                f"受限 Bearer user 走平行 URL 触发全库摄取应 403，实际 {r_user.status_code}"
            )


class TestRecallReportRoutePrincipalPassthrough:
    def test_route_passes_principal_narrowing(self, engine, monkeypatch):
        """复查补刀（票 19 阻塞项 2）：`GET /retrieval/recall-report` 此前零身份
        调 `recall_report(days)`——服务本就按 viewer 收窄（readside-gaps/05，
        `_recall_scope` 口径同票 04），MCP 透传（mcp.py:995）而 REST 丢弃——
        与考功报告病灶同构（服务收窄、路由绕过）。修后路由透传：user 视角
        只见自己的事件（+NULL 老行），admin 全量。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)

        from lantai.api.app import app
        from lantai.observability.retrieval_log import log_retrieval

        log_retrieval("user-a 的私有问句", [], latency_ms=1, principal=Principal(user_id="k19-ua"))
        log_retrieval("user-b 的私有问句", [], latency_ms=1, principal=Principal(user_id="k19-ub"))

        ua = Principal(user_id="k19-ua", role="user")
        with TestClient(app) as c:
            r_admin = c.get("/retrieval/recall-report", headers={"X-API-Key": ADMIN_KEY})
            assert r_admin.status_code == 200, f"admin 全量报告应 200：{r_admin.text}"
            assert r_admin.json()["total"] == 2, (
                f"admin/全量口径应见两用户事件，实际 {r_admin.json()['total']}"
            )
            r_user = _as_override(ua, lambda: c.get("/retrieval/recall-report"))
            assert r_user.status_code == 200
            assert r_user.json()["total"] == 1, (
                f"user 视角不应聚合他人检索事件（服务收窄被路由绕过=跨属主统计"
                f"投毒面），实际 total={r_user.json()['total']}"
            )


class TestEvolveRunRouteAdminOnly:
    def test_admin_runs_full_cycle_user_bearer_403(self, engine, monkeypatch):
        """POST /evolve/run：admin → 200 ok:true（空库真演化周期，零候选
        零 LLM）；受限 user → 403（此前任何持 key 者可直跑全表演化）。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        raw = _seed_bearer_user(engine, monkeypatch)

        from lantai.api.app import app

        with TestClient(app) as c:
            r_admin = c.post("/evolve/run", headers={"X-API-Key": ADMIN_KEY})
            assert r_admin.status_code == 200, (
                f"admin 直跑演化周期应 200：{r_admin.status_code} {r_admin.text}"
            )
            assert r_admin.json()["ok"] is True

            r_user = c.post("/evolve/run", headers={"Authorization": f"Bearer {raw}"})
            assert r_user.status_code == 403, (
                f"受限 Bearer user 直跑全表演化应 403，实际 {r_user.status_code}"
            )


class TestKaogongReportRoutesAdminOnly:
    def test_both_report_routes_admin_200_user_403(self, engine, monkeypatch):
        """两个考功报告路由（/evolution/kaogong/report 与姊妹 /kaogong/report）：
        报告是全局快照（`_LATEST_KAOGONG_REPORT`，admin/系统身份周期产生，
        `details[].fenced_content` 含记忆明文全文）——只该 admin 可读。
        受限 user → 403。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)
        user_p = Principal(user_id="k19-report-user", role="user")

        from lantai.api.app import app

        with TestClient(app) as c:
            for path in ("/evolution/kaogong/report", "/kaogong/report"):
                r_admin = c.get(path, headers={"X-API-Key": ADMIN_KEY})
                assert r_admin.status_code == 200, (
                    f"admin 读报告应 200：{path} {r_admin.status_code} {r_admin.text}"
                )
                assert "evaluated" in r_admin.json()

                r_user = _as_override(user_p, lambda p=path: c.get(p))
                assert r_user.status_code == 403, (
                    f"非 admin 读全表考功审计报告（含他人记忆正文）应 403：{path}，"
                    f"实际 {r_user.status_code}"
                )


class TestIdentityShapes:
    @pytest.mark.parametrize(
        "principal",
        [
            Principal(user_id="u1", role="user"),
            Principal(user_id="", role="user"),  # 空身份
            Principal(user_id=None, role="user"),  # 缺失身份字段
            Principal(user_id="u1", tenant_id="tenant-x", role="user"),  # 带租户仍非 admin
            Principal(user_id="svc", role="system"),  # system 角色也不过 admin 面
        ],
        ids=["user", "empty-user-id", "missing-user-id", "tenant-user", "system-role"],
    )
    def test_non_admin_identity_shapes_rejected(self, engine, monkeypatch, principal):
        """伪造/空/不完整/降权身份全拒绝：守卫判据是严格 `role=="admin"`。
        `role="system"` 也不放行——与既有 admin 面（routes_admin）同口径
        （`Principal.is_admin` 含 system 是 service 层另一口径，两口径并存
        见 auth 注释，admin 面从严）。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)

        from lantai.api.app import app

        with TestClient(app) as c:
            for path, method in GUARDED:
                if method == "POST":
                    resp = _as_override(principal, lambda p=path: c.post(p))
                else:
                    resp = _as_override(principal, lambda p=path: c.get(p))
                assert resp.status_code == 403, (
                    f"{method} {path} 对 {principal.role}/{principal.user_id} 应 403，"
                    f"实际 {resp.status_code}"
                )

    def test_dev_mode_principal_rejected(self, monkeypatch):
        """DEV MODE（role="user"）也不放行——与既有 admin 面在 DEV 下 403
        同构（p0-auth-entry.md:68：admin 仅 env key 是现行登记契约；角色
        可赋值是 Out-of-Scope P1 延后项）。行为锁，防未来静默放宽。"""
        monkeypatch.setattr(settings, "API_KEY", "", raising=False)

        from lantai.api.app import app

        with TestClient(app) as c:
            r = c.post("/evolve/run")
            assert r.status_code == 403, (
                f"DEV MODE 身份直跑演化周期应 403（admin 面口径），实际 {r.status_code}"
            )

    def test_missing_credentials_401_router_level_auth(self, monkeypatch):
        """无凭证 → 401（router 级 AUTH 认证层），不是 403（授权层）——
        两层语义分开钉，防测试写宽。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)

        from lantai.api.app import app

        with TestClient(app) as c:
            r = c.post("/evolve/run")
            assert r.status_code == 401, f"无凭证应 401（认证层拒绝），实际 {r.status_code}"

    def test_forged_bearer_key_401(self, engine, monkeypatch):
        """伪造 Bearer key（库内不存在）→ 401——伪造在**认证层**就失败，
        到不了授权层。"""
        monkeypatch.setattr(settings, "API_KEY", ADMIN_KEY, raising=False)
        _patch_db(monkeypatch, engine)

        from lantai.api.app import app

        with TestClient(app) as c:
            r = c.post("/evolve/run", headers={"Authorization": "Bearer sk-forged-key"})
            assert r.status_code == 401, f"伪造 key 应 401，实际 {r.status_code}"
