"""票 20：`POST /obsidian/sync` 补身份 + verbatim 去重补归属 + 实体/边补归属。

**先说影响**：A 提交一条 note，其内容的 sha256 **撞上** B 的一条 verbatim 记忆
（读同一篇公开文档即可构造）→ verbatim 去重无归属过滤 → 返回 **B 的** `memory_id`
→ `sync_obsidian_note` 拿这个 id 取到 B 的行 → 下一轮 sync 时 **A 的实体名从
B 的记忆里连出去**（`links` 边方向是「笔记 → 实体」，所以污染是「从 B 连出去」，
不是票面写的「连进 B」）。

探针实证（`.scratch/readside-gaps/probe_round14.py`）：A 两次 sync 后，
B 的记忆行长出指向 A 指定的实体的 `links` 边，且返回值 `note_id` 就是 B 的 ULID
（ULID 是票 15/16/17 都需要的前置预言机）。

**票面漏了第三个缺口**（`.scratch/readside-gaps/probe_round14b.py` 实证）：
票面说「根治点在票 17」，但票 17 **挡不住**。B 自己
`expand_graph_associations` 照样展开出 A 的实体——因为 `_get_or_create_entity`
造实体时**不填任何归属**，实体行 `user_id` 恒 NULL，`_owns` 判「NULL 属主可见」
就放行了。污染通道是「**无主实体 + NULL 可见口径**」，不是边本身：
票 17 修的是「沿**别人的**边走」，这里是「沿**自己的**边走到**无主**实体」。

**NULL 属主 verbatim 判重复，不判新建**（票面口径 4 的价值观选择，已实测）：
真实库 **391 条 verbatim 全部 `user_id` 为 NULL**。verbatim 是**内容寻址**
（sha256 即 id 语义），同一段文本就是同一条记忆；属主只是标注、不是身份判据。
判新建会让真实库里每一条既有无主 verbatim 都无法去重，同一内容每 sync 一次
就多存一份——把内容寻址退化成多份存储。

**纪律**：不 mock 被测函数的内部计算。这里 mock 的只有外部依赖
（embedding 网络、向量存储），被测的 SQL / FTS / 边沉淀全程真实执行。
"""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.schemas import ObsidianSyncReq
from lantai.models.tables import MemoryEdge, MemoryItem

SECRET_ENTITY = "A 的秘密实体"


def _principal(user_id: str | None, *, role: str = "user", allowed_lanes=None):
    """构造主体。

    `allowed_lanes=None` = **未绑定、不限泳道**（`acl.ensure_can_delete` 口径），
    `[]` = 绑定了但零泳道允许——两者语义不同，别用 `or []` 抹掉区别。
    """
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=allowed_lanes, role=role)


@pytest.fixture()
def obs_env():
    """内存 SQLite 真实建表 + FTS 初始化；只 patch 外部依赖。"""
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

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.services.memory_service.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
    ):
        yield session_factory, engine, vector_store_mock


def _verbatim(session_factory, mid: str, user_id: str | None, content: str):
    """按 `sync_obsidian_note` 的寻址口径落一条 verbatim 行。

    `add_raw_memory` 算 `sha256(req.content)`，而 obsidian 传的是
    `f"{title}\\n\\n{content}"`——key 必须按**完整串**算，否则测的是
    「哈希不撞」而不是「撞上了会怎样」。
    """
    import hashlib

    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mid,
                user_id=user_id,
                tenant_id=None,
                memory_type="verbatim",
                key=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                title=mid,
                content=content,
                lane="general",
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


def _note_text(title: str, body: str) -> str:
    """与 `sync_obsidian_note` 的拼接口径逐字一致。"""
    return f"{title}\n\n{body}".strip() if title else body


def _edges_from(session_factory, source_id: str) -> list:
    """从某行**连出去**的边——污染方向是「从 B 连出去」，别查反了。

    第一版探针查的是 `target=B`，得出「没污染」的错结论。
    """
    with session_factory() as s:
        return list(
            s.exec(select(MemoryEdge).where(MemoryEdge.source_memory_id == source_id)).all()
        )


def _entity_contents(session_factory, source_id: str) -> list[str]:
    """B 的记忆连出去的那些边，目标实体的正文。"""
    with session_factory() as s:
        out = []
        for e in _edges_from(session_factory, source_id):
            ent = s.get(MemoryItem, e.target_memory_id)
            if ent is not None:
                out.append(ent.content)
        return out


def _as(principal, fn):
    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Red 1：verbatim 去重不返回别人的 id ───────────────────────


