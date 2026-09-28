"""票 `.scratch/fts-null-owner/01`：FTS 两个召回通道的归属过滤漏了 `OR IS NULL`。

**先说影响**：读侧每条收窄都靠 `OR IS NULL` 保命（票 03/04/05/06/15 都是它），
唯独关键词召回这条没有。真实库只读实测：

```
memoryitem 共 657 行；NULL 属主 636 行（96.8%）
口径 A `user_id=? OR IS NULL`（收窄统一口径）：657 行
口径 B `user_id=?`（FTS 现行 SQL）          ： 21 行
→ 丢掉 96.8% 的可召回记忆
```

**这不是「收紧过头」，是单人部署下检索功能大面积失效**：新记忆落
`default`（`auth.py:187` DEV MODE 回退值），旧记忆全是 NULL，于是
**越老的记忆越搜不到**。表现是「时灵时不灵」而不是「搜不到」。

**为什么错了一年没人发现**：`git grep -rn -E "search_fts|search_fts_bm25"
-- tests/` 共 20 处调用，**没有一处传 `principal`**。带 `principal` 的
SQL 分支从写下来就没被执行过。

**NULL 是「未记录」不是「属于所有人」**，但必须可见——同票 06 口径。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, search_fts, search_fts_bm25, sync_fts

# 三个属主共用的查询词（trigram 最小 3 字符，中文 3-gram 可命中）
QUERY = "数据库密码"


def _principal(user_id, *, role="user"):
    return Principal(
        tenant_id=None,
        user_id=user_id,
        agent_id=None,
        session_id=None,
        role=role,
        allowed_lanes=None,
    )


@pytest.fixture
def engine():
    """真内存 SQLite + 真 FTS5 trigram 表（不 mock 检索逻辑）。"""
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed(engine, mid: str, owner: str | None) -> None:
    """造一条记忆并同步 FTS（走真实 `sync_fts`，不直接插 FTS 表）。"""
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"key-{mid}",
                content=f"{mid} 的正文：数据库密码是 hunter2",
                namespace="default",
                status="active",
                tier="working",
                importance=0.5,
                confidence=1.0,
                reason="",
                role="OBSERVATION",
                lane="general",
                domain="general",
                version=1,
                use_count=0,
                helpful_count=0,
                decay_score=1.0,
                decay_class="slow",
                event_time_precision="none",
                lifecycle_status="ACTIVE",
                user_id=owner,
                created_at=now,
                updated_at=now,
            )
        )
        sync_fts(s, mid, f"{mid} 的正文：数据库密码是 hunter2")
        s.commit()


def _ids(rows) -> list[str]:
    """`search_fts` 返回 str 列表，`search_fts_bm25` 返回 (id, score) 元组列表。"""
    return [r[0] if isinstance(r, tuple) else r for r in rows]


# ── search_fts（AND 确定性面）────────────────────────────────────


class TestSearchFtsNullOwnerVisible:
    def test_null_owner_row_still_recalled(self, engine):
        """NULL 属主老行必须被召回——真实库 636/657 行是它。"""
        _seed(engine, "mem-legacy", None)
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts(
                    conn.connection.driver_connection,
                    QUERY,
                    top_k=50,
                    principal=_principal("default"),
                )
            )

        assert "mem-legacy" in got, f"NULL 属主老行被 FTS 滤掉了（单人部署检索空转）：{got}"
        assert "mem-B" not in got, f"搜到了别人的行：{got}"

    def test_own_row_recalled(self, engine):
        """自己的行照常召回（收窄不能把正常功能修废）。"""
        _seed(engine, "mem-A", "default")
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts(
                    conn.connection.driver_connection,
                    QUERY,
                    top_k=50,
                    principal=_principal("default"),
                )
            )

        assert "mem-A" in got
        assert "mem-B" not in got

    def test_none_principal_unchanged(self, engine):
        """`principal=None` 仍全表——worker / 定时反思不能空转（票 06 口径）。"""
        _seed(engine, "mem-legacy", None)
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts(conn.connection.driver_connection, QUERY, top_k=50, principal=None)
            )

        assert {"mem-legacy", "mem-B"} <= set(got), f"principal=None 不再全表了：{got}"

    def test_admin_principal_unchanged(self, engine):
        """admin 仍全表（运维排障路径不能瞎）。"""
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts(
                    conn.connection.driver_connection,
                    QUERY,
                    top_k=50,
                    principal=_principal(None, role="admin"),
                )
            )

        assert "mem-B" in got, f"admin 被收窄了：{got}"


# ── search_fts_bm25（OR 召面）────────────────────────────────────


class TestSearchFtsBm25NullOwnerVisible:
    def test_null_owner_row_still_recalled(self, engine):
        """BM25 通道同样漏 `OR IS NULL`（两个函数是同一次手写的同一段）。"""
        _seed(engine, "mem-legacy", None)
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts_bm25(
                    conn.connection.driver_connection,
                    QUERY,
                    top_k=50,
                    principal=_principal("default"),
                )
            )

        assert "mem-legacy" in got, f"BM25 把 NULL 属主老行滤掉了：{got}"
        assert "mem-B" not in got, f"BM25 搜到了别人的行：{got}"

    def test_own_row_recalled(self, engine):
        _seed(engine, "mem-A", "default")

        with engine.connect() as conn:
            got = _ids(
                search_fts_bm25(
                    conn.connection.driver_connection,
                    QUERY,
                    top_k=50,
                    principal=_principal("default"),
                )
            )

        assert "mem-A" in got

    def test_none_principal_unchanged(self, engine):
        _seed(engine, "mem-B", "user-B")

        with engine.connect() as conn:
            got = _ids(
                search_fts_bm25(conn.connection.driver_connection, QUERY, top_k=50, principal=None)
            )

        assert "mem-B" in got, f"principal=None 不再全表：{got}"


# ── 端到端：/search 不该把老记忆漏掉 ─────────────────────────────


class TestHybridSearchKeepsLegacyRows:
    """`hybrid_search` 端到端：老记忆（NULL 属主）必须出现在结果里。

    这是用户真正感知到的面——上面的单元测试过了但融合层再滤一次，
    用户照样搜不到。故此处不过 mock，直走 `_hybrid_search_impl`。
    """

    def test_legacy_memory_reachable_via_hybrid_search(self, engine, monkeypatch):
        import lantai.storage.db as db_module
        from lantai.retrieval import hybrid as hybrid_mod
        from lantai.retrieval.hybrid import _hybrid_search_impl

        original = db_module.get_session
        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

        # 向量通道关掉：本测试要孤立验证关键词通道的归属口径，
        # 否则向量召回会把结果补回来，掩盖 FTS 的漏。
        monkeypatch.setattr(hybrid_mod, "get_vector_store", lambda: None)

        try:
            _seed(engine, "mem-legacy", None)
            _seed(engine, "mem-B", "user-B")

            res = _hybrid_search_impl(
                "数据库密码",
                top_k=50,
                use_rerank=False,
                principal=_principal("default"),
            )
            ids = {r["memory"]["id"] for r in res}

            assert "mem-legacy" in ids, f"端到端也没召回 NULL 属主老记忆：{ids}"
            assert "mem-B" not in ids, f"端到端搜到了别人的记忆：{ids}"
        finally:
            monkeypatch.setattr(db_module, "get_session", original)
