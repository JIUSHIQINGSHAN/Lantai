"""票 `.scratch/mcp-identity-gaps/02`：`decide_proposal(principal=None)` 越权裁决。

**先说影响**：这是**真脏写**，不是"收紧过头"。MCP `proposal_decide`
在宿主不透传 `user_id` 时拿到 `principal=None`，而
`evolution_service._ensure_can_decide` 的第一行是
`if principal is None: return`——**归属校验整段跳过**。

决定性实证（`.scratch/mcp-identity-gaps/probe_02e_decide_proposal.py`）：

```
S1 principal=None   reject B 的提案   → pending → rejected  ❌ 脏写
S2 principal=user-A reject B 的提案   → 403，未改动          ✅
S4 principal=None   reject default    → pending → rejected  （单人部署正确行为）
S5 principal=None   approve B 的提案  → pending → rejected  ❌ 脏写（apply 还被
                                           自己的下游挡了一层，但状态已改）
```

**S5 特别值得记**：`approve` 比 `reject` 重（会 `apply_proposal` 直接写库），
下游 `apply_proposal` 确实按归属挡住了内容写入（宁 miss 不脏写，返回
`{"ok": false}`），**但提案状态已经从 pending 变成 rejected**——
上游 `decide_proposal` 在第 102 行先改状态再 commit，下游失败也挽不回。
所以"下游有校验"不能替代"上游先校验"。

**HTTP 侧不受影响**：`routes_evolution.py:62` 与 `routes_work_items.py:65`
都显式传 `ctx`（`get_current_user` 永不返回 None）。**这个洞只有 MCP
入口能触发**——第二个入口面的典型代价，同票 04/05。

**修法**：`_ensure_can_decide` 的 `None` 口径从"不校验"改成
"经 `acl.viewer_of` 收敛到 `default` 后照常校验"。
与 04 号票给 `checkpoint_write` 的修法同一个真源，不发明第二份口径。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.schemas import ProposalDecisionReq
from lantai.models.tables import MemoryItem, MemoryProposal
from lantai.services.evolution_service import decide_proposal
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


def _seed_memory(engine, mid, owner, content):
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


def _seed_proposal(engine, pid, owner, target_mid=None):
    now = utcnow()
    with Session(engine) as s:
        s.add(
            MemoryProposal(
                id=pid,
                tenant_id="tenant-1",
                user_id=owner,
                candidate_id=None,
                proposal_type="add",
                status="pending",
                target_memory_id=target_mid,
                reason="probe",
                proposed_patch={"content": "提案正文"},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


def _state(engine, pid):
    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT user_id, status FROM memoryproposal WHERE id=?", (pid,)
        ).fetchone()
    return tuple(row) if row else None


class TestDecideProposalNonePrincipal:
    """`principal=None` 不再等于"跳过归属校验"。

    **Red 实证（2026-09-28，修前）**：S1/S5 两条把 B 的提案从 pending
    改成 rejected——`_ensure_can_decide` 的 `if principal is None: return`
    让 MCP 侧（宿主不透传 user_id）完全不过滤。
    """

    def test_none_principal_cannot_reject_others_proposal(self, engine, monkeypatch):
        """不带身份拒绝 B 的提案 → 必须 403 且状态不变（宁 miss 不脏写）。"""
        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        _seed_proposal(engine, "p-B", "user-B")
        before = _state(engine, "p-B")

        with pytest.raises(Exception) as ei:
            decide_proposal(
                "p-B",
                ProposalDecisionReq(approve=False, reason="x"),
                principal=None,
            )

        assert "403" in str(ei.value) or "belongs to another" in str(ei.value), (
            f"没有按越权拒绝：{ei.value}"
        )
        assert _state(engine, "p-B") == before, (
            f"B 的提案状态被改了（越权脏写）：{before} → {_state(engine, 'p-B')}"
        )

    def test_none_principal_cannot_approve_others_proposal(self, engine, monkeypatch):
        """不带身份批准 B 的提案 → 必须 403 且状态不变。

        **S5 是这条的由来**：approve 比 reject 重（会 apply_proposal 写库），
        下游虽按归属挡住了内容写入，但上游已先把状态改成 rejected。
        """
        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        _seed_memory(engine, "m-B", "user-B", "B 的原始记忆")
        _seed_proposal(engine, "p-B2", "user-B", "m-B")
        before = _state(engine, "p-B2")

        with pytest.raises(Exception) as ei:
            decide_proposal(
                "p-B2",
                ProposalDecisionReq(approve=True, reason=""),
                principal=None,
            )

        assert "403" in str(ei.value) or "belongs to another" in str(ei.value)
        assert _state(engine, "p-B2") == before, (
            f"B 的提案状态被改了：{before} → {_state(engine, 'p-B2')}"
        )

    def test_none_principal_can_still_decide_default_proposal(self, engine, monkeypatch):
        """单人部署不能空转：不带身份仍可裁决 `default` 的提案。

        这是本修法的**收益边界**——收敛到 `default` 而不是一律拒绝，
        否则 MCP 客户端（普遍不传 user_id）全部无法裁决。
        """
        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        _seed_proposal(engine, "p-def", "default")

        r = decide_proposal(
            "p-def",
            ProposalDecisionReq(approve=False, reason="x"),
            principal=None,
        )

        assert r.get("ok") is True, f"default 的提案也裁不动了：{r}"
        assert _state(engine, "p-def")[1] == "rejected"

    def test_explicit_user_still_blocked_from_others(self, engine, monkeypatch):
        """回归护栏：显式 user-A 裁 B 的提案仍 403（收窄没放松）。"""
        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        _seed_proposal(engine, "p-B3", "user-B")
        before = _state(engine, "p-B3")

        with pytest.raises(Exception) as ei:
            decide_proposal(
                "p-B3",
                ProposalDecisionReq(approve=False, reason="x"),
                principal=_principal("user-A"),
            )

        assert "403" in str(ei.value) or "belongs to another" in str(ei.value)
        assert _state(engine, "p-B3") == before

    def test_admin_principal_still_unrestricted(self, engine, monkeypatch):
        """admin 仍全权——运维排障路径不能瞎（同 01a 口径）。"""
        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        _seed_proposal(engine, "p-B4", "user-B")

        r = decide_proposal(
            "p-B4",
            ProposalDecisionReq(approve=False, reason="admin 操作"),
            principal=_principal(None, role="admin"),
        )

        assert r.get("ok") is True, f"admin 被挡住了：{r}"