class TestVerbatimDedupOwnership:
    def test_dedup_does_not_return_other_users_memory_id(self, obs_env):
        """决定性：A 提交与 B 的 verbatim 内容 sha256 相同 →
        **不**返回 B 的 `memory_id`。

        断言落在返回的 `memory_id` **字段值**上，不只看「没建边」——
        没建边可能因为别的原因。
        """
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", f"引用了 [[{SECRET_ENTITY}]] 的正文"
        _verbatim(session_factory, "v-B", "user-B", _note_text(title, body))

        out = sync_obsidian_note(
            ObsidianSyncReq(content=body, title=title, lane="general"),
            principal=_principal("user-A"),
        )

        assert out["note_id"] != "v-B", f"A 沿用了 B 的 verbatim 记忆（拿到别人的 ULID）：{out}"
        assert out.get("dedup") is not True or out["note_id"] != "v-B", (
            f"dedup 把 B 的记忆当成 A 的重复内容：{out}"
        )

    def test_dedup_still_returns_own_memory_id(self, obs_env):
        """Red 3：A 提交**自己**已有的 verbatim 内容 → 照常去重（不能修废）。"""
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        req = ObsidianSyncReq(content="A 自己的笔记正文", title="A1", lane="general")

        r1 = sync_obsidian_note(req, principal=_principal("user-A"))
        r2 = sync_obsidian_note(req, principal=_principal("user-A"))

        assert r1["note_id"] == r2["note_id"], f"自己的内容没去重：{r1} / {r2}"
        assert r2["dedup"] is True, f"第二次没报 dedup：{r2}"
        with session_factory() as s:
            rows = s.exec(select(MemoryItem).where(MemoryItem.memory_type == "verbatim")).all()
        assert len(rows) == 1, f"自己的内容存了多份：{[r.id for r in rows]}"

    def test_null_owner_verbatim_counts_as_duplicate(self, obs_env):
        """Red 4：NULL 属主的 verbatim 行**判重复**，不判新建。

        价值观选择（实测支撑）：真实库 391 条 verbatim **全部** `user_id` 为
        NULL。verbatim 是内容寻址（sha256 即 id 语义），同一段文本就是同一条
        记忆，属主只是标注不是身份判据。判新建会让每一条既有无主 verbatim 都
        无法去重、同一内容每 sync 一次多存一份——把内容寻址退化成多份存储。
        """
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", "引用了无主记忆的正文"
        _verbatim(session_factory, "v-legacy", None, _note_text(title, body))

        out = sync_obsidian_note(
            ObsidianSyncReq(content=body, title=title, lane="general"),
            principal=_principal("user-A"),
        )

        assert out["note_id"] == "v-legacy", (
            f"NULL 属主的 verbatim 不再被判重复（内容寻址被破坏）：{out}"
        )
        with session_factory() as s:
            rows = s.exec(select(MemoryItem).where(MemoryItem.memory_type == "verbatim")).all()
        assert len(rows) == 1, f"同一内容存了多份：{[r.id for r in rows]}"

    def test_admin_and_none_still_dedup_globally(self, obs_env):
        """Red 5：admin / `principal=None` 照旧全局去重（worker 不能空转）。"""
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", "全局去重正文"
        _verbatim(session_factory, "v-B", "user-B", _note_text(title, body))
        req = ObsidianSyncReq(content=body, title=title, lane="general")

        for p in (_principal("admin-user", role="admin"), None):
            out = sync_obsidian_note(req, principal=p)
            assert out["note_id"] == "v-B", (
                f"principal={p!r} 下全局去重失效了（worker 会重复建行）：{out}"
            )


# ── Red 2 / 2b：B 的记忆不长出边，B 的检索不展开出 A 的实体 ────


