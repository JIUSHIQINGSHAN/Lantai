"""票 18：`delete_document` 级联删除补归属——A 删自己的文档，B 的记忆不再被连带物理删除。

**先说影响**：`promoter.delete_memory` 没有任何 principal 形参，`s.get(MemoryItem, id)`
取到就 `s.delete`，再 `sync_fts(None)` + `delete_memory_item` 从 FTS 和向量库除名。
**这是全代码库最具破坏性的操作。** 它的调用方 `source_service.delete_document`
逐个删除「无更多 doc 来源」的目标记忆，**不检查这个记忆属于谁**。

而 `DELETE /documents/{id}`（`routes_sources.py`）**确实**调了 `ensure_can_delete`，
但只校验 `RawDocument` 那一行的 `user_id` / `tenant_id`——边的另一端是**另一张表
的另一行**，路由那道校验够不着它。

**攻击面**：A 有一个文档，其边指向 B 的记忆（票 17 修前 `MemoryEdge` 无归属过滤；
票 16 的无归属 apply 也会造这种边）→ A 删自己的文档 → **B 的记忆被物理删除**，
无 checkpoint、无 undo、无 audit 事件。探针实证（`probe_round12.py`）：行没了、
FTS 索引也没了。

**两道校验管的不是同一行**：路由那道管 `RawDocument`，service 这道管
`MemoryItem`。别让后来人以为重复就删掉其中一道。

**纪律**：不 mock 被测函数的内部计算。这里 mock 的只有外部存储
（vector_store.delete）与文件系统副作用（无）。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem, RawDocument

SECRET_B = "B 的银行密码是 9527"
SECRET_A = "A 的项目部署在本地内网服务器"


def _principal(user_id, *, role="user", allowed_lanes=None):
    from lantai.core.auth import Principal

    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=allowed_lanes, role=role)


def _mem(engine, mid, user_id, content, *, lane="preference"):
    from lantai.storage.fts import sync_fts

    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                user_id=user_id,
                memory_type="preference",
                key=f"k-{mid}",
                title=mid,
                content=content,
                lane=lane,
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
        # 裸 INSERT 不会进 FTS，须显式 sync_fts（否则「FTS 还在吗」断言的是空气）
        sync_fts(s, mid, content)
        s.commit()


def _edge(engine, eid, src, dst, user_id=None):
    with Session(engine) as s:
        s.add(
            MemoryEdge(
                id=eid,
                source_memory_id=src,
                target_memory_id=dst,
                relation="extract",
                confidence=0.9,
                user_id=user_id,
            )
        )
        s.commit()


def _doc(engine, did, user_id):
    with Session(engine) as s:
        s.add(
            RawDocument(
                id=did,
                user_id=user_id,
                source_type="manual",
                source_id=did,
                url=f"file://{did}",
                title=did,
                content="文档正文",
                content_hash=f"hash-{did}",
            )
        )
        s.commit()


@pytest.fixture()
def doc_env(monkeypatch):
    """真实临时 SQLite + 真 FTS5 + 假向量库（外部存储才 mock）。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())
    # 模块级 engine 仅本测试内生效，测试后由 monkeypatch 还原，防顺序污染
    monkeypatch.setattr(db_module, "engine", engine)

    class VS:
        def __init__(self):
            self.deleted = []

        def delete(self, ids):
            self.deleted.extend(ids)

    vs = VS()
    monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: vs)
    yield engine, vs
    engine.dispose()


def _row(engine, mid):
    with Session(engine) as s:
        return s.get(MemoryItem, mid)


def _fts_hit(engine, mid):
    with Session(engine) as s:
        r = s.connection().exec_driver_sql(
            "SELECT count(*) FROM memory_fts WHERE memory_id = ?", (mid,)
        ).fetchone()
        return bool(r and r[0])


def _seed_two_users(engine, *, mem_b_owner="user-B", edge_owner="user-A"):
    _doc(engine, "doc-A", "user-A")
    _mem(engine, "m-A", "user-A", SECRET_A)
    _mem(engine, "m-B", mem_b_owner, SECRET_B)
    _edge(engine, "e-A", "doc-A", "m-A", edge_owner)
    _edge(engine, "e-B", "doc-A", "m-B", edge_owner)


# ── Red 1 / 2：B 的记忆不再被连带物理删除 ──────────────────────


