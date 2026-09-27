"""`/checkpoint*` 归属：票 .scratch/readside-gaps/09

**先说影响**：A 能读到任意会话的五段底本全文，一个身份都不验。
`GET /checkpoint?session_id=<任意值>` 甚至不用猜 newest——知道 id
就能读到那一会话的当前意图、下一步动作、工作区、关键决策、待办。
那是把当前工作现场整个吐出去。

第五轮实证（`probe_round5.py`）：A 打过去 len=321 **含** B 的密文。

**第四轮为什么漏了**：第四轮探针种的是 `MemoryCheckpoint`
（evolution 的记忆变更快照），而 `get_latest_checkpoint` 读的是
`SessionCheckpoint`（ADR-0021 底本五段）——两张表同名不同物，
那条探针一直在测空表，len=4 被当成了"已修"。
教训：**空响应冒充 ok**。本文件的判据因此全部落在具体 block 的
content 上，且种子保证库非空。

**归属列一直在**：`tables.py:645` `SessionCheckpoint` 四元组齐全，
只是写入方从不填、读取方从不校验——同票 01/02/03/06 的共同结构。

**NULL 口径用 `OR IS NULL`**（同票 03/04/06）：单人部署下老行
`user_id` 为 NULL，判"不可见"会让 `/checkpoint/latest` 对唯一真实
用户返回空，底本注入直接失效。NULL 是「未记录」不是「属于所有人」。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import SessionCheckpoint

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"

BLOCK_KEYS = (
    "cp_active_intent",
    "cp_next_action",
    "cp_current_work",
    "cp_key_decisions",
    "cp_open_notes",
)


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def cp_env():
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


def _write(
    session_factory,
    session_id: str,
    user_id: str | None,
    *,
    age_days: int = 0,
):
    """直接落库一个会话的五段底本（绕开 service，专测读侧）。

    `age_days` 让这个会话变旧——淘汰算法按 created_at 取 newest，
    所有会话同一时刻落库时彼此并列，`max_sessions=1` 也删不掉任何一条
    （探针会因此假绿）。想让淘汰真的发生，就得让新旧可区分。
    """
    from datetime import UTC, datetime, timedelta

    from lantai.core.time import utcnow

    created = utcnow() - timedelta(days=age_days)
    with session_factory() as s:
        for key in BLOCK_KEYS:
            s.add(
                SessionCheckpoint(
                    session_id=session_id,
                    block_key=key,
                    content=f"{session_id}:{key}:{SECRET}",
                    user_id=user_id,
                    tenant_id=None,
                    created_at=created,
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


def _rows(session_factory, session_id: str) -> list[SessionCheckpoint]:
    from sqlmodel import select

    with session_factory() as s:
        return list(
            s.exec(
                select(SessionCheckpoint).where(SessionCheckpoint.session_id == session_id)
            ).all()
        )


# ── 读侧 ────────────────────────────────────────────────────────


class TestCheckpointReadOwnership:
    def test_by_session_id_excludes_other_users(self, cp_env):
        """A 按 session_id 读 B 的会话 → 拿不到 B 的底本正文。

        判据落在具体 block_key 的 content 上（不是"机密不在响应里"）。
        """
        sf = cp_env
        _write(sf, "sess-B", "user-B")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"), lambda: c.get("/checkpoint", params={"session_id": "sess-B"})
            )

        assert resp.status_code in (200, 403, 404), resp.text
        body = resp.text
        assert SECRET not in body, "A 按 session_id 读到了 B 的底本正文"
        assert "cp_active_intent" not in body, "A 读到了 B 的在做意图"
        assert "cp_key_decisions" not in body, "A 读到了 B 的关键决策"

    def test_latest_excludes_other_users(self, cp_env):
        """`/checkpoint/latest` 只在自己和 NULL 属主的会话里取 newest。"""
        sf = cp_env
        _write(sf, "sess-B", "user-B")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/checkpoint/latest"))

        assert resp.status_code == 200, resp.text
        body = resp.text
        assert SECRET not in body, "A 从 /checkpoint/latest 读到了 B 的底本"
        assert "sess-B" not in body, "A 从 /checkpoint/latest 拿到了 B 的 session_id"

    def test_null_owner_session_still_visible(self, cp_env):
        """NULL 属主的老会话对非 admin 仍可见——底本注入不能失效。"""
        sf = cp_env
        _write(sf, "sess-null", None)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/checkpoint", params={"session_id": "sess-null"}),
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("session_id") == "sess-null", f"NULL 属主老会话不可见了：{body}"

    def test_admin_sees_all(self, cp_env):
        sf = cp_env
        _write(sf, "sess-B", "user-B")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.get("/checkpoint", params={"session_id": "sess-B"}),
            )

        assert resp.status_code == 200, resp.text
        assert SECRET in resp.text, "admin 读不到会话底本（运维修不了）"


# ── 写侧 ────────────────────────────────────────────────────────


class TestCheckpointWriteOwnership:
    def test_write_records_owner(self, cp_env):
        """A 写入 → 落库行 user_id 是 A（落库事实，不只是状态码）。"""
        sf = cp_env

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/checkpoint",
                    json={
                        "session_id": "sess-A",
                        "blocks": {
                            "cp_active_intent": "A 在做的事",
                            "cp_next_action": "A 的下一步",
                        },
                    },
                ),
            )

        assert resp.status_code == 200, resp.text
        rows = _rows(sf, "sess-A")
        assert rows, "写入没落库"
        for r in rows:
            assert r.user_id == "user-A", f"底本行没落归属：user_id={r.user_id}"

    def test_owner_can_read_back_own(self, cp_env):
        """owner 写完能读回自己的（不能修废）。"""
        from lantai.api.app import app

        with TestClient(app) as c:
            _as(
                _principal("user-A"),
                lambda: c.post(
                    "/checkpoint",
                    json={
                        "session_id": "sess-A",
                        "blocks": {"cp_active_intent": "A 在做的事"},
                    },
                ),
            )
            resp = _as(
                _principal("user-A"), lambda: c.get("/checkpoint", params={"session_id": "sess-A"})
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["blocks"]["cp_active_intent"] == "A 在做的事"

    def test_other_user_cannot_cleanup_others(self, cp_env):
        """A 清理不会删掉 B 的会话。

        判据必须落在真正会变的量上：只种 B 一个会话时，`max_sessions=1`
        下 B 永远在 keep 集合里，**收窄生不生效结果都一样**——那是假绿。
        所以种两个会话且让 B 的**更旧**：不带 scope 时淘汰算法看见两个
        会话、保留 newest 的 A，B 的被删；带 scope 时 A 的候选集里只有
        自己那一个，B 根本不在淘汰算法视野内，删不到。
        """
        sf = cp_env
        _write(sf, "sess-B", "user-B", age_days=10)
        _write(sf, "sess-A", "user-A")
        before_b = len(_rows(sf, "sess-B"))
        assert before_b > 0

        from lantai.api.app import app

        with TestClient(app) as c:
            # max_sessions 是查询参数（routes_checkpoint.py:59 的标量签名），
            # 不是 body——发 json 会被静默忽略，回落默认值，
            # 于是 kept=2/deleted=0，断言变成假绿。
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/checkpoint/cleanup", params={"max_sessions": 1}),
            )

        assert resp.status_code == 200, resp.text
        assert len(_rows(sf, "sess-B")) == before_b, "A 的 cleanup 删掉了 B 的会话快照"

    def test_admin_can_cleanup(self, cp_env):
        """admin 照旧能清理**别人的**会话（不能把运维修废）。

        同样不能只断言 200：那在收窄把 admin 也挡掉时照样成立。
        判据是 admin 的 cleanup **真的删掉了 B 的会话**。
        """
        sf = cp_env
        _write(sf, "sess-B-old", "user-B", age_days=10)
        _write(sf, "sess-B-new", "user-B")
        assert len(_rows(sf, "sess-B-old")) > 0

        from lantai.api.app import app

        with TestClient(app) as c:
            # 同 test_other_user_cannot_cleanup_others：max_sessions 是查询参数
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.post("/checkpoint/cleanup", params={"max_sessions": 1}),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] > 0, f"admin 的 cleanup 什么都没删：{resp.text}"
        assert len(_rows(sf, "sess-B-old")) == 0, "admin 删不掉 B 的会话（运维修不了）"

    def test_inject_context_excludes_other_users(self, cp_env):
        """自动注入路径也不吐 B 的底本。"""
        sf = cp_env
        _write(sf, "sess-B", "user-B")

        from lantai.services.checkpoint_service import inject_checkpoint_context

        text = inject_checkpoint_context(
            session_id="sess-A",
            include_persona=False,
            include_scratchpad=False,
            principal=_principal("user-A"),
        )
        assert SECRET not in text, "注入文本含 B 的底本"
        assert "sess-B" not in text, "注入文本泄漏了 B 的 session_id"


# ── 内部调用口径 ────────────────────────────────────────────────


class TestCheckpointInternalCall:
    def test_internal_call_unfiltered(self, cp_env):
        """principal=None（内部/CLI）不加过滤，与改动前逐字一致。"""
        sf = cp_env
        _write(sf, "sess-B", "user-B")

        from lantai.services.checkpoint_service import get_checkpoint

        cp = get_checkpoint("sess-B")
        assert cp is not None and cp["session_id"] == "sess-B", "内部调用被收窄了"