class TestObsidianPollution:
    def test_other_users_memory_grows_no_edges(self, obs_env):
        """Red 2：A 两次 sync 后，B 的记忆行**不长出**指向 A 的实体的边。

        方向是 `source=B`（「从 B 连出去」）——第一版探针查 `target=B`
        得出「没污染」的错结论。
        """
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", f"引用了 [[{SECRET_ENTITY}]] 的正文"
        _verbatim(session_factory, "v-B", "user-B", _note_text(title, body))
        req = ObsidianSyncReq(content=body, title=title, lane="general")

        sync_obsidian_note(req, principal=_principal("user-A"))
        sync_obsidian_note(req, principal=_principal("user-A"))

        leaked = _entity_contents(session_factory, "v-B")
        assert not leaked, f"B 的记忆长出了指向 A 的实体的边：{leaked}"
        assert _edges_from(session_factory, "v-B") == [], (
            f"B 的记忆行被建了边：{[(e.id, e.target_memory_id) for e in _edges_from(session_factory, 'v-B')]}"
        )

    def test_owner_does_not_expand_others_entities(self, obs_env):
        """Red 2b（决定性，票面漏的第三个缺口）：B 自己沿图走，
        **不展开出 A 的实体**。

        这条锁「无主实体 + NULL 可见」通道。票 17 修不到它——17 修的是
        「沿**别人的**边走」，这里是「沿**自己的**边走到**无主**实体」。
        根因：`_get_or_create_entity` 造实体时不填归属 → 实体行 `user_id`
        恒 NULL → `_owns` 判「NULL 属主可见」→ 放行。
        """
        from lantai.retrieval.graph_retriever import expand_graph_associations
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", f"引用了 [[{SECRET_ENTITY}]] 的正文"
        _verbatim(session_factory, "v-B", "user-B", _note_text(title, body))
        req = ObsidianSyncReq(content=body, title=title, lane="general")
        sync_obsidian_note(req, principal=_principal("user-A"))

        res = expand_graph_associations(["v-B"], max_hops=2, principal=_principal("user-B"))
        contents = [r.get("content") or "" for r in res]
        assert not any(SECRET_ENTITY in c for c in contents), (
            f"B 的检索展开出了 A 的实体（无主实体通道没堵住）：{contents}"
        )

    def test_service_rejects_when_principal_lane_binds_note_lane(self, obs_env):
        """M5 的可杀变体：service 层 `ensure_can_delete` 的 **lane** 维度。

        变异验证里 M5（整条删掉 ensure_can_delete）曾 MISSED。先穷举证明
        （`.scratch/readside-gaps/probe_m5_reachability.py`）：**user 维度
        结构不可达**——M1 的去重归属收窄已堵死「A 拿到 B 的非空属主行」，
        guard 拿到的 `resource_user_id` 只会是 NULL 或自己的 id。

        但 lane 维度**可达**（`probe_m5_lane.py`）：绑定了 lanes 的主体
        推到别的泳道会被拒。这条测试锁那个维度——否则删掉整条 guard
        测试依然全绿，那正是一条「没人能验证的判断」的形状。

        注意**它不该在路由层重演**：路由已前置 lane 校验（先写后拒的
        半成品形状见票面），这里锁的是 service 契约本身。
        """
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env

        out = sync_obsidian_note(
            ObsidianSyncReq(content="越泳道的正文 [[某实体]]", title="T", lane="general"),
            principal=_principal("user-A", allowed_lanes=["fact"]),
        )

        assert out.get("ok") is False, f"越泳道没被拒：{out}"
        assert str(out.get("reason", "")).startswith("forbidden"), (
            f"拒绝原因不是 forbidden（契约形状被改）：{out}"
        )

    def test_service_allows_when_principal_unbound(self, obs_env):
        """`allowed_lanes=None` = 未绑定、不限泳道（不是「零泳道」）。

        这条与上一条互为对偶：锁 `ensure_can_delete` 的
        `lanes is not None and lane not in lanes` 短路。少了它，
        把条件写成 `lane not in lanes` 也能让上面那条绿——而那样会把
        所有未绑定主体（含 worker/脚本）全部误拒。
        """
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env

        out = sync_obsidian_note(
            ObsidianSyncReq(content="未绑定主体的正文 [[某实体]]", title="T", lane="general"),
            principal=_principal("user-A", allowed_lanes=None),
        )

        assert out.get("ok") is not False, f"未绑定主体被误拒：{out}"
        assert out.get("note_id"), f"未绑定主体没建成：{out}"

    def test_own_entities_still_expandable(self, obs_env):
        """Red 7（不能修废）：A sync 自己的笔记 → A 自己仍能沿图展开出
        自己的实体。实体与边补了属主之后，这条保「没有一刀切改成谁都不见」。
        """
        from lantai.retrieval.graph_retriever import expand_graph_associations
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env
        req = ObsidianSyncReq(
            content="引用了 [[我自己的实体]] 的正文", title="A 的笔记", lane="general"
        )

        out = sync_obsidian_note(req, principal=_principal("user-A"))
        res = expand_graph_associations(
            [out["note_id"]], max_hops=2, principal=_principal("user-A")
        )

        contents = [r.get("content") or "" for r in res]
        assert any("我自己的实体" in c for c in contents), (
            f"A 自己的实体展开不出来了（属主补太狠，一刀切）：{contents}"
        )
        # 实体行与边都要落到属主上，否则上面那条 Red 2b 无判据可用
        with session_factory() as s:
            ents = s.exec(select(MemoryItem).where(MemoryItem.memory_type == "entity")).all()
        assert ents and all(e.user_id == "user-A" for e in ents), (
            f"实体行没落到属主上：{[(e.key, e.user_id) for e in ents]}"
        )
        with session_factory() as s:
            links = s.exec(select(MemoryEdge).where(MemoryEdge.relation == "links")).all()
        assert links and all(e.user_id == "user-A" for e in links), (
            f"links 边没落到属主上：{[(e.id, e.user_id) for e in links]}"
        )