class TestDocumentCascadeNoCrossUserDelete:
    def test_other_users_memory_survives_row_and_fts(self, doc_env):
        """决定性：A 删自己的文档 → B 的记忆行与 FTS 索引都还在。

        断言落在**落库行**与 **FTS 索引**上，不只看返回的 `deleted_memories`
        计数——计数为 0 可能有一堆别的原因。
        """
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _seed_two_users(engine)

        out = delete_document("doc-A", principal=_principal("user-A"))

        assert out.get("ok") is False, f"B 的记忆没有被整体中止挡住：{out}"
        assert "forbidden" in str(out.get("reason", "")), f"中止原因不是越权：{out}"
        b = _row(engine, "m-B")
        assert b is not None and SECRET_B in b.content, (
            f"B 的记忆行被物理删除了：{b}"
        )
        assert _fts_hit(engine, "m-B"), "B 的记忆从 FTS 索引里被删了（删索引比删行更难察觉）"
        assert "m-B" not in vs.deleted, f"B 的记忆被向量库除名了：{vs.deleted}"
        assert out.get("deleted_memories") != 2, (
            f"两条都被当成可删目标了：{out}"
        )
        # 文档与边也必须完好——中止就是什么都没动
        with Session(engine) as s:
            assert s.get(RawDocument, "doc-A") is not None, "中止了文档却被删掉"
            assert len(s.exec(select(MemoryEdge)).all()) == 2, "中止了边却被删掉"

    def test_other_users_memory_survives_over_http(self, doc_env):
        """宿主打的是 HTTP：越权必须是 **403**，不能是 200 + ok:false。

        200 + ok:false 语义上是成功、实际失败，调用方必须翻 body 才知道
        出错了（票 `.scratch/api-error-status/01`）。这里锁死状态码。
        """
        engine, _ = doc_env
        _seed_two_users(engine)

        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                r = c.delete("/documents/doc-A")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 403, (
            f"越层级联删除返回了 {r.status_code}（应为 403）：{r.text[:200]}"
        )
        b = _row(engine, "m-B")
        assert b is not None and SECRET_B in b.content, f"HTTP 路径把 B 的记忆删了：{b}"

    def test_legacy_edge_without_owner_still_protects_others_row(self, doc_env):
        """老边无归属（票 17 修前造的）时，行层仍要挡住。"""
        from lantai.services.source_service import delete_document

        engine, _ = doc_env
        _seed_two_users(engine, edge_owner=None)

        delete_document("doc-A", principal=_principal("user-A"))

        b = _row(engine, "m-B")
        assert b is not None, "老边无归属时 B 的记忆照样被删了"

    def test_vector_index_not_removed_for_others_row(self, doc_env):
        """向量库除名也要按归属——否则 B 的记忆明明还在却搜不出来。"""
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _seed_two_users(engine)

        delete_document("doc-A", principal=_principal("user-A"))

        assert "m-B" not in vs.deleted, (
            f"B 的记忆还在库里，却被向量库除名了：{vs.deleted}"
        )


# ── Red 3 / 4 / 5 / 6：不能修废 ─────────────────────────────────


