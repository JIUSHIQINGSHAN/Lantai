"""票 57：principal=None 身份收敛缺失第四波——六引擎 + 底本二写方 + 润物参数面。

`.scratch/v0.68-next-window-2026-11/issues/57-principal-none-identity-convergence-family.md`

**先说影响**（MCP 宿主不透传 user_id → `_principal_from_params` 返回 None）：

| 面 | 无身份时（修复前） |
|---|---|
| 钩沉 dredge/snapshot | 不加任何 where，全库所有用户/租户的沉埋记忆全文 |
| 底本 synthesize | 落 NULL 属主 SessionCheckpoint（绕开受审计写方全部保护）；无 upsert 同 session 块行累积 |
| 卷宗 list/get/focus | 列全库卷宗、读任意绑定记忆原文 |
| 尘封 unarchive | 任意他人记忆解封回 active（ensure_can_delete 对 None 三段 getattr 全落空 → 放行） |
| 目次/法门/佐证 | `_check_access` None→True 全放行 |
| 润物 rules | 规则跨用户全表拉取 |

**修法**（acl.py 立法语义，同 zhaji/sanxing/qijuzhu/kaogong 已写对范式）：
`principal=None` 一律经 `viewer_of` 收敛到 `"default"`（只看默认属主 + NULL
老行）；显式全表一律 `Principal(user_id=SYSTEM_VIEWER)`。本批六引擎无任何
worker/scheduler 调用方（grep 实证），全表场景仅 SYSTEM_VIEWER 臂锁存在性。

NULL 老行口径不变：`OR IS NULL` 全保留——NULL 是「未记录」不是「属于所有人」。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.cognition.chenfeng import ChenfengEngine
from lantai.cognition.diben import DibenEngine
from lantai.cognition.famen import FamenEngine
from lantai.cognition.gouchen import GouchenEngine
from lantai.cognition.juanzong import JuanzongEngine
from lantai.cognition.muci import MuciEngine
from lantai.cognition.zuozheng import ZuozhengEngine
from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.models.tables import CognitiveRole, Dossier, MemoryItem, SessionCheckpoint


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=None,
        role=role,
    )


@pytest.fixture()
def test_db():
    """真 SQLite 内存库 + get_session 指向它（不 mock 计算逻辑）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    import lantai.storage.fts as fts

    with eng.connect() as conn:
        fts.init_fts(conn.connection.driver_connection)

    original = db_module.get_session

    def session_factory() -> Session:
        return Session(eng)

    db_module.get_session = session_factory
    yield Session(eng)
    # 必须还原成原来的可调用对象（同 test_mcp_none_scope_leaks 教训）
    db_module.get_session = original


@pytest.fixture()
def vector_stubs(monkeypatch):
    """解封委托的向量层在场核验打外部替身（被测闸门在引擎 ACL，不在委托）。"""
    import lantai.services.record_ops_service as ros

    monkeypatch.setattr(ros, "sync_vector_has", lambda _mid: True)
    monkeypatch.setattr(ros, "sync_vector_upsert", lambda _mid, _item: True)


def _seed_memory(
    s: Session,
    mid: str,
    owner: str | None,
    *,
    status: str = "archived",
    role: CognitiveRole = CognitiveRole.OBSERVATION,
    content: str | None = None,
) -> MemoryItem:
    m = MemoryItem(
        id=mid,
        memory_type="semantic",
        key=f"key-{mid}",
        content=content or f"{mid} 的正文",
        namespace="default",
        status=status,
        role=role,
        user_id=owner,
    )
    s.add(m)
    s.commit()
    return m


