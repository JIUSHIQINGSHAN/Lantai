"""终端读路由归属：`GET /terminal/memory/{memory_id}` 补 `_check_ownership`。

票 `.scratch/readside-gaps/24-terminal-get-memory-no-ownership.md`

**先说影响**：`get_single_memory`（routes_terminal.py:227）签名里有
`principal=Depends(get_current_user)`，函数体却一次都没引用它——同文件另外
9 个 `/terminal/memory/*` 路由全过 `_check_ownership`（:367，P0 票04 建的），
只有这条读路由漏了。后果：A 拿 B 的 memory_id 一次请求拿到**完整 41 字段**
（content 全文、structure/provenance/tags JSON、confidence/importance/
decay、lifecycle_status、superseded_by）。

**这条比"没有 Depends"更危险**——代码审查看到 `principal=Depends(...)`
就会打勾。本轮路线普查 193 个入口里它是唯一一条"签名有、函数体不用"的形状。

测试纪律（AGENTS.md）：不 mock 内部逻辑——真实内存 SQLite 建表、真
`get_db_conn`（monkeypatch `db_module.engine`）、路由层真 `TestClient`。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem


@pytest.fixture()
def env(monkeypatch):
    """真 DB + 真 HTTP。`routes_terminal` 走 `get_db_conn()`（raw engine），
    所以 monkeypatch `db_module.engine` 就够——**不要**用
    `dependency_overrides`，那条路只对 `Depends(get_session)` 生效
    （票 03 的教训：对象同一性）。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)

    now = utcnow()

    def add(mid, user_id, content, *, lane="general", status="active"):
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id=mid,
                    content=content,
                    memory_type="text",
                    user_id=user_id,
                    lane=lane,
                    confidence=0.8,
                    importance=0.7,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()

    # A / B 各一条 + 一条 NULL 属主老行 + 一条别的 lane
    add("mem-A-own", "user-A", "A 自己的记忆：下周要发版")
    add("mem-B-secret", "user-B", "B 的私有记忆：收购对家的报价底牌")
    add("mem-null-old", None, "没有属主的老记忆：2024 年的项目纪要")
    add("mem-B-otherlane", "user-B", "B 的私有记忆（别的 lane）", lane="secret")

    yield engine, add
    app.dependency_overrides.pop(get_current_user, None)


def _principal(user_id, *, role="user", lanes=("general",), tenant=None):
    from lantai.core.auth import Principal

    return Principal(
        user_id=user_id,
        role=role,
        allowed_lanes=list(lanes),
        tenant_id=tenant,
    )


A = _principal("user-A")
ADMIN = _principal("admin-1", role="admin")


def _get(principal, mid):
    """打一次真实 GET。**不复用 TestClient 的 lifespan**——`with TestClient(app)`
    进出一次会跑 startup/shutdown，把 `db_module.engine` 重置回宿主库
    （探针场景 2-5 全 404 的根因）。每次新建客户端，只打这一次请求。
    """
    app.dependency_overrides[get_current_user] = lambda: principal
    return TestClient(app).get(f"/terminal/memory/{mid}")


# ── Red 1（决定性）：A 取不到 B 的记忆 ────────────────────────────


class TestCrossUserReadBlocked:
    def test_a_cannot_read_b_memory(self, env):
        """A 取 B 的记忆 → 403，且响应体不含 content。

        修前：200 + 41 个字段原样吐出（含 content 全文）。
        """
        resp = _get(A, "mem-B-secret")
        assert resp.status_code == 403, (
            f"A 读到了 B 的记忆（该 403，实际 {resp.status_code}）：{resp.text[:300]}"
        )
        assert "收购对家的报价底牌" not in resp.text, "403 的响应体里仍带 B 的记忆正文"

    def test_lane_mismatch_also_blocked(self, env):
        """资源在 A 的 allowed_lanes 之外的 lane → 403。

        `_check_ownership` → `ensure_can_delete` 的 lane 判据。修前这条
        也是 200（principal 整个没被用）。

        **判据必须让 lane 成为唯一的拦截者**：若资源的 user_id 也跟 A 不同，
        用户判据先拦住，lane 分支永远不会成为决定性的一步——那正是变异
        M4（删掉 lane 判据）能存活的原因。所以这条的资源属主是 **A 自己**，
        lane 才是唯一的拦截者。
        """
        engine, add = env
        add("mem-A-otherlane", "user-A", "A 的记忆但放在 rule lane", lane="rule")
        resp = _get(A, "mem-A-otherlane")
        assert resp.status_code == 403, (
            f"别的 lane 的资源该拒（实际 {resp.status_code}）：{resp.text[:200]}"
        )

    def test_own_lane_readable(self, env):
        """正面对照：lane 在 A 的 allowed_lanes 内 → 200。

        没有这条，"lane 一律拒"的实现也能让上面那条测试绿（变异等价）。
        """
        engine, add = env
        add("mem-A-rulelane-ok", "user-A", "A 的 rule lane 记忆", lane="rule")
        resp = _get(_principal("user-A", lanes=("general", "rule")), "mem-A-rulelane-ok")
        assert resp.status_code == 200, f"自己 lane 内的资源该放行：{resp.text[:200]}"

    def test_cross_tenant_blocked(self, env):
        """跨租户 → 403（`ensure_can_delete` 的租户判据）。

        资源 user_id 留空、tenant 与主体不同：这样**租户判据是唯一的拦截者**
        （否则用户判据先拦，删掉租户判据的变异杀不掉）。owner 留空正好落在
        "无归属老行只受 lane 约束"的口径上，剩下的就只有租户这一道。
        """
        engine, add = env
        from sqlmodel import Session

        from lantai.core.time import utcnow
        from lantai.models.tables import MemoryItem

        now = utcnow()
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id="mem-other-tenant",
                    content="别的租户的记忆",
                    memory_type="text",
                    user_id=None,
                    tenant_id="tenant-other",
                    lane="general",
                    confidence=0.8,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()
        resp = _get(_principal("user-A", tenant="tenant-A"), "mem-other-tenant")
        assert resp.status_code == 403, (
            f"跨租户的资源该拒（实际 {resp.status_code}）：{resp.text[:200]}"
        )


