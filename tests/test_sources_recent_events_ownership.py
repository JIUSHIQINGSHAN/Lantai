"""`/sources`、`/retrieval/recent-events` 归属：票 .scratch/readside-gaps/10

**先说影响**：两件事，都是"配置即机密"，一个身份都不取。

1. `GET /sources` 把 `Source.config` 原样吐出——`config` 是 JSON 列，
   按设计放的就是 http header、token、api key 这类连接凭证。
2. `GET /retrieval/recent-events` 吐出 `query_text`——用户问过什么，
   比记忆正文更直接地暴露意图。真实库 918 行。

第四/五轮动态实证：两条都 LEAK（`probe_round5.py`）。

**两张表都没有归属列**：`Source`（`tables.py:439`）、
`RetrievalEvent`（`tables.py:554`）。本票加列 + 迁移（同票 11 的
幂等模式），真实库 source 0 行 / retrieval_event 918 行——
后者不回填，老行保持 NULL，读侧靠 `OR IS NULL` 兜住。

**两件事要分开测**（票 10 口径 4）：归属收窄与 `config` 脱敏是独立的
两道防线。即使归属修好，`GET /sources` 也不该把 token 明文回显。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import RetrievalEvent, Source

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def se_env():
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


def _seed(session_factory) -> None:
    from lantai.core.time import utcnow

    with session_factory() as s:
        s.add(
            Source(
                id="src-B",
                kind="http",
                config={"url": "http://b.example", "token": SECRET},
                enabled=True,
                user_id="user-B",
            )
        )
        s.add(
            Source(
                id="src-null",
                kind="http",
                config={"url": "http://legacy.example"},
                enabled=True,
                user_id=None,
            )
        )
        s.add(
            RetrievalEvent(
                id="ev-B",
                trace_id="tr-B",
                query_text=SECRET,
                query_norm_hash="h-B",
                lane="fact",
                param_snapshot_hash="p-B",
                user_id="user-B",
                created_at=utcnow(),
            )
        )
        s.add(
            RetrievalEvent(
                id="ev-null",
                trace_id="tr-null",
                query_text="未归属的老查询",
                query_norm_hash="h-null",
                lane="fact",
                param_snapshot_hash="p-null",
                user_id=None,
                created_at=utcnow(),
            )
        )
        s.commit()


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _row(session_factory, model, row_id: str):
    with session_factory() as s:
        return s.get(model, row_id)


# ── 读侧：sources ───────────────────────────────────────────────


class TestSourceReadOwnership:
    def test_list_excludes_other_users(self, se_env):
        """A 列来源看不到 B 的 config（断言落在 config 内容上）。"""
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/sources"))

        assert resp.status_code == 200, resp.text
        rows = resp.json()["sources"]
        ids = [r["id"] for r in rows]
        assert "src-B" not in ids, f"A 看到了 B 的来源 id：{ids}"
        assert SECRET not in repr(rows), "A 读到了 B 的来源 config"

    def test_null_owner_source_still_visible(self, se_env):
        """NULL 属主的老来源对非 admin 仍可见（不能修废）。"""
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/sources"))

        ids = [r["id"] for r in resp.json()["sources"]]
        assert "src-null" in ids, f"NULL 属主老来源不可见了：{ids}"

    def test_admin_sees_all(self, se_env):
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/sources"))

        ids = sorted(r["id"] for r in resp.json()["sources"])
        assert ids == ["src-B", "src-null"], f"admin 看不到全部：{ids}"

    def test_config_secrets_redacted(self, se_env):
        """脱敏独立于归属：即使 admin，token 也不该明文回显。

        这是票 10 口径 4 单独列出的判据——归属修好不等于可以把凭证
        回显给任何人。`{"url": "http://b.example", "token": SECRET}`
        → url 原样（宁 miss 不脏写：不认识的键不猜），token 变 `***`。
        """
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/sources"))

        rows = resp.json()["sources"]
        b = [r for r in rows if r["id"] == "src-B"][0]
        cfg = b["config"]
        assert SECRET not in repr(cfg), f"token 明文回显了：{cfg}"
        assert cfg.get("token") == "***", f"token 没脱敏成 ***：{cfg}"
        assert cfg.get("url") == "http://b.example", f"正常配置被误脱敏了：{cfg}"


# ── 读侧：recent-events ─────────────────────────────────────────


class TestRecentEventsReadOwnership:
    def test_excludes_other_users_queries(self, se_env):
        """A 读检索事件流看不到 B 的 query_text。"""
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/retrieval/recent-events"))

        assert resp.status_code == 200, resp.text
        body = resp.text
        assert SECRET not in body, "A 读到了 B 的查询词"
        assert "tr-B" not in body, "A 读到了 B 的检索事件 id"

    def test_null_owner_event_still_visible(self, se_env):
        """NULL 属主的老事件对非 admin 仍可见（918 行真实数据全靠这个兜住）。"""
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/retrieval/recent-events"))

        ids = [e["id"] for e in resp.json()["events"]]
        assert "ev-null" in ids, f"NULL 属主老事件不可见了：{ids}"

    def test_admin_sees_all(self, se_env):
        sf = se_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/retrieval/recent-events"))

        ids = [e["id"] for e in resp.json()["events"]]
        assert "ev-B" in ids, f"admin 看不到 B 的事件：{ids}"


# ── 写侧 ────────────────────────────────────────────────────────


class TestWriteOwnership:
    def test_add_source_records_owner(self, se_env):
        """A 新建来源 → 落库行 user_id 是 A（落库事实，不只是状态码）。"""
        sf = se_env

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/sources",
                    json={"kind": "http", "config": {"url": "http://a.example"}, "enabled": True},
                ),
            )

        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]
        row = _row(sf, Source, new_id)
        assert row is not None, "写入没落库"
        assert row.user_id == "user-A", f"来源没落归属：user_id={row.user_id}"

    def test_retrieval_event_records_owner(self, se_env):
        """一次真实检索后，retrieval_event 落库行 user_id 是发起者。

        **不 mock 的冒烟测试**（AGENTS.md 纪律 + 票 10 Red 4）：
        真调 `log_retrieval`，看事件真的落了归属。mock 掉它就读不到
        这条写入路径，测了也白测。
        """
        sf = se_env

        from lantai.observability.retrieval_log import log_retrieval

        with (
            patch.object(db_module, "get_session", sf),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
        ):
            event_id = log_retrieval(
                "A 的查询词",
                [],
                latency_ms=1,
                gate=None,
                lanes=["fact"],
                principal=_principal("user-A"),
            )

        assert event_id, "log_retrieval 没返回 event id"
        row = _row(sf, RetrievalEvent, event_id)
        assert row is not None, "事件没落库"
        assert row.user_id == "user-A", f"检索事件没落归属：user_id={row.user_id}"

    def test_internal_call_unfiltered(self, se_env):
        """principal=None（内部/CLI）不加过滤，与改动前逐字一致。"""
        sf = se_env
        _seed(sf)

        from lantai.services.source_service import list_sources

        ids = sorted(r["id"] for r in list_sources()["sources"])
        assert ids == ["src-B", "src-null"], f"内部调用被收窄了：{ids}"

    def test_search_route_records_event_owner(self, se_env):
        """REST 检索落的事件带发起者身份（顺着调用链，不在 service 另取）。

        **不 mock 的冒烟测试**：真打 `POST /search`，事件真落库，
        再看行的 `user_id`。闸门拦不住就实测混合检索（空库必然零命中，
        仍会走 `_try_log` 那条路）。
        """
        sf = se_env

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/search", json={"query": "A 的检索词", "top_k": 3}),
            )

        assert resp.status_code == 200, resp.text
        event_id = resp.json().get("event_id")
        assert event_id, f"检索没埋点：{resp.json()}"
        row = _row(sf, RetrievalEvent, event_id)
        assert row is not None, "事件没落库"
        assert row.user_id == "user-A", f"REST 检索事件没落归属：user_id={row.user_id}"

    def test_search_route_after_retrieval_records_event_owner(self, se_env):
        """真跑完混合检索那条埋点路径也落归属（`force=True` 绕过闸门）。

        上一条用例的查询词被闸门拦下，走的是 `gate` 拦截分支的 `_try_log`；
        这条专门走到 `hybrid_search` **之后**的那个调用点——两个调用点
        是两行独立的代码，漏一个就有一半的检索事件没有归属。
        空库必然零命中，但埋点照走（zero_result=True 正是它的用途）。
        """
        sf = se_env

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/search",
                    json={"query": "A 的检索词", "top_k": 3, "force": True},
                ),
            )

        assert resp.status_code == 200, resp.text
        event_id = resp.json().get("event_id")
        assert event_id, f"检索没埋点：{resp.json()}"
        row = _row(sf, RetrievalEvent, event_id)
        assert row is not None, "事件没落库"
        assert row.user_id == "user-A", f"检索后埋点没落归属：user_id={row.user_id}"

    def test_mcp_principal_from_params(self):
        """MCP 从 params 取身份：透传则构造、不透传则 None（不猜）。"""
        from lantai.cli.mcp import _principal_from_params

        p = _principal_from_params({"user_id": "user-A"})
        assert p is not None and p.user_id == "user-A", f"透传 user_id 没拿到身份：{p}"
        assert not p.is_admin, "MCP 宿主透传的身份不该是 admin"

        for missing in ({}, {"user_id": None}, {"user_id": ""}, {"user_id": "   "}):
            assert _principal_from_params(missing) is None, f"不该猜出身份：{missing}"
        assert _principal_from_params({"user_id": 42}) is None, "非字符串 user_id 不该构造身份"

    def test_mcp_search_passes_principal_to_log(self, se_env):
        """MCP search 把归属身份传给埋点（两个调用点各测一条）。

        变异 M8/M17 专治：两个 `_try_log` 调用点漏一个，就有一半的
        检索事件没有归属。**不 mock `log_retrieval` 本体**——断言它
        收到的 principal 落在库里的行上（真写真读），mock 掉就只能
        验证"传没传"，验证不到"传对了没有"。
        """
        sf = se_env
        import importlib.util
        from pathlib import Path

        mcp_path = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"
        spec = importlib.util.spec_from_file_location("mcp_server_t10", mcp_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 1) 闸门拦截分支（needs_memory=False）
        with (
            patch.object(db_module, "get_session", sf),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
            patch.object(mod, "relevance_check", return_value={"needs_memory": False}),
        ):
            out = mod.handle_search({"query": "被闸门拦下的查询", "user_id": "user-A"})
        blocked_id = out.get("event_id")
        assert blocked_id, f"闸门分支没埋点：{out}"
        row = _row(sf, RetrievalEvent, blocked_id)
        assert row is not None and row.user_id == "user-A", f"闸门分支没落归属：{row}"

        # 2) 检索后分支（needs_memory=True，空库零命中但埋点照走）
        with (
            patch.object(db_module, "get_session", sf),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
            patch.object(mod, "relevance_check", return_value={"needs_memory": True}),
        ):
            out = mod.handle_search({"query": "真跑检索的查询", "user_id": "user-A", "force": True})
        searched_id = out.get("event_id")
        assert searched_id, f"检索分支没埋点：{out}"
        row = _row(sf, RetrievalEvent, searched_id)
        assert row is not None and row.user_id == "user-A", f"检索分支没落归属：{row}"
        assert searched_id != blocked_id, "两个分支落成了同一条事件"