class TestGouchenNoneConvergence:
    def test_dredge_none_sees_only_default_and_null(self, test_db):
        """无身份打捞只见 default 属主 + NULL 老行（修复前全库可见）。"""
        _seed_memory(test_db, "mem-default", "default")
        _seed_memory(test_db, "mem-null", None)
        _seed_memory(test_db, "mem-bob", "bob")
        _seed_memory(test_db, "mem-alice", "alice")

        out = GouchenEngine().dredge_sunken_memories(session=test_db)

        ids = {it.memory_id for it in out.items}
        assert ids == {"mem-default", "mem-null"}, f"无身份只见 default/NULL，得到 {ids}"

    def test_dredge_named_user_narrowed(self, test_db):
        """具名用户只见自己 + NULL（现状即绿，防收窄回退）。"""
        _seed_memory(test_db, "mem-alice", "alice")
        _seed_memory(test_db, "mem-bob", "bob")
        _seed_memory(test_db, "mem-null", None)

        out = GouchenEngine(principal=_principal("alice")).dredge_sunken_memories(session=test_db)

        ids = {it.memory_id for it in out.items}
        assert ids == {"mem-alice", "mem-null"}

    def test_dredge_system_viewer_and_admin_full_table(self, test_db):
        """显式系统身份 / admin 全表（刻意，立法语义）。"""
        for owner in ("default", None, "bob", "alice"):
            _seed_memory(test_db, f"mem-{owner or 'null'}", owner)

        sys_out = GouchenEngine(principal=_principal(SYSTEM_VIEWER)).dredge_sunken_memories(
            session=test_db
        )
        assert len({it.memory_id for it in sys_out.items}) == 4

        admin_out = GouchenEngine(
            principal=_principal("root", role="admin")
        ).dredge_sunken_memories(session=test_db)
        assert len({it.memory_id for it in admin_out.items}) == 4

    def test_snapshot_none_narrowed(self, test_db):
        """历史语境重建同口径（修复前全库）。"""
        _seed_memory(test_db, "mem-default", "default")
        _seed_memory(test_db, "mem-bob", "bob")

        from lantai.core.time import utcnow

        out = GouchenEngine().reconstruct_historical_context(anchor_time=utcnow(), session=test_db)

        ids = {it.memory_id for it in out.memories}
        assert ids == {"mem-default"}, f"无身份重建只见 default/NULL，得到 {ids}"


class TestDibenSecondWriter:
    def test_synthesize_none_owns_default_not_null(self, test_db):
        """无身份落底本 owner 收敛 "default"，不再落 NULL 属主行。"""
        DibenEngine(session=test_db).synthesize_anchor(
            "sess-a", {"cp_active_intent": "迁移数据库", "cp_next_action": "跑迁移脚本"}
        )

        rows = test_db.exec(select(SessionCheckpoint)).fetchall()
        assert rows, "应落底本块行"
        owners = {r.user_id for r in rows}
        assert owners == {"default"}, f"owner 必须收敛 default（不落 NULL），得到 {owners}"

    def test_synthesize_upsert_no_accumulation(self, test_db):
        """同 session 重复落底本 = upsert 替换，块行不累积（受审计写方口径）。"""
        eng = DibenEngine(session=test_db)
        blocks = {"cp_active_intent": "迁移数据库", "cp_next_action": "跑迁移脚本"}
        eng.synthesize_anchor("sess-up", blocks)
        eng.synthesize_anchor("sess-up", blocks)

        rows = test_db.exec(select(SessionCheckpoint)).fetchall()  # type: ignore[attr-defined]
        sess_rows = [r for r in rows if r.session_id == "sess-up"]
        assert len(sess_rows) == 2, f"同 session 重写应替换（2 块 2 行），得到 {len(sess_rows)}"

    def test_synthesize_clamps_short_long_background(self, test_db):
        """对齐受审计写方四保护：过短不落、超长截断、后台通知整块丢弃。"""
        long_text = "很" * 700
        DibenEngine(session=test_db).synthesize_anchor(
            "sess-clamp",
            {
                "cp_active_intent": "ab",  # < CHECKPOINT_MIN_CONTENT=3 → 不落
                "cp_next_action": long_text,  # → 截断到 CHECKPOINT_MAX_CONTENT=600
                "cp_current_work": "[IMPORTANT: Background process xyz] 定时任务",  # → 整块丢弃
            },
        )

        rows = test_db.exec(select(SessionCheckpoint)).fetchall()  # type: ignore[attr-defined]
        by_key = {r.block_key: r.content for r in rows if r.session_id == "sess-clamp"}
        assert "cp_active_intent" not in by_key, "过短块（<3 字符）不得落库"
        assert len(by_key.get("cp_next_action", "")) == 600, "超长块截断到 600"
        assert "cp_current_work" not in by_key, "后台通知非会话内容，整块丢弃"

    def test_get_active_anchor_none_scoped(self, test_db):
        """无身份读激活底本只见 default/NULL（修复前任何人的都能读）。"""
        now_owner_rows = [
            SessionCheckpoint(
                session_id="sess-bob",
                user_id="bob",
                block_key="cp_active_intent",
                content="bob 的机密工作现场",
            )
        ]
        for r in now_owner_rows:
            test_db.add(r)
        test_db.commit()

        assert DibenEngine(session=test_db).get_active_anchor() is None, "无身份不得读到他人底本"

        # NULL 老行可读（口径不变）
        test_db.add(
            SessionCheckpoint(
                session_id="sess-null",
                user_id=None,
                block_key="cp_active_intent",
                content="老行工作现场",
            )
        )
        test_db.commit()
        anchor = DibenEngine(session=test_db).get_active_anchor()
        assert anchor is not None and anchor["session_id"] == "sess-null"