class TestDocumentCascadeOwnerBoundary:
    def test_own_cascade_still_deletes(self, doc_env):
        """A 删自己文档、目标全是自己的记忆 → 照常级联删除（不能修废）。"""
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-A", "user-A", SECRET_A)
        _mem(engine, "m-A2", "user-A", "A 的第二条记忆")
        _edge(engine, "e1", "doc-A", "m-A", "user-A")
        _edge(engine, "e2", "doc-A", "m-A2", "user-A")

        out = delete_document("doc-A", principal=_principal("user-A"))

        assert out.get("ok") is True, f"A 自己的级联被修废了：{out}"
        assert out.get("deleted_memories") == 2, f"A 自己的记忆没删干净：{out}"
        assert _row(engine, "m-A") is None and _row(engine, "m-A2") is None
        assert _row(engine, "doc-A") is None
        assert set(vs.deleted) >= {"m-A", "m-A2"}

    def test_null_owner_legacy_row_still_deletable(self, doc_env):
        """Red 5：NULL 属主老行照常可删（单人部署不能修废）。"""
        from lantai.services.source_service import delete_document

        engine, _ = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-legacy", None, "老数据：没有属主")
        _edge(engine, "e1", "doc-A", "m-legacy", "user-A")

        out = delete_document("doc-A", principal=_principal("user-A"))

        assert out.get("ok") is True, f"NULL 属主老行不能删了：{out}"
        assert out.get("deleted_memories") == 1, f"NULL 属主老行没删成：{out}"
        assert _row(engine, "m-legacy") is None

    def test_mixed_targets_abort_before_any_memory_deleted(self, doc_env):
        """Red 4：混合目标（一条自己 + 一条 B 的）→ 整体中止。

        **宁 miss 不脏写**：半途删一半比不删更脏。已删的要说清删了什么。
        """
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-A", "user-A", SECRET_A)
        _mem(engine, "m-B", "user-B", SECRET_B)
        _edge(engine, "e1", "doc-A", "m-A", "user-A")
        _edge(engine, "e2", "doc-A", "m-B", "user-A")

        out = delete_document("doc-A", principal=_principal("user-A"))

        assert out.get("ok") is False, f"混合目标没有整体中止：{out}"
        assert "forbidden" in str(out.get("reason", "")), f"中止原因不是越权：{out}"
        # 自己的那条也不该被删（先校验后删除，而不是删到一半才发现）
        assert _row(engine, "m-A") is not None, "整体中止前先删掉了自己的记忆（半途而废）"
        assert _row(engine, "m-B") is not None
        assert not vs.deleted, f"整体中止了却仍往向量库发删除：{vs.deleted}"
        # 文档本身与它的边也必须完好——「整体中止」意味着什么都没动。
        # 少了这条，一个「先 commit 删除再校验」的实现能混过全部记忆行断言。
        with Session(engine) as s:
            assert s.get(RawDocument, "doc-A") is not None, "整体中止了文档却被删掉"
            remaining = s.exec(select(MemoryEdge)).all()
            assert len(remaining) == 2, f"整体中止了边却被删掉：{len(remaining)} 条剩"

    def test_admin_and_none_still_cascade(self, doc_env):
        """Red 6：admin / principal=None 照常删（worker 不能空转）。"""
        from lantai.services.source_service import delete_document

        for p in (_principal("user-A", role="admin"), None):
            engine, _ = doc_env
            _seed_two_users(engine)
            out = delete_document("doc-A", principal=p)
            assert out.get("ok") is True, f"principal={p!r} 下级联被挡了：{out}"
            assert out.get("deleted_memories") == 2, (
                f"principal={p!r} 下别人的记忆删不掉（worker 会空转）：{out}"
            )


# ── `promoter.delete_memory` 本体 ───────────────────────────────


