"""POST /edges 归属校验：票 .scratch/ownership-gaps/02

`POST /edges` 只建边，不查两端记忆是谁的。而 `hybrid.py:248-255` 的
`_edge_cb` 查 supersedes/contradicts 边时**没有用户过滤**——所以 A 在 B 的
记忆上建一条 supersedes 边，就能把 B 的记忆在 B 自己的检索里压到 A 的之下。
不是读不到，是**排序被改**：用户看到的还是自己的记忆，但「哪个说法排在
前面」被别人定了。

票 03 已经让边带上属主（事后能用 DELETE 的 ACL 止损），缺的是**事前**：
建边时就要校验两端点记忆是不是我的。

校验口径同票 05：建边 = 改写两端记忆的关联关系，按破坏性操作处理，
复用 `acl.ensure_can_delete` 单一真源。**不搞「某些 relation 宽松」的双轨**
——那是漏洞温床。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryEdge, MemoryItem


def _principal(user_id: str, *, admin: bool = False, lanes=("fact",)):
    return Principal(
        user_id=user_id,
        allowed_lanes=list(lanes),
        role="admin" if admin else "user",
    )


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def edge_env(param_env):
    """param_env 底座：内存 SQLite + 真实建表。"""
    return param_env


def _seed(session_factory):
    with session_factory() as s:
        s.add_all(
            [
                MemoryItem(
                    id="memA",
                    memory_type="semantic",
                    key="k-memA",
                    content="A 说连接池上限是 500",
                    lane="fact",
                    status="active",
                    user_id="user-A",
                ),
                MemoryItem(
                    id="memB",
                    memory_type="semantic",
                    key="k-memB",
                    content="B 说连接池上限是 100",
                    lane="fact",
                    status="active",
                    user_id="user-B",
                ),
            ]
        )
        s.commit()


def _mk_edge(client, principal, src, tgt, relation="supersedes", confidence=1.0):
    return _as(
        principal,
        lambda: client.post(
            "/edges",
            json={
                "source_memory_id": src,
                "target_memory_id": tgt,
                "relation": relation,
                "confidence": confidence,
            },
        ),
    )


# ── Red 1（决定性）：不能碰别人的记忆 ──────────────────────────


class TestEdgeAuthorization:
    def test_supersede_other_users_memory_is_rejected(self, edge_env):
        """A 在 B 的记忆上建 supersedes 边 → 403，且边不许落库。

        这条边会参与 B 自己的检索重排序（`_edge_cb` 无用户过滤），
        等于 A 能决定 B 看到的事实排序。
        """
        session_factory, _ = edge_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("user-A"), "memA", "memB")

        assert resp.status_code == 403, (
            f"A 给 B 的记忆建了 supersedes 边（{resp.status_code} {resp.text}）"
        )
        with session_factory() as s:
            assert s.exec(select(MemoryEdge)).all() == [], "拒绝了但边仍然落库"

    def test_other_relations_are_also_checked(self, edge_env):
        """supports/related 同样要校验——不搞「某些 relation 宽松」的双轨。"""
        session_factory, _ = edge_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("user-A"), "memA", "memB", relation="supports")
        assert resp.status_code == 403, f"supports 边绕过了校验（{resp.status_code}）"

    def test_reverse_direction_is_also_checked(self, edge_env):
        """反向（A 作为 target）同样要查：建边是双向改写。"""
        session_factory, _ = edge_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("user-A"), "memB", "memA")
        assert resp.status_code == 403, f"A 作为 target 也绕过了校验（{resp.status_code}）"


# ── Red 2/4：不能把功能修废 ────────────────────────────────────


class TestNoRegression:
    def test_owner_can_link_own_memories(self, edge_env):
        """A 连自己的两条记忆 → 200 且真的建边。"""
        session_factory, _ = edge_env
        _seed(session_factory)
        with session_factory() as s:
            s.add(
                MemoryItem(
                    id="memA2",
                    memory_type="semantic",
                    key="k-memA2",
                    content="A 的另一条：连接池上限调整为 500",
                    lane="fact",
                    status="active",
                    user_id="user-A",
                )
            )
            s.commit()
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("user-A"), "memA", "memA2")
        assert resp.status_code == 200, resp.text
        with session_factory() as s:
            edges = s.exec(select(MemoryEdge)).all()
            assert len(edges) == 1
            assert edges[0].user_id == "user-A", "自己的边也要带属主（票 03 不回退）"

    def test_admin_can_link_any_memories(self, edge_env):
        """admin 全权：运维维护关联关系不被挡住。"""
        session_factory, _ = edge_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("ops", admin=True), "memA", "memB")
        assert resp.status_code == 200, resp.text

    def test_nonexistent_memory_still_404s(self, edge_env):
        """端点不存在时保持原有 404 语义（不被新校验改掉）。"""
        session_factory, _ = edge_env
        _seed(session_factory)
        with TestClient(_app()) as c:
            resp = _mk_edge(c, _principal("user-A"), "memA", "mem-nope")
        assert resp.status_code == 404, f"端点不存在应 404，实得 {resp.status_code}"


def _app():
    from lantai.api.app import app

    return app