class TestJuanzongConvergence:
    def test_create_none_owner_default(self, test_db):
        """无身份建卷宗 owner 收敛 "default"（修复前落 NULL）。"""
        dos = JuanzongEngine(session=test_db).create_dossier("巡检卷宗")

        assert dos.user_id == "default", f"owner 必须收敛 default，得到 {dos.user_id!r}"

    def test_list_none_sees_only_default_null(self, test_db):
        """无身份列卷宗只见 default/NULL（修复前全库）。"""
        for did, owner in (("dos-default", "default"), ("dos-null", None), ("dos-bob", "bob")):
            test_db.add(Dossier(id=did, user_id=owner, title=f"{did} 标题"))
        test_db.commit()

        items = JuanzongEngine(session=test_db).list_dossiers()

        ids = {d.id for d in items}
        assert ids == {"dos-default", "dos-null"}, f"无身份只见 default/NULL，得到 {ids}"

    def test_get_dossier_none_other_denied(self, test_db):
        """无身份读他人卷宗 → None（修复前返回详情）。"""
        test_db.add(Dossier(id="dos-bob", user_id="bob", title="bob 的卷宗"))
        test_db.commit()

        assert JuanzongEngine(session=test_db).get_dossier("dos-bob") is None

    def test_focus_none_excludes_other_memories(self, test_db):
        """无身份聚焦他人卷宗 → 绑定的他人记忆原文不外泄。"""
        _seed_memory(test_db, "mem-bob-secret", "bob", status="active", content="机密部署要点")
        test_db.add(
            Dossier(id="dos-bob", user_id="bob", title="bob 的卷宗", memory_ids=["mem-bob-secret"])
        )
        test_db.commit()

        out = JuanzongEngine(session=test_db).focus_dossier_context("dos-bob")

        assert out.focused_memories == [], (
            f"无身份不得读他人绑定记忆原文，得到 {out.focused_memories}"
        )


class TestChenfengUnarchiveGate:
    def test_unarchive_none_other_user_403(self, test_db, vector_stubs):
        """无身份解封他人记忆 → 403（修复前静默越权解封）。"""
        _seed_memory(test_db, "mem-bob-arch", "bob", status="archived")

        with pytest.raises(HTTPException) as exc_info:
            ChenfengEngine(session=test_db).unarchive_memory("mem-bob-arch")

        assert exc_info.value.status_code == 403

    def test_unarchive_none_default_owned_ok(self, test_db, vector_stubs):
        """无身份解封 default 属主记忆 → 放行。"""
        _seed_memory(test_db, "mem-default-arch", "default", status="archived")

        out = ChenfengEngine(session=test_db).unarchive_memory("mem-default-arch")

        assert out.get("ok") is True

    def test_unarchive_system_viewer_other_ok(self, test_db, vector_stubs):
        """显式系统身份全表（刻意）：可解封他人记忆。"""
        _seed_memory(test_db, "mem-bob-arch2", "bob", status="archived")

        out = ChenfengEngine(session=test_db, principal=_principal(SYSTEM_VIEWER)).unarchive_memory(
            "mem-bob-arch2"
        )

        assert out.get("ok") is True

    def test_unarchive_named_other_403(self, test_db, vector_stubs):
        """具名跨属主 403（现状即绿，ensure_can_delete 单一真源防回退）。"""
        _seed_memory(test_db, "mem-bob-arch3", "bob", status="archived")

        with pytest.raises(HTTPException) as exc_info:
            ChenfengEngine(session=test_db, principal=_principal("alice")).unarchive_memory(
                "mem-bob-arch3"
            )

        assert exc_info.value.status_code == 403


