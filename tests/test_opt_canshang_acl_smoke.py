"""归属锁：参商（canshang）无身份时不得读到别人属主的记忆。

MCP `conflict_scan_consistency` 的宿主若不透传 `user_id`，`_principal_from_params`
返回 `None`。修前 `CanshangEngine._check_access` 对 None 直接 `return True`、
查询侧也不加属主条件——任意调用方都能借扫描读到全库记忆的正文片段
（`description` 里 `content[:30]`，`metadata` 里还有更多结构）。

口径与 `famen` / `zuozheng` / `muci` / `diben`（票 57）一致：None 收敛到
"default" 属主空间（`user_id in (None, "default")`）。不 mock 产品逻辑。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

# 用「单机 vs 集群」这对状态反义词：canshang 对它零实词交叉即可判违例，
# 于是违例集合确实会带上两条记忆的 id——若只造语义相斥但词面不撞的内容，
# 违例恒空，断言就成了空转（第一版就是这么假绿的）。
_B_SECRET = "用户B的部署方案是单机"
_B_SECRET_2 = "用户B的部署方案是集群"


@pytest.fixture
def audit_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    now = utcnow()
    with Session(engine) as s:
        for mid, text, owner in (
            ("mem-b-1", _B_SECRET, "user_b"),
            ("mem-b-2", _B_SECRET_2, "user_b"),
            ("mem-a-1", "用户A的部署方案是单机", "user_a"),
            ("mem-null", "老数据的部署方案是集群", None),
        ):
            s.add(
                MemoryItem(
                    id=mid,
                    key=mid,
                    content=text,
                    lane="general",
                    status="active",
                    lifecycle_status="active",
                    confidence=0.8,
                    importance=0.5,
                    created_at=now,
                    updated_at=now,
                    user_id=owner,
                )
            )
        s.commit()
    return engine


def _audit(engine, principal):
    from lantai.cognition.canshang import CanshangEngine

    with Session(engine) as s:
        engine_ = CanshangEngine(session=s, principal=principal)
        return engine_.audit_consistency(limit=50)


def _seen_ids(report) -> set[str]:
    ids: set[str] = set()
    for v in report.violations:
        ids.update(v.conflicting_memory_ids or [])
    return ids


def test_none_principal_cannot_see_other_owner(audit_env):
    """无身份扫描不得产出 B 的记忆（修前会全库扫）。"""
    report = _audit(audit_env, None)
    ids = _seen_ids(report)
    assert "mem-b-1" not in ids and "mem-b-2" not in ids, (
        f"无身份扫描读到了别人的记忆：{sorted(ids)}"
    )


def test_none_principal_sees_default_space(audit_env):
    """反向保险：收敛到 "default" 不等于全瞎——NULL 属主的老行必须仍可见。

    否则单人部署下审计整体空转（同 `hybrid_search` 收敛口径的既定论证）。
    """
    report = _audit(audit_env, None)
    # 只有一条 NULL 属主行时凑不出冲突对，故断言扫描计数而不是违例：
    # 覆盖到的行数为 1 才说明它没被一并挡掉。
    assert report.total_scanned >= 1, "无身份扫描把 NULL 属主老行也挡掉了（全瞎）"


def test_explicit_ids_also_filtered(audit_env):
    """显式点名别人的 id 也读不到（主键直读那条路同样过 ACL）。"""
    report = _audit(audit_env, None)
    ids = _seen_ids(report)
    assert "mem-b-1" not in ids
    # 直接点名：memory_ids 分支
    from lantai.cognition.canshang import CanshangEngine

    with Session(audit_env) as s:
        engine_ = CanshangEngine(session=s, principal=None)
        named = engine_.audit_consistency(memory_ids=["mem-b-1", "mem-b-2"], limit=50)
    assert not _seen_ids(named), "显式点名绕过了归属校验"


def test_owner_sees_own_memories(audit_env):
    """A 仍能扫到自己的记忆（防止修成"谁都看不见"的假绿）。

    用 `total_scanned` 而不是违例集合：本夹具里 A 只有一条记忆，凑不出冲突对，
    违例恒为空——拿违例断言会让本锁对所有实现都通过（空转）。
    """
    from lantai.core.acl import Principal

    report = _audit(audit_env, Principal(user_id="user_a"))
    assert report.total_scanned >= 1, "属主扫不到自己的记忆——ACL 修过头了"
