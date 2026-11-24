"""入口收敛锁：拾遗的 MCP/REST 服务入口不得让匿名调用拿到别人的记忆。

`ShiyiEngine._check_access` 对 `principal=None` 全放——这是**内部调用**的
契约（worker/CLI/无身份直调不空转，`tests/test_key_gate_and_shiyi_perf.py`
钉住）。而 `services/shiyi_service.py` 是 MCP `shiyi_remedy` /
`shiyi_pipeline` 的入口面：宿主不透传 `user_id` 时 `_principal_from_params`
返回 None，引擎全放就等于匿名调用方拿到全库模糊召回（候选带完整正文）。

修法按本仓范式走「不动承重墙、在调用方收敛」（同 `hybrid_search.
_converge_principal`）。本文件同时锁两头：

1. 服务入口传 None → 收敛成 default，读不到别人属主的行；
2. 引擎直接传 None → **仍全放**（既有内部契约不许被顺手收紧）。

不 mock 产品逻辑：真实内存库 + 真 LIKE 预筛。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

_QUERY = "K8S 容器重启总是启动"
_SECRET = "Kubernetes集群中Pod容器重启策略配置为Always"


@pytest.fixture
def shiyi_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    now = utcnow()
    with Session(engine) as s:
        for mid, owner in (("mem-b", "user_b"), ("mem-null", None)):
            s.add(
                MemoryItem(
                    id=mid,
                    key=mid,
                    memory_type="semantic",
                    content=_SECRET,
                    lane="general",
                    domain="user",
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
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    return engine


def _ids(out: dict) -> list[str]:
    return [item.get("id") for item in out.get("remedied_items") or []]


def test_service_entry_with_none_cannot_see_other_owner(shiyi_env):
    """MCP 入口（principal=None）读不到 user_b 的记忆。"""
    from lantai.services.shiyi_service import remedy_recall_service

    out = remedy_recall_service(query=_QUERY, domain="user", limit=5, principal=None)
    assert "mem-b" not in _ids(out), f"匿名入口读到了别人的记忆：{_ids(out)}"


def test_service_entry_with_none_still_sees_default_space(shiyi_env):
    """反向保险：收敛不等于全瞎——NULL 属主老行必须仍可补救。

    否则单人部署（真实库 96.8% 是 NULL 属主）下拾遗整体空转。
    """
    from lantai.services.shiyi_service import remedy_recall_service

    out = remedy_recall_service(query=_QUERY, domain="user", limit=5, principal=None)
    assert "mem-null" in _ids(out), f"NULL 属主老行被一并挡掉了：{_ids(out)}"


def test_engine_direct_none_keeps_legacy_contract(shiyi_env):
    """引擎层无身份直调仍全放（内部调用契约，既有限制不许被顺手收紧）。"""
    from lantai.cognition.shiyi import ShiyiEngine

    with Session(shiyi_env) as s:
        engine = ShiyiEngine(session=s)
        out = engine.remedy_recall(query=_QUERY, domain="user", limit=5)
    assert "mem-b" in _ids(out), "引擎层 None 全放的既有契约被改掉了（worker/CLI 会空转）"


def test_owner_still_sees_own(shiyi_env):
    """归属人自己的行照常召回（防「谁都看不见」的假绿）。"""
    from lantai.core.acl import Principal
    from lantai.services.shiyi_service import remedy_recall_service

    out = remedy_recall_service(
        query=_QUERY, domain="user", limit=5, principal=Principal(user_id="user_b")
    )
    assert "mem-b" in _ids(out), f"属主看不到自己的记忆：{_ids(out)}"