# ── Red 2-5：口径边界（修完必须仍成立，否则把人修废） ─────────────


class TestScopeBoundaries:
    def test_a_reads_own_memory(self, env):
        """A 取自己的记忆 → 200 且 content 正确（正常路径不能坏）。"""
        resp = _get(A, "mem-A-own")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content"] == "A 自己的记忆：下周要发版"
        # 41 个字段原样吐出的行为不变——本票只加归属校验，不改响应形状
        assert "confidence" in body and "importance" in body

    def test_admin_reads_any_memory(self, env):
        """admin 全量（不能把运维修废）。"""
        resp = _get(ADMIN, "mem-B-secret")
        assert resp.status_code == 200, f"admin 被挡住了：{resp.text[:200]}"
        assert resp.json()["content"] == "B 的私有记忆：收购对家的报价底牌"

    def test_null_owner_legacy_row_readable(self, env):
        """NULL 属主老行可读——单人部署下这是大量真实数据。

        判不可见等于读不了历史记忆，那是事故。`ensure_can_delete` 对
        `resource_user_id` 为空的资源只受 lane 约束。
        """
        resp = _get(A, "mem-null-old")
        assert resp.status_code == 200, f"NULL 属主老行被藏了（该是 200）：{resp.text[:200]}"
        assert resp.json()["content"] == "没有属主的老记忆：2024 年的项目纪要"

    def test_missing_id_is_404_not_403(self, env):
        """不存在的 id → 404，**不是 403**。

        语义必须分开：404 是"不存在"，403 是"存在但不归你"。混用会让
        A 拿 403/404 的差异当 id 枚举预言机——那正是票 19 记录过的攻击材料。
        """
        resp = _get(A, "mem-does-not-exist")
        assert resp.status_code == 404, (
            f"不存在的 id 该是 404（实际 {resp.status_code}）：{resp.text[:200]}"
        )


# ── 回归护栏 ───────────────────────────────────────────────────────


class TestNoRegressionOnWriteSide:
    def test_write_handlers_still_carry_principal(self):
        """笔削四操作仍带 principal（P0 票04 的成果，本票不得回潮）。"""
        import inspect

        import lantai.api.routes_terminal as rt

        for name in (
            "update_memory",
            "delete_memory",
            "retract_memory_route",
            "unretract_memory_route",
            "archive_memory_route",
            "unarchive_memory_route",
            "correct_memory_route",
            "revive_consolidated_route",
            "merge_memories",
        ):
            params = inspect.signature(getattr(rt, name)).parameters
            assert "principal" in params, f"{name} 丢了 principal 形参（写侧归属回潮）"

    def test_check_ownership_used_by_get_handler(self):
        """结构性护栏：`get_single_memory` 必须调 `_check_ownership`。

        签名有 `principal` 而函数体不用正是本票的 bug 形状（审查会漏）。
        直接看源码比只打 HTTP 更强——HTTP 断言拦不住"绕路拿到数据"的实现。
        """
        import inspect

        import lantai.api.routes_terminal as rt

        src = inspect.getsource(rt.get_single_memory)
        assert "_check_ownership" in src, (
            "get_single_memory 没调 _check_ownership——本票的 bug 形状回来了"
        )
