"""探颐读写两侧归属：票 .scratch/readside-gaps/07（修法口径 4）

**全轮最严重的一条**：`POST /probing/resolve` 写的是**正文**。
服务层 `probing_service.resolve_probe_response` 走到肯定分支时直接
`item.content = conf.incoming_ref`——没有任何归属校验。第三轮探测
`OBSERVED3.txt` 实证：A 用自己的身份调用，`mem-user-B.content`
被覆盖成 A 传的 `incoming_ref`。这既是越权写，也是**数据损毁**
（原文没有备份地消失，只留一个 checkpoint）。

读侧同样漏：`POST /probing/detect` 的 `detect_memory_probes` 用
`select(ConflictEvent).where(status=="open")` 全表捞，把
`existing_content` + `incoming_ref` 两份全文吐出来，还渲染成
"顺便向您求证确认下：关于「…」"的问句——等于把 B 的记忆原文
包装成给 A 的提问。

**归属判据只能过渡推导**：`ConflictEvent` **没有归属列**
（`tables.py:371-383` 只有 id/memory_id/incoming_ref/status…）。
它是账本，归属跟着它指向的记忆走：`conf.memory_id → MemoryItem.user_id`。
这与 `memorynode` / `failurerecord` 是同类问题（没有列），但这里
**不需要加列**——因为冲突账本的存在意义就是挂在某条记忆上，
过渡推导反而是更准的口径（记忆换了属主，冲突的可见性跟着变）。

`POST /probing/resolve` 按**破坏性写操作**处理：复用
`acl.ensure_can_delete` 单一真源（同票 02 口径）。
非 admin 只能消解自己记忆上的冲突。403 **不能伴随落库**——
判定必须发生在任何 `s.commit()` 之前。
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
INCOMING = "A 伪造的新事实：连接池已改为 999999"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def probing_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        # `probing_service.py:182` 的 `from lantai.retrieval.embed import embed`
        # 指向**不存在的模块**（既有 bug，见票 07 Comments 记录）——索引更新
        # 永远走 except 分支。这里 patch 真位置 `lantai.llm.client.embed`
        # 只是防连网；索引断言一律落在 SQLite（唯一真源）上，不碰索引。
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
    ):
        yield session_factory


def _seed(session_factory) -> None:
    """B 有一条记忆 + 挂在它上面的未决冲突（A 想改的就是这条）。"""
    with session_factory() as s:
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
                id="conf-B",
                memory_id="mem-B",
                incoming_ref=INCOMING,
                rule_name="probe_rule",
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


def _content_of(session_factory, mem_id: str) -> str:
    with session_factory() as s:
        m = s.get(MemoryItem, mem_id)
        return m.content if m else ""


def _status_of(session_factory, conf_id: str) -> str:
    with session_factory() as s:
        c = s.get(ConflictEvent, conf_id)
        return c.status if c else ""


# ── 写侧（决定性）：A 不能覆盖 B 的记忆正文 ──────────────────────


class TestProbingResolveWriteOwnership:
    def test_other_user_cannot_overwrite_memory_content(self, probing_env):
        """A resolve B 的冲突 → 403，且 B 的正文**一字未变**、冲突仍 open。

        403 不能伴随落库：判定必须在任何 commit 之前。
        """
        session_factory = probing_env
        _seed(session_factory)
        before = _content_of(session_factory, "mem-B")

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/probing/resolve",
                    json={"conflict_id": "conf-B", "user_reply": "是的，确实改了"},
                ),
            )

        assert resp.status_code == 403, f"A 竟然能 resolve B 的冲突：{resp.status_code} {resp.text}"
        assert _content_of(session_factory, "mem-B") == before, "403 却落库了——B 的记忆正文被改写"
        assert _status_of(session_factory, "conf-B") == "open", "403 却把冲突状态改了"

    def test_owner_can_still_resolve_own(self, probing_env):
        """owner 对自己的冲突照旧能消解（不能修废）。"""
        session_factory = probing_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post(
                    "/probing/resolve",
                    json={"conflict_id": "conf-B", "user_reply": "是的，确实改了"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _status_of(session_factory, "conf-B") == "resolved"
        assert INCOMING in _content_of(session_factory, "mem-B")

    def test_admin_can_still_resolve(self, probing_env):
        """admin 照旧能消解（不能把求证流程修废）。"""
        session_factory = probing_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.post(
                    "/probing/resolve",
                    json={"conflict_id": "conf-B", "user_reply": "是的，确实改了"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _status_of(session_factory, "conf-B") == "resolved"

    def test_internal_call_unfiltered(self, probing_env):
        """principal=None（MCP/CLI 内部调用）不加校验——不改变既有行为。

        `cli/mcp.py` 的 `handle_probe_resolve` 没有调用方身份。
        与票 03/04 同口径：内部入口与 REST 暴露面分开。
        """
        session_factory = probing_env
        _seed(session_factory)

        from lantai.services.probing_service import resolve_probe_response

        res = resolve_probe_response("conf-B", "是的，确实改了")
        assert res["status"] == "resolved", res
        assert INCOMING in _content_of(session_factory, "mem-B")


# ── 读侧：A 探测不到 B 的冲突正文 ──────────────────────────────


class TestProbingDetectReadOwnership:
    def test_detect_excludes_other_users_conflicts(self, probing_env):
        """A 探测不到 B 的 existing_content / incoming_ref。"""
        session_factory = probing_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/probing/detect", json={"query": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        body = resp.text
        assert SECRET not in body, "A 探测到了 B 的 existing_content 全文"
        assert INCOMING not in body, "A 探测到了 B 的 incoming_ref 全文"
        assert "mem-B" not in body, "A 的响应里出现了 B 的 memory_id"

    def test_owner_still_gets_probe(self, probing_env):
        """owner 照旧能探到自己的冲突（不能修废）。"""
        session_factory = probing_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post("/probing/detect", json={"query": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        probes = resp.json()["probes"]
        assert any(p["conflict_id"] == "conf-B" for p in probes), (
            f"owner 探不到自己的冲突：{probes}"
        )
        assert any(SECRET in p["existing_content"] for p in probes)


# ── 遗留 bug 留证（不属于本票修法范围，但被测出来了）────────────────


class TestProbingIndexUpdateBrokenImport:
    """`probing_service.py:182` 的 import 指向不存在的模块（既有 bug 留证）。

    `from lantai.retrieval.embed import embed` —— `lantai/retrieval/` 下
    **没有** `embed` 模块（`ls lantai/retrieval/` 实证）。该 import 被包在
    try/except 里，于是**每次消解冲突后向量索引都不更新**，只留一条 warning。
    数据落库是对的，索引是陈的——检索会漏掉刚改的记忆。

    这条测试**不修**它（超出本票范围），只把它钉住：
    哪天有人"顺手修好"这个 import，这条会失败，提醒他那是另一个 ticket 的
    改动，应该单独提交、单独验证。
    """

    def test_embed_import_target_does_not_exist(self, probing_env):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("lantai.retrieval.embed")
