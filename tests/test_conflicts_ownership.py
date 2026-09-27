"""冲突账本读写两侧归属：票 .scratch/readside-gaps/07（修法口径 3）

两条一起修，因为它们同源：

1. **读侧** `GET /conflicts`（`routes_conflicts.py` → `conflict_service.list_conflict_events`）
   一个身份都不取，`select(ConflictEvent).order_by(created_at.desc())` 全表捞，
   把别人的 `incoming_ref` **正文**连同 `memory_id` 一起吐出来
   （第三轮探测 `OBSERVED3.txt` 实证）。

2. **写侧** `POST /conflicts/{id}/resolve`：实测 A 能把 B 的冲突
   `open → dismissed`。那是**替 B 做判断**——dismissed 的语义是
   "这条冲突是误报"，由别人来判就是篡改他人的消解结论。

**归属判据只能过渡推导**：`ConflictEvent` **没有归属列**
（`tables.py:371-383` 只有 id/memory_id/incoming_ref/rule_name/status…）。
它是账本，归属跟着它指向的记忆走：`conf.memory_id → MemoryItem.user_id`。
与探颐（同票口径 4）逐字同源，也与 `memorynode` / `failurerecord`
同类（没有列）——但这里不需要加列，理由见探颐测试文件的说明。

裁决是**破坏性操作**（状态机不可逆：open → resolved/dismissed 后
`resolve_conflict_event` 会因 "not open" 拒绝二次裁决），
复用 `acl.ensure_can_delete` 单一真源（同票 02 口径）。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import ConflictEvent, MemoryItem

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"
INCOMING = "B 的待裁决事实：连接池即将扩容到 200"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def conflicts_env():
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
    """A/B 各一条记忆 + 挂在各自记忆上的未决冲突。"""
    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem-A",
                content="A 的事实：连接池 500",
                memory_type="text",
                lane="fact",
                domain="user",
                decay_score=1.0,
                status="active",
                user_id="user-A",
                tenant_id=None,
            )
        )
        s.add(
            MemoryItem(
                id="mem-B",
                content=SECRET,
                memory_type="text",
                lane="fact",
                domain="user",
                decay_score=1.0,
                status="active",
                user_id="user-B",
                tenant_id=None,
            )
        )
        s.add(
            ConflictEvent(
                id="conf-A",
                memory_id="mem-A",
                incoming_ref="A 的待裁决事实",
                rule_name="r",
                status="open",
            )
        )
        s.add(
            ConflictEvent(
                id="conf-B",
                memory_id="mem-B",
                incoming_ref=INCOMING,
                rule_name="r",
                status="open",
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


def _status_of(session_factory, conf_id: str) -> str:
    with session_factory() as s:
        c = s.get(ConflictEvent, conf_id)
        return c.status if c else ""


# ── 读侧：A 看不到 B 的冲突正文与 memory_id ─────────────────────


class TestConflictsReadOwnership:
    def test_list_excludes_other_users_conflicts(self, conflicts_env):
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/conflicts"))

        assert resp.status_code == 200, resp.text
        blob = resp.text
        assert SECRET not in blob, "A 读到了 B 的记忆正文（经 existing 关联）"
        assert INCOMING not in blob, "A 读到了 B 的 incoming_ref 全文"
        assert "conf-B" not in blob, "A 的响应里出现了 B 的冲突 id"
        assert "mem-B" not in blob, "A 的响应里出现了 B 的 memory_id（id 是 oracle）"

    def test_owner_still_sees_own(self, conflicts_env):
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/conflicts"))

        assert resp.status_code == 200, resp.text
        ids = [e["id"] for e in resp.json()["events"]]
        assert ids == ["conf-A"], f"A 只看得到自己的冲突：{ids}"

    def test_admin_sees_all(self, conflicts_env):
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/conflicts"))

        assert resp.status_code == 200, resp.text
        ids = sorted(e["id"] for e in resp.json()["events"])
        assert ids == ["conf-A", "conf-B"], f"admin 看不到全部冲突：{ids}"

    def test_internal_call_converges_to_default(self, conflicts_env):
        """`principal=None`（内部调用）收敛到 `"default"`，不是全表。

        同 `_viewer_of` 的既有约定（`work_item_service.py:503-517`）：
        内部调用/MCP/脚本与未记录归属都收敛到 `"default"`。
        """
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.services.conflict_service import list_conflict_events

        rows = list_conflict_events()["events"]
        # A/B 都不是 default → 内部调用一条都看不到（宁 miss 不脏写）
        assert [e["id"] for e in rows] == [], f"principal=None 的可见范围不符合约定：{rows}"


# ── 写侧：A 不能替 B 裁决 ──────────────────────────────────────


class TestConflictsResolveOwnership:
    def test_other_user_cannot_resolve(self, conflicts_env):
        """A resolve B 的冲突 → 403，且冲突仍 open（403 不落库）。"""
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/conflicts/conf-B/resolve",
                    json={"decision": "dismissed", "note": "我说是误报"},
                ),
            )

        assert resp.status_code == 403, f"A 竟然能替 B 裁决冲突：{resp.status_code} {resp.text}"
        assert _status_of(session_factory, "conf-B") == "open", "403 却把冲突状态改了"

    def test_owner_can_still_resolve(self, conflicts_env):
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post(
                    "/conflicts/conf-B/resolve",
                    json={"decision": "resolved", "note": "确实矛盾"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _status_of(session_factory, "conf-B") == "resolved"

    def test_admin_can_still_resolve(self, conflicts_env):
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.post(
                    "/conflicts/conf-B/resolve",
                    json={"decision": "dismissed", "note": "运维确认为误报"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _status_of(session_factory, "conf-B") == "dismissed"

    def test_internal_call_unfiltered(self, conflicts_env):
        """principal=None（内部/CLI）不加校验，与改动前逐字一致。"""
        session_factory = conflicts_env
        _seed(session_factory)

        from lantai.services.conflict_service import resolve_conflict_event

        r = resolve_conflict_event("conf-B", "dismissed", note="内部裁决")
        assert r["status"] == "dismissed"
        assert _status_of(session_factory, "conf-B") == "dismissed"

    def test_orphan_conflict_invisible_to_non_admin(self, conflicts_env):
        """孤儿冲突（挂载记忆已删）对非 admin 不可见，admin 仍可见可裁决。

        归属只能从挂载记忆过渡推导，记忆没了就推导不出属主，于是**无法证明
        它"不是别人的"**——按 `_owner_scope` 的 NULL 口径办：NULL 不等于
        default，宁可漏列。代价是孤儿账本从列表消失；但 admin 看得见，
        不会永久卡死（`test_admin_sees_all` 已覆盖 admin 全权）。
        """
        session_factory = conflicts_env
        _seed(session_factory)
        with session_factory() as s:
            s.add(
                ConflictEvent(
                    id="conf-orphan",
                    memory_id="mem-deleted",
                    incoming_ref="孤儿账本的待裁决事实",
                    rule_name="r",
                    status="open",
                )
            )
            s.commit()

        from lantai.services.conflict_service import list_conflict_events

        ids = [e["id"] for e in list_conflict_events(principal=_principal("user-A"))["events"]]
        assert "conf-orphan" not in ids, f"孤儿冲突对非 admin 可见了：{ids}"
        assert ids == ["conf-A"], f"非 admin 只看得到自己的冲突：{ids}"

        admin_ids = [
            e["id"]
            for e in list_conflict_events(principal=_principal(None, role="admin"))["events"]
        ]
        assert "conf-orphan" in admin_ids, f"admin 也看不到孤儿冲突（会永久卡死）：{admin_ids}"
