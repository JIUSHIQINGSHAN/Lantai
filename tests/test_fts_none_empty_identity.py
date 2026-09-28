"""票 `.scratch/mcp-identity-gaps/issues/13`：`search_fts` / `search_fts_bm25`
在 `principal=None` 与空串身份时不加归属过滤。

**先说影响**：两个 FTS 函数在 `principal=None` 与 `Principal(user_id="")` 时
整个归属块被跳过，直接全表召回别人的私有记忆。

实证（`.scratch/mcp-identity-gaps/probe_13_empty_user_id.py`，修前）：

```
viewer_of(空串) = 'default'；viewer_of(None) = 'default'

  空串 user_id  -> ['m-B', 'm-def', 'm-null']  ← 露出 user-B
  None          -> ['m-B', 'm-def', 'm-null']  ← 露出 user-B
  default       -> ['m-def', 'm-null']
```

同库同查询，`default` 只见 2 条，`None` 与空串见 3 条（含 user-B 的私有记忆）。

**两道判据各自漏半边**（`fts.py` 的 `if principal and ...` 与
`if getattr(principal, "user_id", None):`）：

1. `principal=None` → 整个归属块跳过。票 11 只在 `hybrid_search` **入口**
   收敛 None，这两个函数本身没有。当前四处直调都走入口，**主路径安全**；
   但函数是公共 API，换条路径进来就漏（记忆
   `readside-narrowing-needs-or-is-null`：「入口收紧了，函数本身没收紧，
   换条路径进来就漏」）。
2. 空串 → `getattr(principal,"user_id",None)` 为假 → 归属块跳过。
   而 `viewer_of` 对空串**会收敛到 `"default"`**（`acl.py:62`）。
   这两处直接取 `principal.user_id` 没走 `viewer_of`，空串既不被收敛也不被过滤。
   **空串身份真实可达**：`auth.py:157` `make_principal(api_key.user_id, ...)`
   的 `api_key.user_id` 是数据库列，库里一条 ApiKey 的 user_id 为空串即得此形态。

**修法**：在函数内把身份收敛到 `viewer_of`（与仓内多数派一致），
`None` 与空串都得到 `"default"`，走同一条 `user_id == viewer OR IS NULL`。
`SYSTEM_VIEWER` 显式全表保持不动（票 11 的 worker 语义）。

两个函数都**没有 docstring 声明「None = 全表」契约**（不像
`vector_owner_filter` 有明确 docstring 故票 11 选择不动它），且除 `hybrid.py`
四处透传外无其他直调方——所以改函数内语义不破坏任何声明过的契约。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, search_fts, search_fts_bm25, sync_fts

QUERY = "数据库密码是"


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed_all(engine):
    now = utcnow()
    with Session(engine) as s:
        for mid, owner, content in [
            ("m-def", "default", f"{QUERY}六位数字"),
            ("m-null", None, f"{QUERY}八位数字"),
            ("m-B", "user-B", f"{QUERY}十位数字"),
        ]:
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
            sync_fts(s, mid, content)
        s.commit()


def _fts(engine, principal):
    with engine.connect() as conn:
        return sorted(search_fts(conn.connection.driver_connection, QUERY, principal=principal))


def _bm25(engine, principal):
    with engine.connect() as conn:
        return sorted(
            r[0] if isinstance(r, tuple) else r
            for r in search_fts_bm25(conn.connection.driver_connection, QUERY, principal=principal)
        )


# 三种「应被收敛」的身份：空串（auth 可达形态）
# 注意 **`None` 不在其中**：`principal=None` 在 `search_fts` / `search_fts_bm25`
# 的语义是「不过滤」——票 06 的刻意契约，被
# `tests/test_fts_owner_recall.py::test_none_principal_unchanged` 钉住
# （worker / 定时反思直调这两个函数不能空转）。这与 `hybrid_search` **入口**
# 把 None 收敛成 `default`（票 11）是两个不同层次的决定：入口收敛保护 MCP
# 调用方，函数本身保留 worker 契约。**改函数内的 None 语义须先改那条测试，
# 属承重墙**——本轮不擅动，冲突已升级为待维护者决策（见票据 13 的 Comments）。
CONVERGED_FORMS = [
    ("空串 user_id（auth 可达）", Principal(user_id="", role="user", allowed_lanes=None)),
]

# 三种「不应被收窄」的身份
NOT_NARROWED_FORMS = [
    (
        "SYSTEM_VIEWER（worker 显式全表）",
        Principal(user_id="__system__", role="system", allowed_lanes=None),
    ),
    ("admin api_key（票 08 形态）", Principal(user_id="api_key", role="admin", allowed_lanes=None)),
]


@pytest.mark.parametrize("label,principal", CONVERGED_FORMS, ids=[c[0] for c in CONVERGED_FORMS])
class TestConvergedIdentitiesDoNotLeak:
    """None 与空串都必须收敛到 `default` + NULL 老行，不见别人的行。"""

    def test_search_fts_excludes_others(self, engine, label, principal):
        _seed_all(engine)

        got = _fts(engine, principal)

        assert "m-B" not in got, f"{label}：search_fts 露出了 user-B 的私有记忆：{got}"

    def test_search_fts_bm25_excludes_others(self, engine, label, principal):
        _seed_all(engine)

        got = _bm25(engine, principal)

        assert "m-B" not in got, f"{label}：search_fts_bm25 露出了 user-B 的私有记忆：{got}"

    def test_converged_equals_default(self, engine, label, principal):
        """收敛后必须与 `Principal(user_id="default")` 逐条相同（同库同查询）。"""
        _seed_all(engine)

        got = _fts(engine, principal)
        expected = _fts(engine, Principal(user_id="default", role="user", allowed_lanes=None))

        assert got == expected, f"{label} 应收敛到 default 的行为，实际 {got} vs {expected}"


class TestConvergedStillSeesOwn:
    """单人部署不能空转：收敛后仍见 default 自己的 + NULL 老行。"""

    def test_empty_string_still_sees_default_and_null(self, engine):
        _seed_all(engine)

        got = _fts(engine, Principal(user_id="", role="user", allowed_lanes=None))

        assert got == ["m-def", "m-null"], f"空串该恰好收敛到 default + NULL 老行，实际 {got}"


class TestNonePrincipalContractPinned:
    """`principal=None` 在**本函数**仍是「不过滤」——票 06 契约的回归护栏。

    这条不是缺陷，是**刻意决定**的留痕。它与票 11 在 `hybrid_search` 入口的
    None 收敛是两个层次：入口收敛保护 MCP 调用方（宿主不透传 user_id 时
    拿到的是 None），函数本身保留 worker 契约（worker / 定时反思直调它
    不能空转）。

    **冲突已记录待维护者决策**：若将来要把 None 也收敛到 `default`，
    须同时改 `tests/test_fts_owner_recall.py::test_none_principal_unchanged`
    并确认 worker 路径全走 `hybrid_search` 入口。改这条测试 = 动承重墙。
    """

    def test_none_principal_still_unfiltered_in_fts(self, engine):
        _seed_all(engine)

        got = _fts(engine, None)

        assert set(got) == {"m-def", "m-null", "m-B"}, (
            f"principal=None 的行为变了（票 06 契约）：{got}。"
            "若这是有意改动，须同步更新 test_fts_owner_recall.py 的契约测试"
        )


@pytest.mark.parametrize(
    "label,principal", NOT_NARROWED_FORMS, ids=[c[0] for c in NOT_NARROWED_FORMS]
)
class TestNotNarrowedIdentitiesUnchanged:
    """显式系统身份与 admin 的行为不被本轮改动破坏（票 11 / 08 的语义）。"""

    def test_search_fts(self, engine, label, principal):
        _seed_all(engine)

        got = _fts(engine, principal)

        assert "m-B" in got, f"{label}：该全表却滤掉了 user-B：{got}"

    def test_search_fts_bm25(self, engine, label, principal):
        _seed_all(engine)

        got = _bm25(engine, principal)

        assert "m-B" in got, f"{label}：该全表却滤掉了 user-B：{got}"


class TestSystemViewerWithoutAdminRole:
    """`SYSTEM_VIEWER` 的 `_is_system_viewer` 分支必须独立成立（不靠 admin）。

    **为什么需要这条**：`acl.SYSTEM_VIEWER` 的真实形态是
    `Principal(user_id="__system__", role="system")`，而
    `Principal.is_admin` 是 `role in ("admin","system")`——**它天然就是 admin**。
    所以 SYSTEM_VIEWER 在 `_owner_filter_clause` 里被 **admin 分支**拦下返回
    None（不过滤），`_is_system_viewer` 那一行根本没被执行到。

    这正是票 11 踩过的同一个坑（`role="system"` 天然 admin，让测试走错分支、
    变异体因此存活）。若只用 `role="system"` 测 SYSTEM_VIEWER，删掉
    `_is_system_viewer` 分支**测试照样绿**——守卫是死的而你以为它活着。

    故这里用 **`role="user"` + `user_id=SYSTEM_VIEWER`**（不是 admin 的形状）
    单独钉住那个分支。
    """

    def test_system_viewer_user_role_is_not_narrowed(self, engine):
        _seed_all(engine)

        from lantai.core.acl import SYSTEM_VIEWER

        p = Principal(user_id=SYSTEM_VIEWER, role="user", allowed_lanes=None)
        assert not p.is_admin, "这条测试的前提是 principal 不是 admin"

        got = _fts(engine, p)

        assert "m-B" in got, (
            f"SYSTEM_VIEWER（非 admin 形态）该全表却滤掉了 user-B：{got}。"
            "`_is_system_viewer` 分支失效——worker 全量批处理会被误滤成空集"
        )

    def test_system_viewer_bm25_user_role_is_not_narrowed(self, engine):
        _seed_all(engine)

        from lantai.core.acl import SYSTEM_VIEWER

        p = Principal(user_id=SYSTEM_VIEWER, role="user", allowed_lanes=None)

        got = _bm25(engine, p)

        assert "m-B" in got, f"SYSTEM_VIEWER（非 admin 形态）该全表却滤掉了 user-B：{got}"
