"""VAULT 档案页看不到历史记忆：`build_memories_page` 缺 `OR IS NULL`。

票 `.scratch/mcp-identity-gaps/issues/03-vault-page-missing-or-is-null.md`

同文件另外 4 处收窄（memory_service.py :503/:532/:639/:644）口径全是
`user_id == viewer OR user_id IS NULL`，**只有 `build_memories_page`
（:737）是裸 `== principal.user_id`**。真实库 650 行 memoryitem 里 629 行
`user_id` 为 NULL（v022 之前写入没有属主概念），所以对 DEV MODE
（viewer="default"）与任何显式属主的用户，档案页只剩自己那几条。

**测试策略**：真 `build_memories_page` + 内存 SQLite 真建表，不 mock
内部逻辑——要验证的正是那个 `where` 条件的形状。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem


@pytest.fixture()
def sf():
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", lambda: Session(engine))
        yield Session(engine)


def _seed(s, mid: str, content: str, user_id: str | None) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"k-{mid}",
            content=content,
            lane="fact",
            status="active",
            user_id=user_id,
            importance=0.5,
            decay_score=1.0,
            decay_class="episodic",
            use_count=0,
            created_at=now,
            updated_at=now,
        )
    )


def _count(sf, principal, limit: int = 100) -> int:
    from lantai.services.memory_service import build_memories_page

    return build_memories_page(sf, limit=limit, principal=principal).get("total") or 0


class TestNullOwnerRowsVisible:
    """NULL 属主老行必须对收敛后的 viewer 可见。"""

    def test_dev_mode_sees_null_owner_rows(self, sf):
        """决定性：629 行 NULL 老行 + 3 行 default → DEV MODE 该看到 632。

        修前裸 `== "default"` 只命中那 3 行——单用户部署的档案页基本空白。
        """
        with sf as s:
            for i in range(629):
                _seed(s, f"m-null-{i}", f"NULL 属主老行 {i}", None)
            for i in range(3):
                _seed(s, f"m-def-{i}", f"default 行 {i}", "default")
            _seed(s, "m-b", "B 的私有记忆", "user-B")
            s.commit()

        total = _count(sf, Principal(user_id="default"))
        assert total == 632, (
            f"DEV MODE 看不到 NULL 属主老行（该 632 = 629 NULL + 3 default，"
            f"实际 {total}）——单用户部署的档案页基本空白"
        )

    def test_explicit_owner_sees_null_rows_but_not_others(self, sf):
        """A 该看到 629 NULL + 自己的，**看不到 B 的**。

        这条防的是"把 `OR IS NULL` 写成放开全部"的变异——
        `OR IS NULL` 的语义是"老行人人可读"，不是"不隔离"。
        """
        with sf as s:
            for i in range(629):
                _seed(s, f"m-null-{i}", f"NULL 属主老行 {i}", None)
            _seed(s, "m-a", "A 的记忆", "user-A")
            _seed(s, "m-b", "B 的私有记忆", "user-B")
            s.commit()

        total = _count(sf, Principal(user_id="user-A"))
        assert total == 630, f"A 该看到 629 NULL + 自己 1 条 = 630，实际 {total}"

        from lantai.services.memory_service import build_memories_page

        rows = build_memories_page(sf, limit=100, principal=Principal(user_id="user-A"))
        ids = {m["id"] for m in rows["memories"]}
        assert "m-b" not in ids, f"A 读到了 B 的记忆：{sorted(ids)[:5]}"

    def test_none_principal_unchanged(self, sf):
        """回归护栏：`principal=None`（内部 worker / 宿主不透传）行为不变。

        02 号票明确"读操作不收紧"——一次性收紧会把现有 MCP 客户端全打断。
        """
        with sf as s:
            for i in range(10):
                _seed(s, f"m-null-{i}", f"NULL 属主老行 {i}", None)
            _seed(s, "m-a", "A 的记忆", "user-A")
            _seed(s, "m-b", "B 的私有记忆", "user-B")
            s.commit()

        assert _count(sf, None) == 12, "principal=None 的行为变了（该仍是全表）"

    def test_admin_unchanged(self, sf):
        """回归护栏：真形态 admin（`user_id=None`，见 auth.py:168）仍看全部。

        我一度判"admin 看到 0 条"是 bug，实测推翻：真 admin 的 user_id
        是 None，`if getattr(principal,'user_id',None)` 为假 → 不加条件。
        **这条用例锁住那个形状，免得下一个人照错误结论去改。**
        """
        with sf as s:
            for i in range(10):
                _seed(s, f"m-null-{i}", f"NULL 属主老行 {i}", None)
            _seed(s, "m-a", "A 的记忆", "user-A")
            s.commit()

        assert _count(sf, Principal(role="admin")) == 11, "admin 看不全了（该 11 = 10 NULL + 1 A）"
