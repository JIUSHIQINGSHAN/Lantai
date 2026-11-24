"""重构守护锁（第二批）：候选裁决链的归属与浪费调用。

本文件锁住两处**已核实**的问题：

1. `review_candidate` 批准路径调 `propose_from_candidate` 时**没把 principal
   传下去**——proposer 的 EXISTING 取数只在 `principal is not None` 时才按归属
   收窄，于是 A 的候选审批会把 B 的记忆正文拼进外部 LLM 的提示词；同时
   `target_key` 是 LLM 的不可信输出，无收窄时可解析到别人的记忆 id。
2. `apply_proposal` 对 deprecate / merge 提案**白跑一次 embed**（网络往返）：
   `emb` 只有 add / update / consolidation 三支用得到，deprecate 用 `evidence_ids`、
   merge 另算 `emb2`。而这次 embed 在 CAS 认领之前，直接拉长该票想缩短的竞态窗。

纪律：外部网络面（LLM / embed）允许替身，被测函数的判断逻辑真跑。
"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem, MemoryProposal
from lantai.storage.fts import init_fts

_B_SECRET = "用户B的私人计划：下月去东京出差"


@pytest.fixture
def owner_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    return engine


def _seed_candidate_and_foreign_memory(engine):
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
                status="pending_review",
                user_id="user_a",
            )
        )
        s.add(
            MemoryItem(
                id="mem_b",
                key="B的计划",
                content=_B_SECRET,
                lane="general",
                status="active",
                importance=0.5,
                created_at=now,
                updated_at=now,
                user_id="user_b",
            )
        )
        s.commit()


class TestReviewCandidatePassesPrincipal:
    def test_foreign_memory_not_in_llm_prompt(self, owner_env):
        """A 审批自己的候选时，提示词里不得出现 B 的记忆正文。"""
        from lantai.services import candidate_service

        _seed_candidate_and_foreign_memory(owner_env)
        prompts: list[str] = []

        def capture_prompt(system, user, timeout=None):
            prompts.append(user)
            return {
                "proposal_type": "add",
                "key": "a-key",
                "content": "用户A新增的记忆",
                "confidence": 0.9,
                "reason": "test",
            }

        with patch("lantai.evolution.proposer.chat_json", capture_prompt):
            res = candidate_service.review_candidate(
                "cand_a",
                approve=True,
                reason="ok",
                principal=Principal(user_id="user_a"),
            )

        assert prompts, "proposer 必须真的调用 LLM（否则本锁空转）"
        combined = "\n".join(prompts)
        assert _B_SECRET not in combined, (
            "B 的私有正文进了 A 的审批提示词——review_candidate 没把 principal 传给 proposer"
        )
        assert res.get("ok") is True, f"审批应成功：{res}"


class TestApplyProposalSkipsUselessEmbed:
    def _seed_proposal(self, engine, proposal_type: str):
        now = utcnow()
        with Session(engine) as s:
            if proposal_type != "add":
                # add 走「key 已存在 → 转人工复核」的硬门，故只为需要目标的分支
                # 建既有记忆；add 分支用全新 key，走真实新建路径。
                s.add(
                    MemoryItem(
                        id="mem_target",
                        key="旧值",
                        content="将被撤回的旧说法",
                        lane="general",
                        status="active",
                        importance=0.5,
                        created_at=now,
                        updated_at=now,
                        user_id="user_a",
                    )
                )
            s.add(
                MemoryProposal(
                    id=f"prop_{proposal_type}",
                    proposal_type=proposal_type,
                    target_memory_id="mem_target" if proposal_type != "add" else None,
                    key="旧值" if proposal_type != "add" else "全新键",
                    content="新说法",
                    confidence=0.9,
                    reason="test",
                    status="pending",
                    evidence_ids=[],
                    # 正文真源是 proposed_patch（`apply_proposal` 的 `content` 取自
                    # `patch.get("content")`）——不填就等于空正文，embed 分支会被
                    # `if content` 短路，本锁就测不到东西（假绿）。
                    proposed_patch={"content": "新说法", "key": "全新键"},
                    created_at=now,
                    user_id="user_a",
                )
            )
            s.commit()

    def test_deprecate_does_not_embed(self, owner_env):
        """deprecate 提案不需要 embedding：一次都不该发嵌入请求。"""
        from lantai.evolution import promoter

        self._seed_proposal(owner_env, "deprecate")
        calls: list = []

        def counting_embed(texts, timeout=None):
            calls.append(texts)
            return [[0.1] * 8]

        with patch.object(promoter, "embed", counting_embed):
            res = promoter.apply_proposal("prop_deprecate", principal=Principal(user_id="user_a"))

        assert res.get("ok") is True, f"deprecate 应成功应用：{res}"
        assert calls == [], f"deprecate 白跑了 {len(calls)} 次 embedding"

    def test_add_still_embeds(self, owner_env):
        """反向保险：add 提案必须真的走 embed（证明上一条不是靠关掉整条链）。"""
        from lantai.evolution import promoter

        self._seed_proposal(owner_env, "add")
        calls: list = []

        def counting_embed(texts, timeout=None):
            calls.append(texts)
            return [[0.1] * 8]

        with (
            patch.object(promoter, "embed", counting_embed),
            patch.object(promoter, "index_memory_item", lambda *a, **k: None),
        ):
            res = promoter.apply_proposal("prop_add", principal=Principal(user_id="user_a"))

        assert res.get("ok") is True, f"add 应成功应用：{res}"
        assert calls, "add 提案必须计算 embedding（本锁防「干脆全不 embed」的假修）"
