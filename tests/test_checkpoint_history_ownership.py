"""票 19：`GET /checkpoint?memory_id=` 补身份——A 拉不走 B 的记忆历史版本。

**先说影响**：`routes_checkpoint.py` 的 handler **取了身份却没往下传**——
`session_id` 分支传了 `principal=ctx`（票 09 修的），`memory_id` 分支没有。
`evolution_service.list_checkpoints(memory_id, limit)` 按 `memory_id` 直查，
无任何归属过滤，把每个 checkpoint 的 `model_dump(mode="json")` 全量吐出。

**比读当前版本更糟**：`MemoryCheckpoint.before` / `.after` 是**完整行快照**，
`content` / `title` / `structure` 全在里面。当前版本可能已经被改过，
**历史版本不会**。所以 A 拿到的是 B 这条记忆的编年史。
而 checkpoint 是回滚的原料——先拉历史、再挑一个版本回滚（票 13 已修 rollback
的归属，但读侧这道口子一直开着）。探针实证（`probe_round13.py`）：
A 拉 B 的记忆，2 条 checkpoint 的 `before`/`after` 全文到手。

**修法为什么是 join 而不是先校验记忆**（票 19 调研结论）：
`consolidation_service.py:357` 故意写 `memory_id="cluster_consolidation"` 这个
**伪 id**——它没有 `MemoryItem` 行，是沉潜留痕的对账键。「先按 memory_id 取记忆行」
在它身上必然返回 None，此时 403 / 404 / 放行三个答案全是错的。而 join 天然处理
这种情况：匹配不上就不返回。孤儿 checkpoint（`delete_memory` 不级联删 checkpoint）
同理。所以 (a) join `MemoryItem` 是唯一自洽的选项，不是两个都好选一个。

**NULL 口径 `OR IS NULL`**（读侧一贯，同票 03/04/06/09/10/11/15/17）：
单人部署下老记忆 `user_id` 为 NULL，判不可见会让历史整体消失。

**纪律**：不 mock 被测函数的内部计算。这里 mock 的只有外部存储
（`ChromaVectorStore`），`list_checkpoints` 的 SQL 全程真实执行。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCheckpoint, MemoryItem

SECRET_B = "B 的银行密码是 9527"
SECRET_A = "A 的项目部署在本地内网服务器"
CLUSTER_SECRET = "整簇提纯后的秘密内容"


def _principal(user_id: str | None, *, role: str = "user", tenant_id=None):
    return Principal(user_id=user_id, tenant_id=tenant_id, allowed_lanes=None, role=role)


def _mem(session_factory, mid: str, user_id: str | None, content: str, *, tenant_id=None):
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mid,
                user_id=user_id,
                tenant_id=tenant_id,
                memory_type="preference",
                key=f"k-{mid}",
                title=mid,
                content=content,
                lane="preference",
                domain="user",
                status="active",
                importance=0.5,
                decay_score=0.9,
                tier="working",
                use_count=0,
                helpful_count=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()


def _ckpt(session_factory, cid: str, memory_id: str, version: int, content: str):
    """落一条 checkpoint，`after` 带完整快照形状（真实写入方就是 model_dump）。"""
    with session_factory() as s:
        s.add(
            MemoryCheckpoint(
                id=cid,
                memory_id=memory_id,
                version=version,
                before={"content": f"{content} 的旧版", "title": memory_id},
                after={"content": content, "title": memory_id},
                trigger="manual",
            )
        )
        s.commit()


@pytest.fixture()
def ck_env():
    """真实临时 SQLite + 假向量库（外部存储才 mock）。"""
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
    engine.dispose()


def _as(principal, fn):
    """以指定身份打 HTTP（票 09 同款 helper）。"""
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _contents(out: dict) -> list[str]:
    """把 before/after 的 content 摊平——泄漏判定必须落在正文上。

    不只看列表长度：空列表可能因为本来就没 checkpoint，非空也可能只漏了 id。
    """
    got = []
    for c in out.get("checkpoints", []):
        for side in ("before", "after"):
            v = (c.get(side) or {}).get("content")
            if v:
                got.append(v)
    return got


# ── Red 1：A 拉不走 B 的记忆历史 ────────────────────────────────


class TestCheckpointHistoryNoCrossUserRead:
    def test_other_users_checkpoint_history_not_returned(self, ck_env):
        """决定性：A 查 B 的记忆 → 拿不到 B 的任何正文。

        断言落在 `checkpoints[*].after.content` / `before.content` 上。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-B", "user-B", SECRET_B)
        _ckpt(ck_env, "ck-B-2", "m-B", 2, SECRET_B)
        _ckpt(ck_env, "ck-B-1", "m-B", 1, f"{SECRET_B} 的更早版本")

        out = list_checkpoints("m-B", 20, principal=_principal("user-A"))

        leaked = [c for c in _contents(out) if SECRET_B in c]
        assert not leaked, f"B 的记忆历史正文漏给了 A：{leaked}"
        assert out.get("checkpoints") == [], (
            f"A 看到了 B 的 checkpoint 条目（id 本身也是攻击材料）：{out}"
        )

    def test_other_users_history_not_returned_over_http(self, ck_env):
        """宿主打的是 HTTP：真打 `GET /checkpoint?memory_id=…`。"""
        from lantai.api.app import app

        _mem(ck_env, "m-B", "user-B", SECRET_B)
        _ckpt(ck_env, "ck-B-2", "m-B", 2, SECRET_B)

        def _call():
            with TestClient(app) as c:
                return c.get("/checkpoint", params={"memory_id": "m-B"})

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200, f"HTTP 路径报错：{r.status_code} {r.text[:200]}"
        leaked = [c for c in _contents(r.json()) if SECRET_B in c]
        assert not leaked, f"HTTP 路径把 B 的历史正文给了 A：{leaked}"
        assert r.json().get("checkpoints") == [], f"HTTP 路径返回了 B 的 checkpoint：{r.json()}"

    def test_own_history_still_returned(self, ck_env):
        """Red 2：A 查自己的记忆 → 照常返回全部 checkpoint（不能修废）。"""
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-A", "user-A", SECRET_A)
        _ckpt(ck_env, "ck-A-2", "m-A", 2, SECRET_A)
        _ckpt(ck_env, "ck-A-1", "m-A", 1, f"{SECRET_A} 的旧版")

        out = list_checkpoints("m-A", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 2, f"A 自己的历史拿不全：{out}"
        assert any(SECRET_A in c for c in _contents(out)), f"A 自己的正文没回来：{_contents(out)}"

    def test_mixed_users_own_still_visible(self, ck_env):
        """Red 8：join 条件写反会把 A 也滤掉。A 与 B 都有 checkpoint 时，
        A 查自己的那条仍拿得到。"""
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-A", "user-A", SECRET_A)
        _mem(ck_env, "m-B", "user-B", SECRET_B)
        _ckpt(ck_env, "ck-A", "m-A", 1, SECRET_A)
        _ckpt(ck_env, "ck-B", "m-B", 1, SECRET_B)

        out = list_checkpoints("m-A", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 1, f"A 自己被误伤了：{out}"
        assert any(SECRET_A in c for c in _contents(out)), f"A 自己的正文没回来：{_contents(out)}"

    def test_cross_tenant_checkpoint_not_returned(self, ck_env):
        """同 user_id 不同租户 → 仍要挡住（租户是独立维度）。

        票 18 的跨租户用例证明「同 user_id、不同租户」是真形状（写侧
        `ensure_can_delete` 挡它），读侧不挡就是同一道口子换个方向开。

        但**不能写成严格 `tenant_id == viewer_tenant`**：
        `auth.py:143` 的 tenant 是客户端 header 自报、无租户注册表，
        严格相等会让同一个人不带 header 时连自己 NULL 租户的老行都看不见
        （`.scratch/readside-gaps/probe_tenant_semantics.py` 实证）。
        故取 `ensure_can_delete` 读侧同款：双方都非空且不同才挡。
        """
        from lantai.services.evolution_service import list_checkpoints

        # 行必须真的落在另一个租户上——否则测的是「NULL 租户可见」，不是跨租户
        _mem(ck_env, "m-T", "user-A", "别租户的记忆", tenant_id="tenant-X")
        _ckpt(ck_env, "ck-T", "m-T", 1, "别租户的记忆")

        out = list_checkpoints("m-T", 20, principal=_principal("user-A", tenant_id="tenant-Y"))

        assert out.get("checkpoints") == [], f"跨租户的历史没挡住：{out}"

    def test_own_tenant_history_still_visible(self, ck_env):
        """同租户 → 照常返回（不能修废，这是上面那条的对照组）。

        少了这条，「一律按租户挡」的实现也能让上面的用例绿。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-T", "user-A", "自己租户的记忆", tenant_id="tenant-X")
        _ckpt(ck_env, "ck-T", "m-T", 1, "自己租户的记忆")

        out = list_checkpoints("m-T", 20, principal=_principal("user-A", tenant_id="tenant-X"))

        assert len(out.get("checkpoints", [])) == 1, (
            f"同租户自己的历史看不见了（租户过滤写死了）：{out}"
        )

    def test_null_tenant_row_visible_without_tenant_header(self, ck_env):
        """不带 X-Tenant-Id 时，NULL 租户老行仍可见（租户过滤不能误伤）。

        严格 `tenant_id == viewer_tenant` 的实现会在这里露出马脚：
        viewer_tenant 为 None 时把所有人的行都挡掉。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-legacy", "user-A", "老行：租户为 NULL")
        _ckpt(ck_env, "ck-legacy", "m-legacy", 1, "老行：租户为 NULL")

        out = list_checkpoints("m-legacy", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 1, (
            f"不带租户 header 时自己的老行也看不见了：{out}"
        )

    def test_null_tenant_row_visible_with_tenant_header(self, ck_env):
        """**带了**租户 header 时，NULL 租户老行仍要可见。

        变异 M6 教会我的：上面那条用的是「不带 header」的主体，
        而 M6（`tenant_id == viewer_tenant` 严格相等）在那种主体下
        `viewer_tenant` 本就是 None、条件整段不生效——所以那条用例
        **杀不掉 M6**。真正能区分的形状是「header 非空 + 行租户为 NULL」：
        当前实现 `OR tenant_id IS NULL` 放行，严格相等则挡掉。
        （`.scratch/readside-gaps/probe_m6_m7_diff.py` 实证：同一形状下
        当前实现返回 ck-null。）
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-legacy", "user-A", "老行：租户为 NULL")
        _ckpt(ck_env, "ck-legacy", "m-legacy", 1, "老行：租户为 NULL")

        out = list_checkpoints("m-legacy", 20, principal=_principal("user-A", tenant_id="tenant-X"))

        assert len(out.get("checkpoints", [])) == 1, (
            f"带了租户 header 就看不到自己 NULL 租户的老行了（租户过滤写太死）：{out}"
        )

    def test_own_tenant_tagged_row_visible_without_tenant_header(self, ck_env):
        """变异 M7：`viewer_tenant or "__never__"` 把租户条件变成**恒生效**。

        M7 改的是非 admin 且**不带**租户 header 的主体：`None or "__never__"`
        让条件从「不生效」变成「按一个永不匹配的租户过滤」——于是该主体
        连自己**有租户标签**的老行都看不见（只有 NULL 租户行因 `IS NULL`
        侥幸漏过，所以前两条用例杀不掉它）。

        当前口径是「**双方都非空且不同才挡**」（同 `ensure_can_delete`，
        票 18 跨租户用例的读侧同款）：主体没自报租户时，租户维度无从比起，
        不该挡任何东西。反过来的「写死恒生效」会把多租户用户的历史
        在他们不带 header 时静默清空。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-x", "user-A", "有租户标签的自己的记忆", tenant_id="tenant-X")
        _ckpt(ck_env, "ck-x", "m-x", 1, "有租户标签的自己的记忆")

        out = list_checkpoints("m-x", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 1, (
            f"主体不带租户 header 时，自己有租户标签的历史被挡了：{out}"
        )

    def test_admin_and_internal_not_blocked_by_tenant(self, ck_env):
        """变异 M7：`viewer_tenant or "__never__"` 会把租户条件套到
        admin 与 `principal=None` 路径上，让它们也查不到 NULL 租户行。

        这是「内部路径不能空转」的租户版本——worker 与 CLI 不带 header，
        一视同仁地按租户挡就是把它们的可见面砍成 0。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-legacy", "user-A", "老行：租户为 NULL")
        _ckpt(ck_env, "ck-legacy", "m-legacy", 1, "老行：租户为 NULL")

        for p in (
            _principal("admin-user", role="admin", tenant_id="tenant-Z"),
            None,
        ):
            out = list_checkpoints("m-legacy", 20, principal=p)
            assert len(out.get("checkpoints", [])) == 1, (
                f"principal={p!r} 被租户条件挡住了（worker/CLI 会空转）：{out}"
            )


# ── Red 3 / 4：NULL 老行可见，admin 全见 ───────────────────────


class TestCheckpointHistoryOwnerBoundary:
    def test_null_owner_legacy_memory_history_visible(self, ck_env):
        """Red 3：NULL 属主老记忆的 checkpoint 对非 admin 仍可见。

        单人部署下老记忆 `user_id` 为 NULL，判不可见会让历史整体消失
        （读侧一贯 `OR IS NULL` 口径，同票 03/04/06/09）。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-legacy", None, "老数据：没有属主")
        _ckpt(ck_env, "ck-legacy", "m-legacy", 1, "老数据：没有属主")

        out = list_checkpoints("m-legacy", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 1, (
            f"NULL 属主老记忆的历史看不见了（单人部署会被修废）：{out}"
        )

    def test_admin_sees_all_including_pseudo_id_and_orphan(self, ck_env):
        """Red 4：admin 照见全部，**含伪 id 与孤儿 checkpoint**。

        `cluster_consolidation` 是沉潜的伪 id（没有 `MemoryItem` 行），
        孤儿 checkpoint 是行已删、历史还在（`delete_memory` 不级联删
        checkpoint）。这两类只有 admin 该看见，但 admin 必须看得见——
        否则沉潜对账与运维排查看不到任何东西。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-B", "user-B", SECRET_B)
        _ckpt(ck_env, "ck-B", "m-B", 1, SECRET_B)
        _ckpt(ck_env, "ck-cluster", "cluster_consolidation", 1, CLUSTER_SECRET)
        _ckpt(ck_env, "ck-ghost", "m-gone", 1, "行已删的历史正文")

        out = list_checkpoints("cluster_consolidation", 20, principal=_principal("a", role="admin"))
        assert len(out.get("checkpoints", [])) == 1, (
            f"admin 看不到 cluster_consolidation 伪 id checkpoint：{out}"
        )
        assert any(CLUSTER_SECRET in c for c in _contents(out)), (
            f"admin 看不到伪 id 的正文：{_contents(out)}"
        )

        out2 = list_checkpoints("m-gone", 20, principal=_principal("a", role="admin"))
        assert len(out2.get("checkpoints", [])) == 1, f"admin 看不到孤儿 checkpoint：{out2}"

    def test_internal_call_principal_none_unfiltered(self, ck_env):
        """Red 4 另一半：`principal=None`（worker/CLI/MCP）不过滤。"""
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-B", "user-B", SECRET_B)
        _ckpt(ck_env, "ck-B", "m-B", 1, SECRET_B)

        out = list_checkpoints("m-B", 20, principal=None)

        assert len(out.get("checkpoints", [])) == 1, (
            f"principal=None 的内部路径被过滤了（worker 会空转）：{out}"
        )

    def test_non_admin_cannot_see_pseudo_id_cluster_history(self, ck_env):
        """普通用户看不到 `cluster_consolidation`（整簇提纯结果 + 碎片 id）。

        走 (b)「先取记忆行再校验」的实现会在这里露出马脚：伪 id 没有记忆行，
        无论判越权还是放行都是错的；join 天然匹配不上 → 空。
        """
        from lantai.services.evolution_service import list_checkpoints

        _ckpt(ck_env, "ck-cluster", "cluster_consolidation", 1, CLUSTER_SECRET)

        out = list_checkpoints("cluster_consolidation", 20, principal=_principal("user-A"))

        assert out.get("checkpoints") == [], f"普通用户看到了沉潜伪 id 的 checkpoint：{out}"
        assert not [c for c in _contents(out) if CLUSTER_SECRET in c], (
            f"普通用户看到了整簇提纯正文：{_contents(out)}"
        )

    def test_non_admin_cannot_see_orphan_checkpoint(self, ck_env):
        """孤儿 checkpoint（记忆行已删）对非 admin 不可见。

        `delete_memory` 只删 `MemoryItem`，不动 `MemoryCheckpoint`
        （`promoter.py` 无级联），所以**历史比行活得久**。行没了就没有
        属主可判，放行等于把已删记忆的编年史敞开。
        """
        from lantai.services.evolution_service import list_checkpoints

        _ckpt(ck_env, "ck-ghost", "m-gone", 1, "行已删的历史正文")

        out = list_checkpoints("m-gone", 20, principal=_principal("user-A"))

        assert out.get("checkpoints") == [], f"孤儿 checkpoint 漏给了普通用户：{out}"


# ── Red 5：同 handler 双分支互不影响 ────────────────────────────


class TestCheckpointRouteDualBranch:
    def test_session_id_branch_still_returns_own(self, ck_env):
        """Red 5：`session_id` 分支行为不变（票 09 已修，别被本票改坏）。"""
        from lantai.api.app import app
        from lantai.services.checkpoint_service import write_session_checkpoint

        write_session_checkpoint(
            "sess-A",
            {"cp_active_intent": "A 的当前意图", "cp_current_work": "A 的工作现场"},
            principal=_principal("user-A"),
        )

        def _call():
            with TestClient(app) as c:
                return c.get("/checkpoint", params={"session_id": "sess-A"})

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200
        body = r.json()
        assert body and body.get("session_id") == "sess-A", f"session_id 分支坏了：{body}"
        assert "A 的当前意图" in str(body.get("blocks", {})), (
            f"session_id 分支拿不到自己的底本：{body}"
        )

    def test_session_id_branch_still_excludes_others(self, ck_env):
        """Red 5 另一面：session_id 分支**仍然**挡别人（不能为了让本票绿而放松）。"""
        from lantai.api.app import app
        from lantai.services.checkpoint_service import write_session_checkpoint

        write_session_checkpoint(
            "sess-B",
            {"cp_active_intent": SECRET_B, "cp_current_work": SECRET_B},
            principal=_principal("user-B"),
        )

        def _call():
            with TestClient(app) as c:
                return c.get("/checkpoint", params={"session_id": "sess-B"})

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200
        assert SECRET_B not in r.text, f"session_id 分支把 B 的底本给了 A：{r.text[:200]}"

    def test_memory_id_branch_without_session_id(self, ck_env):
        """两个参数都没传时不应炸（`memory_id=""` → 空列表，同改前行为）。"""
        from lantai.api.app import app

        def _call():
            with TestClient(app) as c:
                return c.get("/checkpoint")

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200, f"空参数路径报错：{r.status_code} {r.text[:200]}"
        assert r.json().get("checkpoints") == [], f"空参数应返回空列表：{r.json()}"


# ── Red 6：limit 与排序不能因 join 失效 ────────────────────────


class TestCheckpointHistoryQuerySemantics:
    def test_limit_and_order_preserved(self, ck_env):
        """join 不能把 `order_by(version.desc())` 与 `limit` 弄丢。"""
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-A", "user-A", SECRET_A)
        for v in range(1, 6):
            _ckpt(ck_env, f"ck-A-{v}", "m-A", v, f"版本 {v}")

        out = list_checkpoints("m-A", 2, principal=_principal("user-A"))

        rows = out.get("checkpoints", [])
        assert len(rows) == 2, f"limit 没生效：{[r.get('id') for r in rows]}"
        assert [r["version"] for r in rows] == [5, 4], (
            f"版本倒序没保住：{[r['version'] for r in rows]}"
        )

    def test_returns_same_shape_as_before(self, ck_env):
        """返回形状不能动（前端与 MCP 都在消费 `checkpoints` 键）。"""
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-A", "user-A", SECRET_A)
        _ckpt(ck_env, "ck-A", "m-A", 3, SECRET_A)

        out = list_checkpoints("m-A", 20, principal=_principal("user-A"))

        assert set(out.keys()) == {"checkpoints"}, f"返回的键变了：{set(out.keys())}"
        row = out["checkpoints"][0]
        for field in ("id", "memory_id", "version", "before", "after", "trigger"):
            assert field in row, f"checkpoint 行少了 {field} 字段：{sorted(row.keys())}"


# ── 不 mock 冒烟：真实调用主路径不炸 ─────────────────────────────


class TestCheckpointHistorySmoke:
    def test_smoke_no_mock(self, ck_env):
        """真实构造最小输入直调 `list_checkpoints`（测试纪律）。

        直接 import 函数、真 SQL、真 session，不 patch 任何被测内部逻辑。
        """
        from lantai.services.evolution_service import list_checkpoints

        _mem(ck_env, "m-S", None, "种子记忆")
        _ckpt(ck_env, "ck-S", "m-S", 1, "种子记忆")

        out = list_checkpoints("m-S", 20, principal=_principal("user-A"))

        assert len(out.get("checkpoints", [])) == 1
        assert out["checkpoints"][0]["memory_id"] == "m-S"

    def test_smoke_route_smoke_no_mock(self, ck_env):
        """HTTP 冒烟：GET /checkpoint?memory_id= 主路径真实跑通。"""
        from lantai.api.app import app

        _mem(ck_env, "m-S", None, "种子记忆")
        _ckpt(ck_env, "ck-S", "m-S", 1, "种子记忆")

        def _call():
            with TestClient(app) as c:
                return c.get("/checkpoint", params={"memory_id": "m-S"})

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200, f"冒烟失败：{r.status_code} {r.text[:200]}"
        assert len(r.json().get("checkpoints", [])) == 1
