"""票 `.scratch/mcp-identity-gaps/09`：`scene_get` / `scenes_list` 无身份时全表。

**先说影响**：`MemoryScene` 没有归属列（`tables.py:174`——场景是聚类产物），
归属只能按**成员记忆**反查。而 `get_scene` / `list_scenes` 的判据都是
`principal is not None and not is_admin`——宿主不透传 `user_id` 时
`principal=None`，**整段归属校验跳过**：

- `get_scene` 返回 B 的场景 `summary`（LLM 依据成员内容生成的摘要）
  **和全部成员的完整正文**；
- `list_scenes` 把 B 的场景连同摘要一起列出来——A 刷列表就读到 B 的
  场景主题画像。

决定性实证（`.scratch/mcp-identity-gaps/probe_scene_none_semantics.py`，
8 场景）：

```
S1 get_scene('sc-B', None)     → B 的 summary + 成员全文   ❌ 洞
S2 list_scenes(None)           → 含 sc-B 与 B 的 summary    ❌ 洞
S3 get_scene('sc-B', user-A)   → scene not found            ✅ 护栏（收窄正确）
S4 list_scenes(user-A)         → 只 2 个（A 的）             ✅ 护栏
S5 get_scene('sc-mix', user-A) → 只 m-mix-A                 ✅ 护栏
S6 get_scene('sc-mix', None)   → m-mix-A + m-mix-B          ❌ 洞
S7 get_scene('sc-B', admin)    → B 的正文                   ✅ 护栏（admin 全权）
S8 list_scenes(None)           → default + NULL 老场景       ✅ 单人部署不空转
```

**四个护栏全绿说明窄化逻辑本身是对的，问题只在 None 那一侧**——
与票 07（`mem_recent`）同一个形状，修法同一个真源。

**修法**：把两处判据从 `principal is not None and not is_admin` 改成
`not is_admin`。`getattr(None, "is_admin", False)` 返回 False，
所以 None 照样进收窄分支，再由 `_scene_visible_member_ids` 里已有的
`viewer_of(principal)` 收敛到 `"default"`——**不需要新写收敛逻辑**。

**与票 07 的形状差异（值得记）**：07 是 `if principal is None:` 单独加
一条 cond；这里改成 `not is_admin` 即可，因为下游已经调了 `viewer_of`。
**判据是"下游有没有现成的收敛"，不是照搬上一票的代码形状。**

**顺带修正一处过期 docstring**：`get_scene` 原写"混了 B 的成员的簇仍会
把 B 的正文带出来"——那是 `:288` 的成员过滤补上**之前**的说法。
探针 S5 实测 A 只见 `m-mix-A`。**docstring 过期比没有 docstring 更危险**，
它会让人照着已经不存在的行为做判断。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem, MemoryScene
from lantai.services.scene_service import get_scene, list_scenes

MARK_B_BODY = "ZZBBBZZ B 的私有记忆正文：对家报价底牌 88 万"
MARK_B_SUM = "ZZSUMZZ B 的场景摘要：关于对家报价的策略簇"
MARK_MIX_B = "ZZMIXBBBZZ 混合簇里 B 的成员正文"


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


def _seed(engine):
    """三条形状：纯 B 的簇 / A 自己的簇 / 混合簇（A + B 各一成员）。"""
    now = utcnow()
    with Session(engine) as s:
        for sid, nm, summ in [
            ("sc-B", "B 的场景", MARK_B_SUM),
            ("sc-A", "A 的场景", "A 自己的场景摘要"),
            ("sc-mix", "混合簇", "混合簇的摘要"),
        ]:
            s.add(
                MemoryScene(
                    id=sid,
                    name=nm,
                    summary=summ,
                    heat=1,
                    member_count=2,
                    created_at=now,
                    updated_at=now,
                )
            )
        for mid, owner, content, sid in [
            ("m-B", "user-B", MARK_B_BODY, "sc-B"),
            ("m-A", "user-A", "A 的记忆", "sc-A"),
            ("m-mix-A", "user-A", "混合簇里 A 的成员", "sc-mix"),
            ("m-mix-B", "user-B", MARK_MIX_B, "sc-mix"),
        ]:
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
                    scene_id=sid,
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
    """在被测函数自己的 session 里跑——`scene_service` 用 `db.get_session()`，
    必须把 engine 指到被测的临时库（探针踩坑：只改 settings 不改 engine，
    种子进隔离库、查询读宿主库，表现为场景凭空 404）。"""
    import lantai.storage.db as db_module

    orig = db_module.engine
    db_module.engine = engine
    try:
        return fn()
    finally:
        db_module.engine = orig


class TestSceneNonePrincipal:
    """`principal=None` 不再等于"整段归属校验跳过"。"""

    def test_none_principal_cannot_get_others_scene(self, engine):
        """不带身份下钻 B 的场景 → scene not found（拿不到 summary 与成员全文）。"""
        _seed(engine)
        with pytest.raises(ValueError, match="scene not found"):
            _run(engine, lambda: get_scene("sc-B", principal=None))

    def test_none_principal_cannot_list_others_scene(self, engine):
        """不带身份列场景 → 不含 B 的场景与其 summary。"""
        _seed(engine)
        r = _run(engine, lambda: list_scenes(50, principal=None))
        scenes = r.get("scenes") or []
        ids = {s["id"] for s in scenes}
        assert "sc-B" not in ids, f"无身份列到了 B 的场景：{ids}"
        assert all(MARK_B_SUM not in (s.get("summary") or "") for s in scenes), (
            "无身份读到了 B 的场景摘要"
        )

    def test_none_principal_cannot_read_mixed_cluster_other_member(self, engine):
        """不带身份下钻混合簇 → 见不到 B 的成员正文。

        这条是**本票最要害的**：混合簇对 A 是可见的（有 A 的成员），
        洞在于 None 时把 B 的成员也一起带出来。
        """
        _seed(engine)
        # 混合簇对无身份**整个不存在**——`_scene_visible_member_ids` 收敛到
        # "default" 后一个可见成员都没有（成员是 A 与 B），于是 :299 直接 404。
        with pytest.raises(ValueError, match="scene not found"):
            _run(engine, lambda: get_scene("sc-mix", principal=None))

    def test_explicit_user_still_narrowed(self, engine):
        """回归护栏：显式 user-A 的行为不变（纯 B 不存在、混合簇只见自己的成员）。"""
        _seed(engine)

        with pytest.raises(ValueError, match="scene not found"):
            _run(engine, lambda: get_scene("sc-B", principal=_principal("user-A")))

        r = _run(engine, lambda: get_scene("sc-mix", principal=_principal("user-A")))
        assert r is not None, "A 该看得见混合簇（有 A 的成员）"
        member_ids = {m["id"] for m in r["members"]}
        assert member_ids == {"m-mix-A"}, f"A 的混合簇成员收窄结果变了：{member_ids}"

    def test_admin_principal_still_unfiltered(self, engine):
        """admin 仍全权——运维排障路径不能瞎（同 01a/07 口径）。"""
        _seed(engine)
        r = _run(engine, lambda: get_scene("sc-B", principal=_principal(None, role="admin")))
        assert r is not None, "admin 被挡住了（该全权）"
        assert any(MARK_B_BODY in (m.get("content") or "") for m in r["members"]), (
            "admin 该读到 B 的成员全文"
        )

    def test_single_user_deployment_not_broken(self, engine):
        """单人部署不能空转：无身份仍见 `default` 自己的场景 + NULL 老行场景。

        **这是本修法的收益边界**——真实库 636/657 行 memoryitem 是 NULL 属主，
        收敛到 `"default"` 后这些老行与 default 自己的行必须仍可见，
        否则"为了安全把场景列表清空"就是另一个 bug。
        """
        now = utcnow()
        with Session(engine) as s:
            for sid, nm in [("sc-def", "default 的场景"), ("sc-legacy", "老场景")]:
                s.add(
                    MemoryScene(
                        id=sid,
                        name=nm,
                        summary=f"{nm}的摘要",
                        heat=1,
                        member_count=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            for mid, owner, sid in [
                ("m-def", "default", "sc-def"),
                ("m-legacy", None, "sc-legacy"),
            ]:
                s.add(
                    MemoryItem(
                        id=mid,
                        memory_type="semantic",
                        key=f"k-{mid}",
                        content=f"c-{mid}",
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
                        scene_id=sid,
                        created_at=now,
                        updated_at=now,
                    )
                )
            s.commit()

        r = _run(engine, lambda: list_scenes(50, principal=None))
        ids = {s["id"] for s in (r.get("scenes") or [])}
        assert {"sc-def", "sc-legacy"} <= ids, (
            f"单人部署场景列表被清空了（该含 default 自己的与 NULL 老场景）：{ids}"
        )