class TestPromoterDeleteMemoryOwnership:
    def test_delete_memory_rejects_others_row(self, doc_env):
        """service 本体也要挡住——`delete_document` 不是唯一入口。"""
        from lantai.evolution.promoter import delete_memory

        engine, vs = doc_env
        _mem(engine, "m-B", "user-B", SECRET_B)

        out = delete_memory("m-B", principal=_principal("user-A"))

        assert out.get("ok") is False and "forbidden" in str(out.get("reason", "")), (
            f"promoter.delete_memory 没挡住别人的记忆：{out}"
        )
        assert _row(engine, "m-B") is not None, "被挡之后记忆还是被删了"
        assert "m-B" not in vs.deleted

    def test_delete_memory_own_row_still_works(self, doc_env):
        """自己的记忆照常删（不能修废）。"""
        from lantai.evolution.promoter import delete_memory

        engine, vs = doc_env
        _mem(engine, "m-A", "user-A", SECRET_A)

        out = delete_memory("m-A", principal=_principal("user-A"))

        assert out.get("ok") is True, f"自己的记忆删不掉了：{out}"
        assert _row(engine, "m-A") is None
        assert "m-A" in vs.deleted

    def test_delete_missing_row_reports_missing_not_ok(self, doc_env):
        """行不存在必须报 missing——`delete_document` 靠这个字符串决定是否中止。

        若这里返回 `ok: True`，级联会把「记忆已被并发删掉」当成成功，
        计数虚高；更糟的是 `delete_document` 的中止判断会被绕过。
        """
        from lantai.evolution.promoter import delete_memory

        out = delete_memory("m-nonexistent", principal=_principal("user-A"))
        assert out.get("ok") is False, f"删除不存在的行却报成功：{out}"
        assert "missing" in str(out.get("reason", "")), f"原因不是 missing：{out}"

    def test_orphan_edge_target_does_not_abort_cascade(self, doc_env):
        """边指向的记忆行已不存在 → 跳过它，不当中途失败（原行为保持）。"""
        from lantai.services.source_service import delete_document

        engine, _ = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-A", "user-A", SECRET_A)
        _edge(engine, "e-orphan", "doc-A", "m-ghost", "user-A")  # 行不存在
        _edge(engine, "e-real", "doc-A", "m-A", "user-A")

        out = delete_document("doc-A", principal=_principal("user-A"))

        assert out.get("ok") is True, f"孤儿边把级联挡了：{out}"
        assert out.get("deleted_memories") == 1, f"孤儿边影响了计数：{out}"
        assert _row(engine, "m-A") is None

    def test_cross_tenant_target_memory_rejected(self, doc_env):
        """同 user_id 不同租户 → 仍要挡住（租户是独立维度）。

        `ensure_can_delete` 只比 user_id 会让跨租户共享 user_id 的场景漏过去；
        传了 `resource_tenant_id` 才拦得住。
        """
        from lantai.core.auth import Principal
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-A", "user-A", SECRET_A)
        # 同 user_id、不同租户的记忆行
        with Session(engine) as s:
            from lantai.core.time import utcnow

            s.add(
                MemoryItem(
                    id="m-T", user_id="user-A", tenant_id="tenant-X", memory_type="preference",
                    key="k-m-T", title="m-T", content="别租户的记忆", lane="preference",
                    domain="user", status="active", importance=0.5, decay_score=0.9,
                    tier="working", use_count=0, helpful_count=0,
                    created_at=utcnow(), updated_at=utcnow(),
                )
            )
            s.commit()
        _edge(engine, "e1", "doc-A", "m-A", "user-A")
        _edge(engine, "e2", "doc-A", "m-T", "user-A")

        out = delete_document(
            "doc-A", principal=Principal(user_id="user-A", tenant_id="tenant-Y", allowed_lanes=None)
        )

        assert out.get("ok") is False, f"跨租户目标没有整体中止：{out}"
        assert "forbidden" in str(out.get("reason", "")), f"中止原因不是越权：{out}"
        assert _row(engine, "m-T") is not None, "跨租户的记忆被删了"
        assert "m-T" not in vs.deleted

    def test_second_check_catches_row_invisible_at_precheck(self, doc_env):
        """纵深防御的第二道校验真的兜得住（不是装饰）。

        `delete_document` 第 3 步预校验时对 `mem is None` 的目标**跳过**，
        第 5 步仍会调 `delete_memory`。若某行在校验时取不到、删除时却取到了
        （并发插入、或预校验被绕过），**第二道校验是唯一防线**。

        探针实证：把第 3 步的 `s.get` 对 m-B 蒙混成 None 后，
        `delete_memory` 那道校验就是拦住 B 的记忆的东西。
        若 `delete_document` 不给 `delete_memory` 传 principal（D8 变异），
        B 的记忆会被直接删掉。
        """
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _doc(engine, "doc-A", "user-A")
        _mem(engine, "m-A", "user-A", SECRET_A)
        _mem(engine, "m-B", "user-B", SECRET_B)
        _edge(engine, "e1", "doc-A", "m-A", "user-A")
        _edge(engine, "e2", "doc-A", "m-B", "user-A")

        # 预校验阶段（第一次取 m-B）装作行不存在，之后恢复正常视野
        # （真实场景：校验后并发插入 / 预校验被绕过）。
        # 注意：只盲**一次**——盲到底会让第 5 步也取不到行，那就测不到
        # 第二道校验了（它正是靠第 5 步真的取到 B 的行才发挥作用）。
        real_get = Session.get
        state = {"blind_left": 1}

        def _blind_get(self, klass, pk, *a, **kw):
            row = real_get(self, klass, pk, *a, **kw)
            if state["blind_left"] > 0 and klass is MemoryItem and pk == "m-B":
                state["blind_left"] -= 1
                return None
            return row

        with patch.object(Session, "get", _blind_get):
            out = delete_document("doc-A", principal=_principal("user-A"))

        assert state["blind_left"] == 0, "预校验阶段没走到 m-B（测试自身失效）"

        assert out.get("ok") is False, f"第二道校验没兜住：{out}"
        assert "forbidden" in str(out.get("reason", "")), f"中止原因不是越权：{out}"
        b = _row(engine, "m-B")
        assert b is not None and SECRET_B in b.content, (
            f"B 的记忆被第二道校验漏过去了：{b}（principal 没传给 delete_memory？）"
        )
        assert "m-B" not in vs.deleted, f"B 的记忆被向量库除名：{vs.deleted}"


# ── 不 mock 冒烟：真实调用主路径不炸 ─────────────────────────────


class TestDocumentCascadeSmoke:
    def test_cascade_smoke_no_mock(self, doc_env):
        """真实构造最小输入直调 `delete_document`（测试纪律）。"""
        from lantai.services.source_service import delete_document

        engine, vs = doc_env
        _doc(engine, "doc-S", None)
        _mem(engine, "m-S", None, "种子记忆")
        _edge(engine, new_id("edge"), "doc-S", "m-S", None)

        out = delete_document("doc-S")
        assert out.get("ok") is True
        assert out.get("deleted_memories") == 1
        assert _row(engine, "m-S") is None
        assert "m-S" in vs.deleted
        with Session(engine) as s:
            assert len(s.exec(select(MemoryEdge)).all()) == 0
