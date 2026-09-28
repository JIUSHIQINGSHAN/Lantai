"""票 `.scratch/mcp-identity-gaps/10`：`offload_read` 无身份时读别人的卸载全文。

**先说影响**：`read_offload_file` 最后一行是 `path.read_text()`——
**别人的记忆全文，不限长度，不走 recall budget**。而归属校验整段挂在
`if principal is not None:` 下，宿主不透传 `user_id` 时 `principal=None`，
**校验一次都不跑**。

决定性实证（`.scratch/mcp-identity-gaps/probe_offload_none_semantics.py`）：

```
S0 ensure_can_delete(None, resource_user_id='user-B') → 没抛错   ❌ 空操作
S1 read_offload_file('m-B', principal=None)           → B 的全文  ❌ 洞
S2 read_offload_file('m-legacy', None) （NULL 老行）   → 仍可读    ✅ 单人部署
S3 read_offload_file('m-B', user-A)                   → 403       ✅ 护栏
S4 read_offload_file('m-A', user-A)                   → 自己的    ✅ 护栏
S5 read_offload_file('m-B', admin)                    → B 的全文  ✅ 护栏
S6 read_offload_file('m-def', None)                   → 自己的    ✅ 单人部署
```

**根因最该记住的一条**：`ensure_can_delete(None, ...)` 是**彻底的空操作**——
它的每个守卫都要求 principal 的某个字段非空（`getattr(principal,"is_admin",False)`
→ False 不放行；`p_user = getattr(principal,"user_id",None)` → None，
于是 `resource_user_id and p_user` 恒假）。**"调用了 ensure_can_delete"
不等于"校验过了"**——所以 `test_ensure_can_delete_none_is_noop` 单独一条
把这个反直觉的事实钉住。

**与票 07/09 的形状差异（方法论）**：07/09 下游是 `viewer_of` 收敛 +
`OR IS NULL` 的读侧口径（NULL 老行可见）；本票下游是 `ensure_can_delete`，
它的语义是"资源标了 user_id 且与主体不同 → 403；**资源无归属 → 不视为
越权**"——NULL 老行**天然放行**，所以不需要 `OR IS NULL`，只需要让
`p_user` 非空。判据仍是"下游有没有现成的收敛"，只是收敛落点不同。

**修法同票 02 给 `_ensure_can_decide` 的形状**：收敛 principal 本身，
**不给 `ensure_can_delete` 加形参**（它被 27 处写侧共用，是承重墙）。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal, ensure_can_delete
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.offload_service import read_offload_file, write_offload_file

MARK_B_BODY = "ZZBBBZZ B 的私有记忆全文（卸载）：对家报价底牌 88 万，签约期三个月"
MARK_A_BODY = "ZZAAAAZZ A 自己的记忆全文（卸载）"
MARK_LEGACY = "ZZLEGZZ 老行的卸载全文（NULL 属主）"
MARK_DEFAULT = "ZZDEFZZ default 自己的卸载全文"


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def offload_dir(tmp_path, monkeypatch):
    """真实 tmp_path 落盘（不 mock 文件系统）。"""
    monkeypatch.setattr(
        "lantai.services.offload_service.settings.OFFLOAD_OUTPUT_DIR", str(tmp_path)
    )
    return tmp_path


def _seed(engine):
    """三条归属形状：B 的 / A 自己的 / NULL 老行 + default 自己的。

    同时**真实写一份卸载文件**（写侧不需要身份，这是既定用法）。
    """
    now = utcnow()
    with Session(engine) as s:
        for mid, owner, body in [
            ("m-B", "user-B", MARK_B_BODY),
            ("m-A", "user-A", MARK_A_BODY),
            ("m-legacy", None, MARK_LEGACY),
            ("m-def", "default", MARK_DEFAULT),
        ]:
            write_offload_file(mid, body)
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=body[:20],
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
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()


def _principal(uid=None, *, role="user"):
    return Principal(
        tenant_id=None, user_id=uid, agent_id=None, session_id=None, role=role, allowed_lanes=None
    )


def _run(engine, fn):
    """把 engine 指到被测临时库（探针踩坑：只改 settings 不改 engine，
    种子进隔离库、查询读宿主库）。"""
    import lantai.storage.db as db_module

    orig = db_module.engine
    db_module.engine = engine
    try:
        return fn()
    finally:
        db_module.engine = orig


class TestEnsureCanDeleteNoneIsNoop:
    """先钉住根因事实：`ensure_can_delete(None, ...)` 一个守卫都不触发。"""

    def test_ensure_can_delete_none_is_noop(self):
        """None 传给 ensure_can_delete → 不抛错（所以"调用了"≠"校验了"）。"""
        # 如果哪天 ensure_can_delete 改成对 None 抛错，这条会红——
        # 那意味着本票的修法可以简化（不需要收敛 principal），
        # 是**好消息**，不是回归。
        ensure_can_delete(None, resource_user_id="user-B", resource_tenant_id=None, lane="general")


class TestOffloadNonePrincipal:
    """`principal=None` 不再等于"整段归属校验跳过"。"""

    def test_none_principal_cannot_read_others_offload(self, engine, offload_dir):
        """不带身份读 B 的卸载全文 → 403（拿不到 read_text() 的全文）。

        错误形态是 `HTTPException(403)`（来自 `ensure_can_delete`），
        **不是** `FileNotFoundError`——那是"记忆不存在"的语义。
        区分二者很重要：403 说"存在但不是你的"，404 说"不存在"。
        这里用 403 是对的（探针 S3 已实证带身份读 B 就是 403）。
        """
        from fastapi import HTTPException

        _seed(engine)
        with pytest.raises(HTTPException) as exc:
            _run(engine, lambda: read_offload_file("m-B", principal=None))
        assert exc.value.status_code == 403, f"状态码变了：{exc.value.status_code}"
        assert "another user" in str(exc.value.detail).lower(), f"理由变了：{exc.value.detail}"

    def test_none_principal_still_reads_legacy_and_default(self, engine, offload_dir):
        """单人部署不空转：无身份仍读得到 NULL 老行与 default 自己的。

        **这是本修法的收益边界**——真实库 636/657 行 memoryitem 是 NULL
        属主，`ensure_can_delete` 对"资源无归属"的既定语义就是"不视为
        越权"。所以这里不需要 `OR IS NULL`，只要让 p_user 非空。
        """
        _seed(engine)
        r = _run(engine, lambda: read_offload_file("m-legacy", principal=None))
        assert MARK_LEGACY in (r.get("content") or ""), "无身份读不到 NULL 老行的卸载全文"
        r2 = _run(engine, lambda: read_offload_file("m-def", principal=None))
        assert MARK_DEFAULT in (r2.get("content") or ""), "无身份读不到 default 自己的卸载全文"

    def test_explicit_user_still_narrowed(self, engine, offload_dir):
        """回归护栏：显式 user-A 读 B → 403；读自己的 → 成功。"""
        _seed(engine)
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _run(engine, lambda: read_offload_file("m-B", principal=_principal("user-A")))
        r = _run(engine, lambda: read_offload_file("m-A", principal=_principal("user-A")))
        assert MARK_A_BODY in (r.get("content") or ""), "A 该能读自己的卸载全文"

    def test_admin_principal_still_unfiltered(self, engine, offload_dir):
        """admin 仍全权——运维排障路径不能瞎（同 01a/07/09 口径）。"""
        _seed(engine)
        r = _run(engine, lambda: read_offload_file("m-B", principal=_principal(None, role="admin")))
        assert MARK_B_BODY in (r.get("content") or ""), "admin 该读到 B 的卸载全文"

    def test_write_read_roundtrip_with_explicit_principal(self, engine, offload_dir):
        """改写 `test_offload.py:67` 的 None 调用为显式身份，roundtrip 覆盖不丢。

        原调用 `read_offload_file("mem_7")` 的目的是验"write 后能 read 回"
        （写侧 roundtrip），不是验 None 语义。给它一个显式身份，
        断言与覆盖都保留。
        """
        now = utcnow()
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id="mem_7",
                    memory_type="semantic",
                    key="k-mem_7",
                    content="长记忆全文 " + "内容" * 100,
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
                    user_id="user-A",
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()
        content = "长记忆全文 " + "内容" * 100
        write_offload_file("mem_7", content)
        r = _run(engine, lambda: read_offload_file("mem_7", principal=_principal("user-A")))
        assert r["content"] == content
        assert r["memory_id"] == "mem_7"
