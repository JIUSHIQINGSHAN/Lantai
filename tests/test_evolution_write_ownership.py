"""演化类写侧归属：考功 / 沉潜 / 遗忘不再批量改写别人的记忆。

票 `.scratch/readside-gaps/12-kaogong-writes-cross-user.md`

**先说影响**：三个"全库演化"入口一个身份都不取，却能真改别人的记忆：
- 考功改写 `tier` / `decay_class` / `importance`（第七轮实证 0.9 → 0.1）
- 沉潜把 `status` 改成 `archived`
- 遗忘改 `decay_score` 并可能归档

读侧缺口只是"看到"，写侧缺口是**真改**，而且多数不可逆
（importance 降了没有回滚，archived 没有 undo 入口）。
这正是「宁 miss 不脏写」要防的：宁可漏一次考功，不能错改别人的权重。

**三处同构，一并修**：分开修会留下"以为修完了"的错觉。
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
from lantai.models.tables import MemoryItem, MemoryProposal

# use_count>=3 且 helpful_ratio<=0.2 → 必然触发考功下考降权
DEMOTING = dict(use_count=10, helpful_count=1)
# use_count>=3 且 helpful_ratio>=0.8 → 必然触发考功上考晋升
PROMOTING = dict(use_count=10, helpful_count=9)


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def ew_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # 沉潜折叠路径会 sync_fts 写 FTS 表（同事务强一致，ADR-0008）——
    # create_all 不建 FTS5 虚表，少了它整笔折叠回滚，与 conftest.param_env 同处理。
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.llm.client.chat_json", return_value={}),
    ):
        yield session_factory


def _add(sf, mem_id: str, user_id: str | None, **kw) -> None:
    fields = dict(
        title=mem_id,
        content=f"{mem_id} 的正文",
        lane="fact",
        status="active",
        importance=0.9,
        decay_score=0.9,
        tier="working",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    fields.update(kw)
    with sf() as s:
        s.add(MemoryItem(id=mem_id, user_id=user_id, **fields))
        s.commit()


def _row(sf, mem_id: str):
    with sf() as s:
        return s.get(MemoryItem, mem_id)


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── 考功 ───────────────────────────────────────────────────────


class TestKaogongWriteOwnership:
    def test_other_users_memory_not_demoted(self, ew_env):
        """**Red 1（决定性）**：A 跑考功，B 的可降权记忆 importance 不变。

        断言落在**落库行的字段值**上，不是报告计数——计数为 0
        可能有一堆别的原因（没数据、样本不足、状态不对）。
        """
        sf = ew_env
        _add(sf, "mem-B", "user-B", **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            report = run_kaogong_cycle(principal=_principal("user-A"))

        row = _row(sf, "mem-B")
        assert row is not None, "B 的记忆没了"
        assert row.importance == 0.9, (
            f"A 跑考功改写了 B 的记忆权重：0.9 → {row.importance}（写侧越权）"
        )
        assert "mem-B" not in [d["id"] for d in report["details"]], "报告里出现了 B 的记忆"

    def test_own_memory_still_demoted(self, ew_env):
        """Red 4：A 自己名下可降权的记忆照常降权（收窄不是把功能修废）。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            run_kaogong_cycle(principal=_principal("user-A"))

        row = _row(sf, "mem-A")
        assert row is not None
        assert row.importance == 0.1, f"A 自己的记忆没被正常降权：importance={row.importance}"

    def test_null_owner_memory_still_evaluated(self, ew_env):
        """Red 5：NULL 属主老记忆仍被处理（单人部署下不能空转）。"""
        sf = ew_env
        _add(sf, "mem-null", None, **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            run_kaogong_cycle(principal=_principal("user-A"))

        row = _row(sf, "mem-null")
        assert row is not None
        assert row.importance == 0.1, f"NULL 属主老记忆不被处理了：importance={row.importance}"

    def test_null_owner_and_other_user_treated_differently(self, ew_env):
        """**NULL 口径专项**：NULL 属主可见、B 属主不可见——两者必须区别对待。

        变异 M3/M7 专治：把 `== viewer OR IS NULL` 收成 `== viewer`，
         NULL 行被判不可见，上面那条 Red 5 会挂；但若只测 NULL 一条，
         `user_id IS NULL` 被误写成恒真/恒假也可能靠巧合过。这里同时种
        NULL 与 B 两行，两边的落库字段值都必须各按各的口径走。
        """
        sf = ew_env
        _add(sf, "mem-null", None, **DEMOTING)
        _add(sf, "mem-B", "user-B", **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            report = run_kaogong_cycle(principal=_principal("user-A"))

        assert _row(sf, "mem-null").importance == 0.1, "NULL 属主老记忆不被处理了"
        assert _row(sf, "mem-B").importance == 0.9, "B 的记忆被 A 的考功改写了"
        ids = [d["id"] for d in report["details"]]
        assert "mem-null" in ids and "mem-B" not in ids, f"报告归属不对：{ids}"

    def test_admin_evaluates_all(self, ew_env):
        """Red 6：admin 全权（与改动前逐字一致）。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", **DEMOTING)
        _add(sf, "mem-B", "user-B", **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            report = run_kaogong_cycle(principal=_principal(None, role="admin"))

        assert report["evaluated"] == 2, f"admin 没评估全库：{report}"
        assert _row(sf, "mem-A").importance == 0.1
        assert _row(sf, "mem-B").importance == 0.1

    def test_internal_call_unfiltered(self, ew_env):
        """Red 7：principal=None（worker/CLI/scheduler）行为不变，仍全表。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", **DEMOTING)
        _add(sf, "mem-B", "user-B", **DEMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            report = run_kaogong_cycle()

        assert report["evaluated"] == 2, f"内部调用被收窄了：{report}"
        assert _row(sf, "mem-B").importance == 0.1

    def test_own_memory_still_promoted(self, ew_env):
        """晋升路径同样过归属（不只测降权那条分支）。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", **PROMOTING)
        _add(sf, "mem-B", "user-B", **PROMOTING)

        from lantai.services.kaogong_service import run_kaogong_cycle

        with patch.object(db_module, "get_session", sf):
            run_kaogong_cycle(principal=_principal("user-A"))

        assert _row(sf, "mem-A").tier == "longterm", "A 自己的记忆没晋升"
        assert _row(sf, "mem-B").tier == "working", (
            f"A 跑考功把 B 的记忆晋升了：tier={_row(sf, 'mem-B').tier}"
        )


# ── 遗忘 ───────────────────────────────────────────────────────


class TestForgettingWriteOwnership:
    def test_other_users_memory_not_archived(self, ew_env):
        """**Red 2（决定性）**：A 跑遗忘，B 的低 decay 记忆不被归档。"""
        sf = ew_env
        # decay_score 低于归档阈值 → 本会被自动归档
        _add(sf, "mem-B", "user-B", decay_score=0.01, last_used_at=utcnow())

        from lantai.memory.forgetting import apply_forgetting

        before = _row(sf, "mem-B")
        with patch.object(db_module, "get_session", sf):
            apply_forgetting(principal=_principal("user-A"))
        after = _row(sf, "mem-B")

        assert after is not None, "B 的记忆没了"
        assert after.status == before.status, (
            f"A 跑遗忘改了 B 的状态：{before.status} → {after.status}"
        )
        assert after.decay_score == before.decay_score, (
            f"A 跑遗忘改了 B 的 decay_score：{before.decay_score} → {after.decay_score}"
        )

    def test_own_memory_still_processed(self, ew_env):
        """A 自己的低 decay 记忆照常被遗忘（收窄不是把功能修废）。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", decay_score=0.01, last_used_at=utcnow())

        from lantai.memory.forgetting import apply_forgetting

        with patch.object(db_module, "get_session", sf):
            apply_forgetting(principal=_principal("user-A"))

        row = _row(sf, "mem-A")
        assert row is not None, "A 自己的记忆没了"
        # decay 被重算（不再是种下去的 0.01），证明这条路径真的跑了
        assert row.decay_score != 0.01, f"A 自己的记忆没被处理：decay_score={row.decay_score}"

    def test_internal_call_unfiltered(self, ew_env):
        """principal=None 内部调用行为不变（定时任务仍全表）。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", decay_score=0.01, last_used_at=utcnow())
        _add(sf, "mem-B", "user-B", decay_score=0.01, last_used_at=utcnow())

        from lantai.memory.forgetting import apply_forgetting

        with patch.object(db_module, "get_session", sf):
            apply_forgetting()

        assert _row(sf, "mem-A").decay_score != 0.01, "内部调用下 A 没被处理"
        assert _row(sf, "mem-B").decay_score != 0.01, "内部调用下 B 没被处理（被收窄了？）"

    def test_null_owner_and_other_user_treated_differently(self, ew_env):
        """**NULL 口径专项（遗忘侧）**：NULL 属主照常衰减，B 属主一动不动。"""
        sf = ew_env
        _add(sf, "mem-null", None, decay_score=0.01, last_used_at=utcnow())
        _add(sf, "mem-B", "user-B", decay_score=0.01, last_used_at=utcnow())

        from lantai.memory.forgetting import apply_forgetting

        with patch.object(db_module, "get_session", sf):
            apply_forgetting(principal=_principal("user-A"))

        assert _row(sf, "mem-null").decay_score != 0.01, "NULL 属主记忆不被处理了"
        assert _row(sf, "mem-B").decay_score == 0.01, "A 跑遗忘改了 B 的 decay_score"
        assert _row(sf, "mem-B").status == "active", "A 跑遗忘归档了 B 的记忆"


# ── 沉潜 ───────────────────────────────────────────────────────


class TestConsolidationWriteOwnership:
    def test_other_users_memory_not_pruned(self, ew_env):
        """**Red 3（决定性）**：A 跑 enforce 沉潜，B 的可裁剪记忆不归档。"""
        sf = ew_env
        from lantai.core.settings import settings

        # helpful_count=0 + decay_score 低于阈值 → 本会被 _prune 归档
        _add(sf, "mem-B", "user-B", decay_score=0.01, helpful_count=0, last_used_at=utcnow())
        _add(sf, "mem-A", "user-A", decay_score=0.01, helpful_count=0, last_used_at=utcnow())

        from lantai.services.consolidation_service import run_consolidation_cycle

        with (
            patch.object(db_module, "get_session", sf),
            patch.object(settings, "CONSOLIDATION_AUDIT_MODE", "enforce"),
        ):
            run_consolidation_cycle(principal=_principal("user-A"))

        b = _row(sf, "mem-B")
        assert b is not None, "B 的记忆没了"
        assert b.status == "active", f"A 跑沉潜把 B 的记忆归档了：status={b.status}"
        a = _row(sf, "mem-A")
        assert a is not None, "A 自己的记忆没了"

    def test_internal_call_unfiltered(self, ew_env):
        """principal=None 内部调用行为不变。"""
        sf = ew_env
        _add(sf, "mem-A", "user-A", decay_score=0.01, helpful_count=0, last_used_at=utcnow())
        _add(sf, "mem-B", "user-B", decay_score=0.01, helpful_count=0, last_used_at=utcnow())

        from lantai.core.settings import settings
        from lantai.services.consolidation_service import run_consolidation_cycle

        with (
            patch.object(db_module, "get_session", sf),
            patch.object(settings, "CONSOLIDATION_AUDIT_MODE", "enforce"),
        ):
            run_consolidation_cycle()

        assert _row(sf, "mem-A").status == "archived", "内部调用下 A 没被裁剪"
        assert _row(sf, "mem-B").status == "archived", "内部调用下 B 没被裁剪（被收窄了？）"

    def test_other_users_fragments_not_folded(self, ew_env):
        """**聚类侧越权（Red 3 的第二半）**：A 跑 off 沉潜，B 的碎片不被折叠。

        上面那条只覆盖裁剪（prune）路径；聚类折叠是**另一条候选集**
        （`find_consolidation_clusters` 的 `select(...status=="active")`），
        会把别人的碎片改成 `consolidated` 并生成一条带别人正文的新主记忆。
        变异 M8/M10 专治此处：不收窄聚类集，或周期不把 principal 传给聚类。
        断言同样落在**落库行的字段值**上，不只看报告计数。
        """
        sf = ew_env
        from lantai.services.consolidation_service import run_consolidation_cycle

        # 3 条同主题碎片（真实 jieba 聚类路径，与 test_consolidation.py 同构）
        for i, text in enumerate(
            [
                "大哥今天早上泡了明前大佛龙井茶",
                "大哥喜欢喝大佛龙井茶，水温要求85度",
                "大哥日常饮品偏好为浙江新昌大佛龙井茶",
            ],
            start=1,
        ):
            _add(sf, f"frag-B-{i}", "user-B", content=text, lane="preference", domain="user")

        with patch.object(db_module, "get_session", sf):
            with (
                patch(
                    "lantai.services.consolidation_service.chat_json",
                    return_value={
                        "consolidated_content": "大哥偏好浙江新昌大佛龙井茶",
                        "importance": 0.9,
                        "confidence": 0.95,
                    },
                ),
                patch("lantai.services.consolidation_service.index_memory_item"),
            ):
                report = run_consolidation_cycle(principal=_principal("user-A"))

        for i in (1, 2, 3):
            row = _row(sf, f"frag-B-{i}")
            assert row is not None
            assert row.status == "active", (
                f"A 跑沉潜把 B 的碎片折叠了：frag-B-{i} status={row.status}"
            )
        with sf() as s:
            masters = s.exec(select(MemoryItem).where(MemoryItem.user_id == "user-B")).all()
        assert len(masters) == 3, f"沉潜给 B 建了新记忆：{[m.id for m in masters]}"
        assert report["consolidated_groups"] == 0, f"报告里出现了 B 的碎片簇：{report}"

    def test_route_passes_principal_to_clustering(self, ew_env):
        """**M12 专治**：REST `/evolution/consolidate` 不带身份时，聚类集是全表。

        必须替掉 `chat_json`：不替的话真实 LLM 调用失败 → `consolidate_cluster`
        返回 None → 什么都不折叠，身份传没传下去都一片绿（测试是空的）。
        这是票 12 变异第一轮 MISSED 的直接原因。
        """
        sf = ew_env
        for i, text in enumerate(
            [
                "大哥今天早上泡了明前大佛龙井茶",
                "大哥喜欢喝大佛龙井茶，水温要求85度",
                "大哥日常饮品偏好为浙江新昌大佛龙井茶",
            ],
            start=1,
        ):
            _add(sf, f"frag-B-{i}", "user-B", content=text, lane="preference", domain="user")

        from lantai.api.app import app

        with (
            patch(
                "lantai.services.consolidation_service.chat_json",
                return_value={
                    "consolidated_content": "大哥偏好浙江新昌大佛龙井茶",
                    "importance": 0.9,
                    "confidence": 0.95,
                },
            ),
            patch("lantai.services.consolidation_service.index_memory_item"),
        ):
            with TestClient(app) as c:
                resp = _as(_principal("user-A"), lambda: c.post("/evolution/consolidate"))

        assert resp.status_code == 200, resp.text
        for i in (1, 2, 3):
            row = _row(sf, f"frag-B-{i}")
            assert row is not None and row.status == "active", (
                f"经 REST 跑沉潜折叠了 B 的碎片：frag-B-{i} status={row.status}"
            )

    def test_admin_folds_everything(self, ew_env):
        """**M13 专治**：admin 全权，聚类集是全表（与改动前逐字一致）。"""
        sf = ew_env
        for i, text in enumerate(
            [
                "大哥今天早上泡了明前大佛龙井茶",
                "大哥喜欢喝大佛龙井茶，水温要求85度",
                "大哥日常饮品偏好为浙江新昌大佛龙井茶",
            ],
            start=1,
        ):
            _add(sf, f"frag-B-{i}", "user-B", content=text, lane="preference", domain="user")

        from lantai.services.consolidation_service import run_consolidation_cycle

        with patch.object(db_module, "get_session", sf):
            with (
                patch(
                    "lantai.services.consolidation_service.chat_json",
                    return_value={
                        "consolidated_content": "大哥偏好浙江新昌大佛龙井茶",
                        "importance": 0.9,
                        "confidence": 0.95,
                    },
                ),
                patch("lantai.services.consolidation_service.index_memory_item"),
            ):
                report = run_consolidation_cycle(principal=_principal(None, role="admin"))

        assert report["consolidated_groups"] == 1, f"admin 没聚出 B 的碎片簇：{report}"
        for i in (1, 2, 3):
            assert _row(sf, f"frag-B-{i}").status == "consolidated", "admin 下碎片没折叠"

    def test_null_owner_fragments_still_folded(self, ew_env):
        """NULL 属主碎片仍被折叠（单人部署下沉潜不能空转；与 M13 互补）。"""
        sf = ew_env
        for i, text in enumerate(
            [
                "大哥今天早上泡了明前大佛龙井茶",
                "大哥喜欢喝大佛龙井茶，水温要求85度",
                "大哥日常饮品偏好为浙江新昌大佛龙井茶",
            ],
            start=1,
        ):
            _add(sf, f"frag-null-{i}", None, content=text, lane="preference", domain="user")

        from lantai.services.consolidation_service import run_consolidation_cycle

        with patch.object(db_module, "get_session", sf):
            with (
                patch(
                    "lantai.services.consolidation_service.chat_json",
                    return_value={
                        "consolidated_content": "大哥偏好浙江新昌大佛龙井茶",
                        "importance": 0.9,
                        "confidence": 0.95,
                    },
                ),
                patch("lantai.services.consolidation_service.index_memory_item"),
            ):
                report = run_consolidation_cycle(principal=_principal("user-A"))

        assert report["consolidated_groups"] == 1, f"NULL 属主碎片没被聚类：{report}"
        for i in (1, 2, 3):
            assert _row(sf, f"frag-null-{i}").status == "consolidated", "NULL 碎片没折叠"


# ── 路由层 ─────────────────────────────────────────────────────


class TestEvolutionRoutesTakeIdentity:
    def test_kaogong_route_passes_principal(self, ew_env):
        """`POST /evolution/kaogong` 带身份并下传（变异专治：路由不取身份）。"""
        sf = ew_env
        _add(sf, "mem-B", "user-B", **DEMOTING)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/evolution/kaogong"),
            )

        assert resp.status_code == 200, resp.text
        row = _row(sf, "mem-B")
        assert row is not None and row.importance == 0.9, (
            f"经 REST 跑考功改写了 B 的记忆：importance={row.importance if row else None}"
        )