class TestMuciFamenZuozhengNone:
    def test_muci_none_narrowed(self):
        """目次：None 收敛 default——他人条目拒，default/NULL 放行。"""
        eng = MuciEngine(principal=None)
        bob_item = MemoryItem(id="m-bob", content="x", user_id="bob")
        default_item = MemoryItem(id="m-default", content="x", user_id="default")
        null_item = MemoryItem(id="m-null", content="x", user_id=None)

        assert eng._check_access(bob_item) is False
        assert eng._check_access(default_item) is True
        assert eng._check_access(null_item) is True

        sys_eng = MuciEngine(principal=_principal(SYSTEM_VIEWER))
        assert sys_eng._check_access(bob_item) is True

    def test_famen_none_narrowed(self):
        """法门：None 收敛 default（swarm_shared 公共结晶口径不变）。"""
        eng = FamenEngine(principal=None)

        assert eng._check_access(owner_id="bob", tenant_id=None) is False
        assert eng._check_access(owner_id="default", tenant_id=None) is True
        assert eng._check_access(owner_id=None, tenant_id=None) is True
        assert (
            eng._check_access(owner_id="bob", tenant_id=None, shared_scope="swarm_shared") is True
        )

        sys_eng = FamenEngine(principal=_principal(SYSTEM_VIEWER))
        assert sys_eng._check_access(owner_id="bob", tenant_id=None) is True

    def test_zuozheng_none_narrowed(self):
        """佐证：None 收敛 default。"""
        eng = ZuozhengEngine(principal=None)

        assert eng._check_access(owner_id="bob", tenant_id=None) is False
        assert eng._check_access(owner_id="default", tenant_id=None) is True
        assert eng._check_access(owner_id=None, tenant_id=None) is True

        sys_eng = ZuozhengEngine(principal=_principal(SYSTEM_VIEWER))
        assert sys_eng._check_access(owner_id="bob", tenant_id=None) is True


class TestRunwuRulesConvergence:
    def test_extract_summary_none_only_default_null(self, test_db):
        """无身份取规则只见 default/NULL（修复前跨用户全表拉取）。"""
        _seed_memory(
            test_db,
            "rule-default",
            "default",
            status="active",
            role=CognitiveRole.RULE,
            content="迁移部署要遵循巡检 keyword 流程",
        )
        _seed_memory(
            test_db,
            "rule-bob",
            "bob",
            status="active",
            role=CognitiveRole.RULE,
            content="bob 的机密规则 keyword 特殊指令",
        )

        from lantai.runtime.runwu import RunwuEngine

        summary = RunwuEngine(db=test_db).extract_summary(
            task="keyword 流程怎么走", user_id=None, max_rules=5, max_probes=0
        )

        rules_text = " | ".join(summary.rules)
        assert "巡检" in rules_text
        assert "bob 的机密规则" not in rules_text, f"无身份不得拉取他人规则：{rules_text}"

    def test_extract_summary_system_viewer_full(self, test_db):
        """显式系统身份全表（刻意）。"""
        _seed_memory(
            test_db,
            "rule-bob2",
            "bob",
            status="active",
            role=CognitiveRole.RULE,
            content="bob 的系统级规则 keyword 指令",
        )

        from lantai.runtime.runwu import RunwuEngine

        summary = RunwuEngine(db=test_db).extract_summary(
            task="keyword 指令", user_id=SYSTEM_VIEWER, max_rules=5, max_probes=0
        )

        assert any("bob" in r for r in summary.rules)


class TestMcpArmsNoIdentity:
    def test_mcp_unarchive_no_identity_denied(self, test_db, vector_stubs):
        """MCP 无身份解封 → 403 回执（TDD 要求 2：MCP 臂真链路）。"""
        from lantai.cli.mcp import handle_memory_unarchive

        _seed_memory(test_db, "mem-bob-mcp", "bob", status="archived")

        out = handle_memory_unarchive({"memory_id": "mem-bob-mcp"})

        assert out.get("ok") is False
        assert out.get("status_code") == 403

    def test_mcp_dossier_focus_no_identity_denied(self, test_db):
        """MCP 无身份聚焦他人卷宗 → 服务层拒绝（ValueError: not found or access denied）。"""
        from lantai.cli.mcp import handle_dossier_focus

        test_db.add(Dossier(id="dos-bob-mcp", user_id="bob", title="bob 的卷宗"))
        test_db.commit()

        with pytest.raises(ValueError, match="not found or access denied"):
            handle_dossier_focus({"dossier_id": "dos-bob-mcp"})
