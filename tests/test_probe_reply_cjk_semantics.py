"""探颐中文答复语义回归（续审 2026-10-31 P0-2）。

修复前实测：分支 B（模糊记忆求证）对「对，没变」「是的，还是喝乌龙茶」「我下周放假再说」
全部判否定并归档记忆；分支 A 对「是的，但其实我还没决定」直接用新说法覆盖旧正文。
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.services.probing_service import resolve_probe_response
from lantai.storage.fts import init_fts


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as c:
        init_fts(c.connection.driver_connection)
    return e


def _probe_memory(engine, reply: str):
    with Session(engine) as s:
        s.add(MemoryItem(id="m1", content="用户偏好乌龙茶", status="active", confidence=0.5))
        s.commit()
        r = resolve_probe_response(probe_id="probe_m1", user_reply=reply, session=s)
        m = s.get(MemoryItem, "m1")
        return r["action"], m.status, m.content


def _probe_conflict(engine, reply: str):
    with Session(engine) as s:
        s.add(MemoryItem(id="m2", content="用户偏好乌龙茶", status="active"))
        s.add(
            ConflictEvent(
                id="c1",
                memory_id="m2",
                incoming_ref="用户偏好改成龙井",
                rule_name="x",
                status="open",
            )
        )
        s.commit()
        r = resolve_probe_response("c1", reply, session=s)
        return r["action"], s.get(MemoryItem, "m2").content


@pytest.mark.parametrize(
    "reply",
    ["对，没变", "是的，还是喝乌龙茶", "对，一直保持这个习惯", "没有变，依然是这样"],
)
def test_branch_b_unchanged_is_confirmation(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "confirmed"
    assert status == "active"


@pytest.mark.parametrize(
    "reply", ["嗯，我下周放假再说", "没有问题，是这样", "是不是假期改了我也不清楚"]
)
def test_branch_b_unclear_never_archives(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "pending"
    assert status == "active"


@pytest.mark.parametrize("reply", ["不对，我早就不喝了", "写错了", "no, that's wrong"])
def test_branch_b_real_negation_still_archives(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "archived"
    assert status == "archived"


@pytest.mark.parametrize("reply", ["是的，但其实我还没决定", "是吧，我也说不好"])
def test_branch_a_hedge_does_not_overwrite(engine, reply):
    action, content = _probe_conflict(engine, reply)
    assert action == "pending"
    assert content == "用户偏好乌龙茶"


def test_branch_a_clear_yes_still_applies(engine):
    action, content = _probe_conflict(engine, "对，改成龙井了")
    assert action == "applied"
    assert content == "用户偏好改成龙井"


def test_branch_a_keep_old_still_dismisses(engine):
    action, content = _probe_conflict(engine, "不是，还是乌龙茶")
    assert action != "applied"
    assert content == "用户偏好乌龙茶"
