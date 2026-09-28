"""票 `.scratch/mcp-identity-gaps/issues/12`：`build_graph` 归属三缺。

**先说影响**：无身份调记忆关系星图，能看到别人的记忆节点。

实证（`.scratch/mcp-identity-gaps/probe_12_sibling_shapes.py`，修前）：

```
A api_key admin   -> nodes=0  []              ← 被自己 "api_key" 属主滤空
B None admin      -> nodes=2  ['m-A', 'm-B']
C explicit user-A -> nodes=0  []              ← 只滤 m-A，边另一端 m-B 不在池 → 边被丢
D None            -> nodes=2  ['m-A', 'm-B']  ← 露出 user-B 的节点（越权方向）
```

`lantai/ops/graph.py:52-64` 是 `build_memories_page` **修前**的同族形状：
`if principal:` 块内只判 `user_id` 非空就加归属条件，**没有 `is_admin` 豁免、
没有 `OR IS NULL`、没有 None 收敛**。票 03 / 07 / 08 三次修正都没跟上这里。

修法（与仓内多数派一致，不新造口径）：
- None → 收敛到 `viewer_of(None)`（`"default"`）+ `OR IS NULL`（同票 07/03）
- admin → 不加归属过滤（同票 08）
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.ops.graph import build_graph
from lantai.storage.fts import init_fts


def _admin(uid=None):
    return Principal(user_id=uid, role="admin", allowed_lanes=None)


def _user(uid):
    return Principal(user_id=uid, role="user", allowed_lanes=None)


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed_all(engine):
    """四种属主的记忆 + 一条 A→B 的边 + 一条 NULL 属主老边。

    边必须 seed：`build_graph` 的节点入选条件是「参与入选边任一端」，
    没有边则候选池虽非空却没有节点可入选，四种形态全返回空图——
    那样会误判成「没有差异」。
    """
    now = utcnow()
    rows = [
        ("m-legacy", None, "NULL 属主的老记忆"),
        ("m-def", "default", "default 属主的记忆"),
        ("m-B", "user-B", "B 的私有记忆"),
        ("m-A", "user-A", "A 的记忆"),
    ]
    with Session(engine) as s:
        for mid, owner, content in rows:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
                    lane="general",
                    importance=0.5,
                    status="active",
                    user_id=owner,
                    created_at=now,
                    updated_at=now,
                )
            )
        # A→B：跨用户边，用来验证 C 形态不会因它丢掉自己的图
        s.add(
            MemoryEdge(
                id="e-ab",
                source_memory_id="m-A",
                target_memory_id="m-B",
                relation="supports",
                confidence=0.9,
                user_id="user-A",
                created_at=now,
            )
        )
        # A→legacy：user-A **自己名下的池内边**。必须有这条——
        # `build_graph` 的节点入选条件是「参与入选边任一端，或携带 scene_id」，
        # 只给 m-A 连跨用户边的话，边另一端 user-B 被归属过滤出池 → 边丢 →
        # m-A 无 scene_id → 节点不入选。那是星图本身的设计（只画有关系的），
        # 不是归属 bug；但归属过滤**会改变池从而改变图拓扑**，这个语义后果
        # 必须有用例钉住。
        s.add(
            MemoryEdge(
                id="e-a-legacy",
                source_memory_id="m-A",
                target_memory_id="m-legacy",
                relation="supports",
                confidence=0.9,
                user_id="user-A",
                created_at=now,
            )
        )
        # NULL 属主老边：既定口径「老边人人可读」
        s.add(
            MemoryEdge(
                id="e-legacy",
                source_memory_id="m-legacy",
                target_memory_id="m-def",
                relation="supports",
                confidence=0.9,
                user_id=None,
                created_at=now,
            )
        )
        s.commit()


def _nodes(engine, principal):
    with Session(engine) as s:
        out = build_graph(s, 100, principal=principal)
    return {n["id"] for n in out["nodes"]}


class TestNonePrincipalDoesNotLeak:
    """票 12 核心：无身份不再露出别人的节点。"""

    def test_none_principal_excludes_other_users(self, engine):
        _seed_all(engine)

        got = _nodes(engine, None)

        assert "m-B" not in got, f"principal=None 星图露出了 user-B 的记忆节点：{sorted(got)}"
        assert "m-A" not in got, f"principal=None 星图露出了 user-A 的记忆节点：{sorted(got)}"

    def test_none_principal_still_sees_default_and_legacy(self, engine):
        """单人部署不能空转：None 时仍见 default 自己的 + NULL 老行。"""
        _seed_all(engine)

        got = _nodes(engine, None)

        assert "m-def" in got, f"default 自己的节点不见了：{sorted(got)}"
        assert "m-legacy" in got, f"NULL 老行节点不见了：{sorted(got)}"

    def test_none_principal_sees_exactly_default_and_legacy(self, engine):
        """None 视角**恰好**是 default + NULL 老行，不多不少。

        差集靶子：删整个归属块（M1）与只删 `OR IS NULL` 半边（M3）在显式
        user 视角上效果相同（都让 m-legacy 消失），在 admin 视角上与
        M4 相同——但**只有 M1 会让 None 视角看到别人的节点**。这条把
        None 视角钉成一个精确集合，三个变异体各有独立杀伤。
        """
        _seed_all(engine)

        got = _nodes(engine, None)

        assert got == {"m-def", "m-legacy"}, (
            f"principal=None 该恰好收敛到 default + NULL 老行，实际 {sorted(got)}"
        )


class TestExplicitUserKeepsOwnGraph:
    """显式 user 不再因跨用户边丢掉自己的整张图。"""

    def test_explicit_user_sees_own_and_null(self, engine):
        _seed_all(engine)

        got = _nodes(engine, _user("user-A"))

        assert "m-A" in got, (
            f"user-A 自己的节点不见了——跨用户边另一端不在池，边被丢，图整体空白：{sorted(got)}"
        )
        assert "m-legacy" in got, f"NULL 属主老行不可见（票 03 半边缺失）：{sorted(got)}"
        assert "m-B" not in got, f"user-A 见到了 user-B 的节点：{sorted(got)}"


class TestAdminTwoFormsAgree:
    """票 08 的形状：两种 admin 构造必须一致且全表。"""

    def test_api_key_admin_and_none_admin_agree(self, engine):
        _seed_all(engine)

        got_a = _nodes(engine, _admin("api_key"))
        got_b = _nodes(engine, _admin(None))

        assert got_a == got_b, (
            f"admin 两种形态不一致：api_key 见 {sorted(got_a)}，None 见 {sorted(got_b)}"
        )

    def test_admin_sees_all_nodes(self, engine):
        _seed_all(engine)

        got = _nodes(engine, _admin("api_key"))

        assert got == {"m-legacy", "m-def", "m-B", "m-A"}, (
            f"admin 该全表却只见 {sorted(got)}——星图对管理员空白，排障会误导"
        )
