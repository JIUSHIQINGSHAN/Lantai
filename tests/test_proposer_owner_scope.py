"""提案 LLM 提示的归属隔离:候选属 A 时,EXISTING 不得含 B 的记忆正文。

不 mock 被测逻辑:真实内存库 + 真 FTS,仅替换外部 LLM 调用并记录其入参。
"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem
from lantai.storage.fts import init_fts

_B_SECRET_TEXT = "用户B的私人计划:下月去东京"


@pytest.fixture
def owner_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    return engine


def _seed(engine):
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryCandidate(
                id="cand_a",
                document_id="doc_cand_a",
                summary="用户A的候选摘要",
                claims=["声明"],
                actions=[],
                lane="general",
                status="new",
                user_id="user_a",
            )
        )
        s.add(
            MemoryItem(
                id="mem_b",
                key="B的计划",
                content=_B_SECRET_TEXT,
                lane="general",
                status="active",
                importance=0.5,
                created_at=now,
                updated_at=now,
                user_id="user_b",
            )
        )
        s.commit()


def _seed_visibility(engine):
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryCandidate(
                id="cand_a",
                document_id="doc_cand_a",
                summary="用户A的候选摘要",
                claims=["声明"],
                actions=[],
                lane="general",
                status="new",
                user_id="user_a",
            )
        )
        for mid, key, owner in (
            ("mem_a", "A的计划", "user_a"),
            ("mem_legacy", "老数据的计划", None),
            ("mem_b", "B的计划", "user_b"),
        ):
            s.add(
                MemoryItem(
                    id=mid,
                    key=key,
                    content=f"{key}正文",
                    lane="general",
                    status="active",
                    importance=0.5,
                    created_at=now,
                    updated_at=now,
                    user_id=owner,
                )
            )
        s.commit()


def _captured_prompt(engine, principal):
    from lantai.evolution.proposer import propose_from_candidate

    captured = {}

    def fake_chat_json(system, user):
        captured["user"] = user
        return {
            "proposal_type": "add",
            "target_key": "",
            "new_content": "新记忆",
            "memory_type": "semantic",
            "reason": "new fact",
            "confidence": 0.8,
        }

    with patch("lantai.evolution.proposer.chat_json", side_effect=fake_chat_json):
        propose_from_candidate("cand_a", {"decision": "promote_semantic"}, principal=principal)
    return captured["user"]


def test_normal_user_sees_own_and_legacy_memory_but_not_others(owner_env):
    from lantai.core.acl import Principal

    _seed_visibility(owner_env)
    prompt = _captured_prompt(owner_env, Principal(user_id="user_a", role="user"))

    assert "A的计划正文" in prompt
    assert "老数据的计划正文" in prompt
    assert "B的计划正文" not in prompt


def test_admin_and_unidentified_caller_see_all_memory(owner_env):
    from lantai.core.acl import Principal

    _seed_visibility(owner_env)
    admin_prompt = _captured_prompt(owner_env, Principal(user_id="admin_x", role="admin"))
    assert "A的计划正文" in admin_prompt
    assert "老数据的计划正文" in admin_prompt
    assert "B的计划正文" in admin_prompt

    none_prompt = _captured_prompt(owner_env, None)
    assert "B的计划正文" in none_prompt


def test_proposal_prompt_excludes_other_owner_memory(owner_env):
    """复现:A 的候选提案时,发给 LLM 的 EXISTING 不得包含 B 的记忆正文。"""
    from lantai.core.acl import Principal
    from lantai.evolution.proposer import propose_from_candidate

    _seed(owner_env)
    captured = {}

    def fake_chat_json(system, user):
        captured["user"] = user
        return {
            "proposal_type": "add",
            "target_key": "",
            "new_content": "新记忆",
            "memory_type": "semantic",
            "reason": "new fact",
            "confidence": 0.8,
        }

    with patch("lantai.evolution.proposer.chat_json", side_effect=fake_chat_json):
        propose_from_candidate(
            "cand_a", {"decision": "promote_semantic"}, principal=Principal(user_id="user_a")
        )

    assert _B_SECRET_TEXT not in captured["user"]