# ── Red 6：路由层 ─────────────────────────────────────────────


class TestObsidianRouteOwnership:
    def test_route_passes_principal(self, obs_env):
        """Red 6：`POST /obsidian/sync` 以 A 的身份打过去，
        返回值不是 B 的 id、B 的记忆不长出边。

        同票 19 的形状：`routes_obsidian.py` 里 `get_current_user`
        **已 import**、同文件的 `/verbatim/search` 也用了
        `Depends(get_current_user)`，只有 `/obsidian/sync` 这个 handler 没用。
        """
        from lantai.services.obsidian_service import sync_obsidian_note  # noqa: F401

        session_factory, engine, _ = obs_env
        title, body = "A 的笔记", f"引用了 [[{SECRET_ENTITY}]] 的正文"
        _verbatim(session_factory, "v-B", "user-B", _note_text(title, body))

        def _call():
            with TestClient(app) as c:
                return c.post(
                    "/obsidian/sync",
                    json={"title": title, "content": body, "lane": "general"},
                )

        r = _as(_principal("user-A"), _call)

        assert r.status_code == 200, f"路由报错：{r.status_code} {r.text[:200]}"
        body_json = r.json()
        assert body_json["note_id"] != "v-B", f"HTTP 路径让 A 拿到了 B 的 verbatim id：{body_json}"
        assert _edges_from(session_factory, "v-B") == [], (
            f"HTTP 路径让 B 的记忆长出了边：{_edges_from(session_factory, 'v-B')}"
        )

    def test_route_rejects_lane_before_writing(self, obs_env):
        """路由层 lane 校验必须在**写之前**。

        service 里那道 lane 校验是 `add_raw_memory` **写完之后**才判的
        （`probe_m5_lane.py` 实测：返回 403 可 verbatim 行已落库）。
        「先写后拒」的语义是调用方以为失败、数据却留下了。所以路由前置。

        这条断言两件事：403 状态码，**且落库零行**。
        """
        session_factory, engine, _ = obs_env

        def _call():
            with TestClient(app) as c:
                return c.post(
                    "/obsidian/sync",
                    json={"title": "T", "content": "越泳道正文", "lane": "general"},
                )

        r = _as(_principal("user-A", allowed_lanes=["fact"]), _call)

        assert r.status_code == 403, f"越泳道没被路由挡下：{r.status_code} {r.text[:200]}"
        with session_factory() as s:
            rows = s.exec(select(MemoryItem).where(MemoryItem.memory_type == "verbatim")).all()
        assert rows == [], f"先写后拒：403 了但 verbatim 行已落库：{[x.id for x in rows]}"

    def test_route_allows_unbound_lanes(self, obs_env):
        """`allowed_lanes=None`（未绑定）走 HTTP 也放行——与上一条互为对偶。

        少了它，把路由条件写成 `req.lane not in (ctx.allowed_lanes or [])`
        也能让上一条绿，而那样会把所有未绑定主体全拒在门外。
        """
        session_factory, engine, _ = obs_env

        def _call():
            with TestClient(app) as c:
                return c.post(
                    "/obsidian/sync",
                    json={"title": "T", "content": "未绑定主体正文", "lane": "general"},
                )

        r = _as(_principal("user-A", allowed_lanes=None), _call)

        assert r.status_code == 200, f"未绑定主体被误拒：{r.status_code} {r.text[:200]}"


# ── 不 mock 冒烟 ──────────────────────────────────────────────


class TestObsidianSyncSmoke:
    def test_smoke_no_mock(self, obs_env):
        """真实构造最小输入直调 `sync_obsidian_note`（测试纪律）。"""
        from lantai.services.obsidian_service import sync_obsidian_note

        session_factory, engine, _ = obs_env

        out = sync_obsidian_note(
            ObsidianSyncReq(title="冒烟", content="见 [[冒烟实体]]"),
            principal=_principal("user-A"),
        )

        assert out["dedup"] is False
        assert out["entities"] == ["冒烟", "冒烟实体"]
        assert out["links_created"] == 2
        with session_factory() as s:
            note = s.get(MemoryItem, out["note_id"])
        assert note is not None and note.memory_type == "verbatim"
        assert note.user_id == "user-A", f"verbatim 行没落到属主上：{note.user_id}"
