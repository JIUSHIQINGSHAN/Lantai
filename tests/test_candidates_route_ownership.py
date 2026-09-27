"""`GET /candidates` 读侧归属：票 .scratch/readside-gaps/07（修法口径 2）

**这条不是 `/candidates/pending`**（那条 Ticket 02 / readside-gaps/02 已修）——
是 `routes_sources.py:31` 那条同名不同源的 `GET /candidates`，读
`source_service.list_candidates` → `MemoryCandidate` 表。它一个身份都不取，
`select(MemoryCandidate).where(status==status).limit(limit)` 全表捞，
把别人的 `summary` / `claims` / `contradictions` 明文连同 `id` 一起吐出来。
第三轮探测实证：A 打这个端点即读到 B 的候选正文（`OBSERVED3.txt`）。

**与已修的 `/candidates/pending` 的口径差别是刻意的**：
- `/candidates/pending`（Ticket 02）走 `_owner_scope` **严格** `user_id = ?`，
  NULL 老行不可见——理由是"候选队列是待审正文，宁 miss 不脏写"。
- 本文件沿用**同一**严格口径（`MemoryCandidate` 有完整归属四元组，
  真实库 37 行候选全部 `user_id='default'`，见 `test_candidate_queue.py`
  的 `_make_cand` 注释）。不另造 `OR IS NULL`：候选正文是高敏字段，
  宁可少列，不可错放。

写入侧（`POST /sources`、`POST /ingest/run` 不传 user_id）不归本文件管，
另见票 07「留待下轮」。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import MemoryCandidate

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def cand_env():
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
    """A/B 各一条 `status="new"` 的候选（`/candidates` 默认查 new）。"""
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id="cand-A",
                document_id="doc-A",
                user_id="user-A",
                summary="A 的候选摘要：连接池 500",
                claims=["A 的论断"],
                contradictions=["A 的矛盾点：副本数待确认"],
                extractor_confidence=0.5,
                lane="general",
                status="new",
            )
        )
        s.add(
            MemoryCandidate(
                id="cand-B",
                document_id="doc-B",
                user_id="user-B",
                summary="B 的候选摘要",
                claims=[SECRET],
                contradictions=["B 的矛盾点"],
                extractor_confidence=0.5,
                lane="general",
                status="new",
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


def _get(principal, **params):
    from lantai.api.app import app

    with TestClient(app) as c:
        resp = _as(principal, lambda: c.get("/candidates", params=params))
    assert resp.status_code == 200, resp.text
    return resp.json()["candidates"]


class TestCandidatesRouteOwnership:
    def test_excludes_other_users_candidates(self, cand_env):
        """A 列候选不该拿到 B 的正文（summary/claims/contradictions）。"""
        session_factory, _ = cand_env, None
        _seed(session_factory)

        rows = _get(_principal("user-A"))
        blob = repr(rows)
        assert SECRET not in blob, "A 读到了 B 的候选正文（claims/contradictions）"
        assert "cand-B" not in blob, "A 的响应里出现了 B 的候选 id"

    def test_owner_still_sees_own(self, cand_env):
        """A 自己的候选照旧能列（不能修废）。"""
        session_factory, _ = cand_env, None
        _seed(session_factory)

        rows = _get(_principal("user-A"))
        ids = [r["id"] for r in rows]
        assert "cand-A" in ids, f"A 自己的候选列不出来：{ids}"
        assert "cand-B" not in ids, f"A 列到了 B 的候选：{ids}"
        summaries = [r.get("summary", "") for r in rows]
        assert any("连接池 500" in t for t in summaries), f"A 的 summary 丢了：{summaries}"

    def test_admin_sees_all(self, cand_env):
        """admin 照见全部（不能把运维候选队列修废）。"""
        session_factory, _ = cand_env, None
        _seed(session_factory)

        rows = _get(_principal(None, role="admin"))
        ids = sorted(r["id"] for r in rows)
        assert ids == ["cand-A", "cand-B"], f"admin 看不到全部候选：{ids}"

    def test_internal_call_converges_to_default(self, cand_env):
        """`principal=None`（内部 CLI/worker/MCP）收敛到 `"default"`，不是全表。

        这是**项目既有约定**，不是本票新造的：`work_item_service._viewer_of`
        的 docstring（`:503-517`）写明「`principal=None`（内部调用 / MCP /
        脚本）与未记录归属都收敛到 `"default"`，与 `auth.py` DEV MODE 回落的
        user_id 同值」。Ticket 02 的 `list_pending_candidates` 同样如此。

        所以内部调用**看得见 default 的行、看不见别人的行**——
        这比"完全不过滤"更安全，且与主链路一致。判据落在具体 id 上。
        """
        session_factory, _ = cand_env, None
        _seed(session_factory)
        with session_factory() as s:
            s.add(
                MemoryCandidate(
                    id="cand-default",
                    document_id="doc-default",
                    user_id="default",
                    summary="default 的候选",
                    extractor_confidence=0.5,
                    lane="general",
                    status="new",
                )
            )
            s.commit()

        from lantai.services.source_service import list_candidates

        rows = list_candidates("new", 20)["candidates"]
        ids = sorted(r["id"] for r in rows)
        # 收敛到 default → 只见 default 自己的行，A/B 的都不可见
        assert ids == ["cand-default"], (
            f"principal=None 应收敛到 default 且只见 default 的行：{ids}"
        )

    def test_status_filter_still_works(self, cand_env):
        """既有 `status` 过滤不能因为加归属而被改坏。"""
        session_factory, _ = cand_env, None
        _seed(session_factory)
        with session_factory() as s:
            s.add(
                MemoryCandidate(
                    id="cand-A-reviewed",
                    document_id="doc-A",
                    user_id="user-A",
                    summary="A 的已审候选",
                    extractor_confidence=0.5,
                    lane="general",
                    status="reviewed",
                )
            )
            s.commit()

        rows = _get(_principal("user-A"), status="reviewed")
        ids = [r["id"] for r in rows]
        assert ids == ["cand-A-reviewed"], f"status 过滤被改坏了：{ids}"
