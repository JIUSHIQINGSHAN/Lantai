"""票 `.scratch/mcp-identity-gaps/02`：`mem_recent` 在无身份时全表返回。

**先说影响**：`memory_service.py:733`（`build_memories_page`）的整个
归属块挂在 `if principal:` 下。宿主不透传 `user_id` 时 `principal=None`
→ **五条归属条件一个都不加** → 全表返回，含 B 的记忆正文。

同文件 :737 那段精心写的 `OR IS NULL` + `viewer_of` 收敛，在 `None` 时
**整段不可达**。

**这不是新引入的**：01a 票修的是"带 user_id 时收窄"（决定性成果：
A 调 `mem_recent` 从返回 B 的记忆全文变成 0 条），**没修"不带 user_id
时全表"**。本票补上后一半。

实测（`.scratch/mcp-identity-gaps/probe_02c_three_way.py`）：

```
mem_recent principal=None  → 4 条（A, B, default, NULL 老行）
mem_recent user_id=user-A  → 2 条（A + NULL 老行）    ← 收窄正确
mem_recent user_id=user-B  → 2 条（B + NULL 老行）
```

**修法**：把 `viewer_of(principal)` 收敛提到 `if principal:` **外面**，
让 user 条件在 None 时也生效（收敛到 `"default"`）。tenant / session /
agent / allowed_lanes 四条保持挂在 `if principal:` 下——它们没有
"无身份回落值"可言（`viewer_of` 只收敛 user_id），None 时加上反而滤空。

**为什么这个形状对**：`cognitive_context` / `candidates_pending` /
`build_overview` 三条实测都是这个行为（None 收敛到 default），
改完之后四处一致，不是新造特例。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.memory_service import build_memories_page
from lantai.storage.fts import init_fts, sync_fts


def _principal(uid=None, *, role="user"):
    return Principal(
        tenant_id=None,
        user_id=uid,
        agent_id=None,
        session_id=None,
        role=role,
        allowed_lanes=None,
    )


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


class TestMemoriesPageNonePrincipal:
    """`principal=None` 不再等于"全表"。

    **Red 实证（2026-09-28，修前）**：`_ids(None)` 返回全部 4 条，
    含 `m-B`（B 的私有记忆）。修后应收敛到 `default` + NULL 老行。
    """

    def test_none_principal_does_not_return_others_rows(self, engine):
        """不带身份 → 不见 B 的行（单人部署看到自己的 + 老行）。"""
        _seed(engine, "m-legacy", None, "老记忆")
        _seed(engine, "m-B", "user-B", "B 的私有记忆：收购对家的报价底牌")
        _seed(engine, "m-A", "user-A", "A 的记忆")
        _seed(engine, "m-def", "default", "default 的记忆")

        got = _ids(engine, None)

        assert "m-B" not in got, f"principal=None 全表返回了，B 的私有记忆泄漏：{sorted(got)}"
        assert "m-A" not in got, f"principal=None 见到了 user-A 的行：{sorted(got)}"

    def test_none_principal_still_sees_default_and_legacy(self, engine):
        """单人部署不能空转：None 时仍见 `default` 自己的行 + NULL 老行。"""
        _seed(engine, "m-legacy", None, "老记忆")
        _seed(engine, "m-B", "user-B", "B 的私有记忆")
        _seed(engine, "m-def", "default", "default 的记忆")

        got = _ids(engine, None)

        assert "m-def" in got, f"default 自己的行不见了（单人部署功能空转）：{sorted(got)}"
        assert "m-legacy" in got, f"NULL 属主老行不见了（96.8% 的数据）：{sorted(got)}"

    def test_explicit_user_unchanged(self, engine):
        """回归护栏：显式 user-A 的行为逐字不变（自己 + NULL 老行）。"""
        _seed(engine, "m-legacy", None, "老记忆")
        _seed(engine, "m-B", "user-B", "B 的私有记忆")
        _seed(engine, "m-A", "user-A", "A 的记忆")

        got = _ids(engine, _principal("user-A"))

        assert got == {"m-A", "m-legacy"}, f"显式用户的收窄结果变了：{sorted(got)}"

    def test_admin_principal_still_unfiltered(self, engine):
        """admin 仍全表——运维排障路径不能瞎（同 01a 口径）。"""
        _seed(engine, "m-legacy", None, "老记忆")
        _seed(engine, "m-B", "user-B", "B 的私有记忆")
        _seed(engine, "m-A", "user-A", "A 的记忆")

        got = _ids(engine, _principal(None, role="admin"))

        assert got == {"m-legacy", "m-B", "m-A"}, f"admin 被收窄了：{sorted(got)}"
