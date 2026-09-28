"""票 `.scratch/mcp-identity-gaps/issues/08`：admin 两种形态行为不一致。

**先说影响**：同一个管理员，`Principal(user_id="api_key", role="admin")`
（`auth.py:168` HTTP 环境变量 API key 的**真形态**）与
`Principal(user_id=None, role="admin")`（测试/CLI 常写法）在
`build_memories_page` 上看到的数据**差一整套**。

实测（`.scratch/mcp-identity-gaps/probe_08_admin_two_forms.py`，修前）：

```
A: user_id='api_key', role=admin  -> total= 1  ids=['m-null']   ← 只有 NULL 老行
B: user_id=None, role=admin       -> total= 3  ids=[default, null, user-B]
C: explicit user-A                -> total= 1  ids=['m-null']
```

形态 A 的真实库表现比票面原判更严重：`viewer_of` 对 `"api_key"` 原样返回
（不等于 `"default"`），于是管理员连自己 `default` 属主的记忆都看不到——
**VAULT 档案页基本空白**，排查"这条记忆去哪了"会得到错误结论。
方向是"该看的没看到"（运维误导），不是越权泄漏。

**修法**（与同文件四处既有形状一致：`_kaogong_scope` :73、
`get_core_memory` :498、`put_core_memory` :523、`find_duplicate_verbatim` :640
全部显式 `is_admin` → 不加 scope）：`build_memories_page` 的 `if principal:` 块内，
加**归属条件前**先判 `is_admin`，admin 一律不加 user 归属过滤。

**边界**：只统一 user_id 归属这一处（A/B 差异的唯一来源）。
`session_id` / `agent_id` / `allowed_lanes` 是调用方显式传的收窄条件，
不属身份差异——顺带放开会把「admin 全表」扩大成「admin 无条件」，那是另一个决定。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.memory_service import build_memories_page
from lantai.storage.fts import init_fts, sync_fts

# 票 07 的教训（`mcp-identity-gaps/11`）：`role="system"` 天然就是 admin
# （`Principal.is_admin` = `role in ("admin","system")`），拿它测 admin 分支
# 会走到 admin 分支而**绕开要钉的那条**。这里统一用 `role="user"` + `user_id`
# 构造 admin 之外的形态，admin 本身用 `role="admin"`。


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


def _seed(engine, mid, owner, content):
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=content,
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
        sync_fts(s, mid, content)
        s.commit()


def _ids(engine, principal):
    with Session(engine) as s:
        page = build_memories_page(s, status="active", limit=50, principal=principal)
    return {m["id"] for m in page["memories"]}


def _seed_all(engine):
    _seed(engine, "m-legacy", None, "老记忆：没有属主的历史行")
    _seed(engine, "m-B", "user-B", "B 的私有记忆：收购对家的报价底牌")
    _seed(engine, "m-def", "default", "default 属主的记忆")
    _seed(engine, "m-A", "user-A", "A 的记忆")


class TestAdminTwoFormsAgree:
    """两种 admin 构造必须看到同一套数据（本票的核心断言）。"""

    def test_api_key_admin_and_none_admin_agree(self, engine):
        """形态 A（`user_id='api_key'`，auth.py 真形态）与形态 B（None）一致。

        Red 实证（修前）：A total=1、B total=3。
        """
        _seed_all(engine)

        got_a = _ids(engine, _admin("api_key"))
        got_b = _ids(engine, _admin(None))

        assert got_a == got_b, (
            f"admin 两种形态不一致：user_id='api_key' 见 {sorted(got_a)}，"
            f"user_id=None 见 {sorted(got_b)}"
        )

    def test_admin_sees_all_rows(self, engine):
        """admin 一律全表（与同文件四处既有形状一致）：四种属主全见。"""
        _seed_all(engine)

        got = _ids(engine, _admin("api_key"))

        assert got == {"m-legacy", "m-B", "m-def", "m-A"}, (
            f"admin 该全表却只见 {sorted(got)}——VAULT 档案页给管理员错误的库容印象"
        )

    def test_admin_with_none_user_id_sees_all_rows(self, engine):
        """形态 B 的全表行为不被本轮改动破坏（回归护栏）。"""
        _seed_all(engine)

        got = _ids(engine, _admin(None))

        assert got == {"m-legacy", "m-B", "m-def", "m-A"}, (
            f"admin(user_id=None) 的全表行为被破坏了：{sorted(got)}"
        )


class TestExplicitUserUnchanged:
    """显式 user 的收窄逐字不变（票 03 的 `OR IS NULL` 半边必须留着）。"""

    def test_explicit_user_sees_own_and_null_but_not_others(self, engine):
        _seed_all(engine)

        got = _ids(engine, _user("user-A"))

        assert got == {"m-legacy", "m-A"}, (
            f"显式 user-A 该见自己的 + NULL 老行，实际见 {sorted(got)}"
        )

    def test_explicit_user_b_does_not_see_a(self, engine):
        _seed_all(engine)

        got = _ids(engine, _user("user-B"))

        assert "m-A" not in got, f"user-B 见到了 user-A 的行：{sorted(got)}"
        assert got == {"m-legacy", "m-B"}


class TestNonePrincipalUnchanged:
    """票 07 的收敛口径不被本轮踩坏：None → `default` + NULL 老行。"""

    def test_none_principal_still_converges_to_default(self, engine):
        _seed_all(engine)

        got = _ids(engine, None)

        assert got == {"m-legacy", "m-def"}, (
            f"principal=None 该收敛到 default + NULL 老行，实际 {sorted(got)}"
        )
