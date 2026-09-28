"""票 06：MCP 五个工具在无身份（`principal=None`）时不再敞开。

`.scratch/mcp-identity-gaps/06-mcp-five-tools-none-unfiltered.md`

**先说影响**：宿主不透传 `user_id` 时，`_principal_from_params` 返回 None，
而 5 个 scope 函数把 None 当成"内部 worker 要全表"。实测后果：

| 工具 | 无身份时 |
|---|---|
| `kaogong_eval` | 改写全库每条记忆的 `tier`/`importance`（探针实测 0.9→0.1） |
| `reflect_run` | 全库候选集连同正文进 curator prompt，外发外部 LLM |
| `crystals_list` | 列出别人的技能结晶 |
| `persona_get` | 拿到别人的人格基座（含认知底色 E 层） |
| `memory_consolidate` | mode 默认 off 暂不可利用，开了 shadow/enforce 即复活 |

**修法**（按宿主调用方 grep 实证，不信 docstring）：
- `_kaogong_scope` / `_crystal_scope` / `_persona_scope`：None 收敛到
  `"default"`（同票 05 口径）——这三个宿主**没有任何 worker/scheduler
  调用方**，docstring 里"定时任务保持全表"的场景不存在。
- `_reflect_scope`：None 同样收敛，但 worker/scheduler 确有调用方，
  故 worker 侧改传显式系统身份 `__system__`，否则定时反思会空转。
- 写路径（kaogong）额外加"无身份不动别人的行"的硬闸。

**NULL 老行口径不变**：`OR IS NULL` 全部保留——真实库 657 行 memoryitem
里 636 行 NULL、5 行 skillcrystal 全 NULL、persona 那一行也 NULL，
判不可见会让单人部署整体空转。NULL 是「未记录」不是「属于所有人」。
"""

from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem, PersonaProfile, SkillCrystal


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def engine():
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
    yield eng
    # **必须还原成原来的可调用对象**：赋成 `Session` 类本身会让后续测试
    # 拿到"没有绑定引擎的 Session 类"，全部报错（本轮踩过）。
    db_module.get_session = original


def _seed_memory(
    s: Session,
    mid: str,
    owner: str | None,
    *,
    use_count: int = 5,
    expired: bool = False,
) -> None:
    """造一条记忆。

    `use_count=5, helpful_count=0` → 考功下考降权到 0.1（探针实测的改动前行为）；
    `expired=True` → `valid_to` 在过去 → 反思 R2「过期」候选（该规则默认开，
    而 R4/R5 受 `REFLECT_STALE_SCAN_ENABLED=False` 关闭，别用它造候选）。
    """
    now = utcnow()
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"key-{mid}",
            content=f"{mid} 的正文",
            namespace="default",
            status="active",
            tier="working",
            importance=0.9,
            confidence=1.0,
            reason="",
            role="OBSERVATION",
            lane="general",
            domain="general",
            version=1,
            use_count=use_count,
            helpful_count=0,
            decay_score=1.0,
            decay_class="slow",
            event_time_precision="none",
            lifecycle_status="ACTIVE",
            user_id=owner,
            valid_to=(now - timedelta(days=1)) if expired else None,
            created_at=now,
            updated_at=now,
        )
    )
    s.commit()


# ---------------------------------------------------------------- 考功（写）


class TestKaogongNoneNoLongerWritesOthers:
    """`run_kaogong_cycle(principal=None)` 不再改写别人的记忆。"""

    def test_none_principal_does_not_demote_other_owner(self, engine):
        """无身份时 B 的高频低效记忆保持 0.9（探针实测改动前会被改成 0.1）。"""
        from lantai.services.kaogong_service import run_kaogong_cycle

        with Session(engine) as s:
            _seed_memory(s, "mem-B", "user-B")

        report = run_kaogong_cycle(principal=None)

        with Session(engine) as s:
            mem = s.get(MemoryItem, "mem-B")
            assert mem.importance == 0.9, "无身份考功改写了别人记忆的 importance"
        assert report["demoted"] == 0

    def test_none_principal_still_evaluates_null_owner(self, engine):
        """NULL 属主老行仍参与（单人部署不能空转）。"""
        from lantai.services.kaogong_service import run_kaogong_cycle

        with Session(engine) as s:
            _seed_memory(s, "mem-legacy", None)

        report = run_kaogong_cycle(principal=None)

        with Session(engine) as s:
            assert s.get(MemoryItem, "mem-legacy").importance == 0.1
        assert report["demoted"] == 1

    def test_explicit_owner_still_demotes_own(self, engine):
        """显式身份仍能考功自己的记忆（回归：收窄不能把正常功能修废）。"""
        from lantai.services.kaogong_service import run_kaogong_cycle

        with Session(engine) as s:
            _seed_memory(s, "mem-A", "user-A")
            _seed_memory(s, "mem-B", "user-B")

        report = run_kaogong_cycle(principal=_principal("user-A"))

        with Session(engine) as s:
            assert s.get(MemoryItem, "mem-A").importance == 0.1
            assert s.get(MemoryItem, "mem-B").importance == 0.9
        assert report["demoted"] == 1

    def test_system_viewer_still_evaluates_everyone(self, engine):
        """显式系统身份仍全表——定时考功不能被收窄成空转（变异 M2 的守门测试）。

        与 `test_none_principal_does_not_demote_other_owner` 配对：
        全表口径没有消失，只是从"歧义的 None"搬到"显式的 SYSTEM_VIEWER"。
        没有这条，把 SYSTEM_VIEWER 分支删掉也不会有任何测试报警。
        """
        from lantai.services.kaogong_service import run_kaogong_cycle

        with Session(engine) as s:
            _seed_memory(s, "mem-A", "user-A")
            _seed_memory(s, "mem-B", "user-B")

        report = run_kaogong_cycle(principal=_principal("__system__"))

        with Session(engine) as s:
            assert s.get(MemoryItem, "mem-A").importance == 0.1
            assert s.get(MemoryItem, "mem-B").importance == 0.1
        assert report["demoted"] == 2


# ---------------------------------------------------------------- 反思（写+外发）


class TestReflectNoneNoLongerScansOthers:
    """`health_scan(principal=None)` 不再把别人的记忆正文收进候选集。"""

    def test_none_principal_excludes_other_owner(self, engine):
        """无身份时 B 的 superseded 记忆不进候选集（探针实测改动前进，且带正文）。"""
        from lantai.evolution.reflector import health_scan

        with Session(engine) as s:
            _seed_memory(s, "mem-B", "user-B")
            _seed_memory(s, "mem-legacy", None)
            # mem-B 被 mem-legacy 取代 → 成为 superseded 残留候选（R1 规则默认开）
            _seed_edge(s, "mem-legacy", "mem-B")
            s.commit()

        res = health_scan(s, principal=None)

        ids = [c.get("memory_id") or c.get("id") for c in res["candidates"]]
        assert "mem-B" not in ids, "无身份反思扫到了别人的记忆"
        assert res["snapshot"]["batch_total"] == 0

    def test_none_principal_still_scans_null_owner(self, engine):
        """NULL 属主老行仍进候选集。"""
        from lantai.evolution.reflector import health_scan

        with Session(engine) as s:
            _seed_memory(s, "mem-legacy", None, expired=True)
            _seed_memory(s, "mem-B", "user-B", expired=True)

        res = health_scan(s, principal=None)

        ids = [c.get("memory_id") or c.get("id") for c in res["candidates"]]
        assert "mem-legacy" in ids

    def test_system_principal_sees_everything(self, engine):
        """显式系统身份（worker/scheduler 专用）仍全表——定时反思不能空转。"""
        from lantai.evolution.reflector import health_scan

        with Session(engine) as s:
            _seed_memory(s, "mem-B", "user-B", expired=True)
            _seed_memory(s, "mem-A", "user-A", expired=True)

        res = health_scan(s, principal=_principal("__system__"))

        ids = [c.get("memory_id") or c.get("id") for c in res["candidates"]]
        assert "mem-B" in ids, "系统身份下定时反思不应被收窄"


def _seed_edge(s: Session, source_id: str, target_id: str) -> None:
    """造一条 supersedes 边：target 成为「superseded 残留」反思候选。"""
    now = utcnow()
    s.add(
        MemoryEdge(
            id=f"edge-{source_id}-{target_id}",
            source_memory_id=source_id,
            target_memory_id=target_id,
            relation="supersedes",
            confidence=1.0,
            reason="",
            created_at=now,
        )
    )


# ---------------------------------------------------------------- 结晶（读）


class TestCrystalsNoneNoLongerListsOthers:
    """`list_crystals(principal=None)` 不再列出别人的结晶。"""

    def _seed(self, s: Session, cid: str, owner: str | None) -> None:
        now = utcnow()
        s.add(
            SkillCrystal(
                id=cid,
                skill_name=f"{cid} 技能",
                trigger_rule="触发",
                procedure="流程",
                status="candidate",
                hit_count=0,
                candidate_count=0,
                decision_reason="",
                user_id=owner,
                created_at=now,
                updated_at=now,
            )
        )

    def test_none_principal_excludes_other_owner(self, engine):
        """无身份时拿不到 B 的结晶（探针实测改动前返回 cry-B）。"""
        from lantai.services.crystal_service import list_crystals

        with Session(engine) as s:
            self._seed(s, "cry-B", "user-B")
            self._seed(s, "cry-legacy", None)
            s.commit()

        res = list_crystals(status="candidate", principal=None)
        ids = [c["id"] for c in res["crystals"]]

        assert "cry-B" not in ids
        assert "cry-legacy" in ids, "NULL 老行必须仍可见"

    def test_explicit_owner_sees_own(self, engine):
        """显式身份能看自己的 + NULL 老行。"""
        from lantai.services.crystal_service import list_crystals

        with Session(engine) as s:
            self._seed(s, "cry-A", "user-A")
            self._seed(s, "cry-B", "user-B")
            s.commit()

        res = list_crystals(status="candidate", principal=_principal("user-A"))
        ids = [c["id"] for c in res["crystals"]]

        assert "cry-A" in ids
        assert "cry-B" not in ids

    def test_system_viewer_still_lists_everyone(self, engine):
        """显式系统身份仍全表（变异 M2 类守门：删掉 SYSTEM_VIEWER 分支须报警）。"""
        from lantai.services.crystal_service import list_crystals

        with Session(engine) as s:
            self._seed(s, "cry-A", "user-A")
            self._seed(s, "cry-B", "user-B")
            s.commit()

        res = list_crystals(status="candidate", principal=_principal("__system__"))
        ids = [c["id"] for c in res["crystals"]]

        assert {"cry-A", "cry-B"} <= set(ids)


# ---------------------------------------------------------------- 人格（读）


class TestPersonaNoneNoLongerGetsOthers:
    """`get_active_persona(principal=None)` 不再拿到别人的人格基座。"""

    def _seed(self, s: Session, pid: str, owner: str | None) -> None:
        now = utcnow()
        s.add(
            PersonaProfile(
                id=pid,
                name=f"{pid} 人格",
                linguistic_style="语气",
                guidelines="准则",
                epistemic_facts="认知底色",
                is_active=True,
                user_id=owner,
                created_at=now,
                updated_at=now,
            )
        )

    def test_none_principal_excludes_other_owner(self, engine):
        """无身份时拿不到 B 的激活人格（探针实测改动前连 E 层一起返回）。"""
        from lantai.services.persona_service import get_active_persona

        with Session(engine) as s:
            self._seed(s, "per-B", "user-B")
            s.commit()

        with Session(engine) as s:
            p = get_active_persona(s, principal=None)

        assert p.id != "per-B", "无身份拿到了别人的人格基座"

    def test_none_principal_still_sees_null_owner(self, engine):
        """NULL 属主的人格仍可见——真实库唯一一行 persona 就是 NULL。"""
        from lantai.services.persona_service import get_active_persona

        with Session(engine) as s:
            self._seed(s, "per-legacy", None)
            s.commit()

        with Session(engine) as s:
            p = get_active_persona(s, principal=None)

        assert p.id == "per-legacy"

    def test_explicit_owner_sees_own(self, engine):
        """显式身份能看自己激活的人格。"""
        from lantai.services.persona_service import get_active_persona

        with Session(engine) as s:
            self._seed(s, "per-A", "user-A")
            s.commit()

        with Session(engine) as s:
            p = get_active_persona(s, principal=_principal("user-A"))

        assert p.id == "per-A"

    def test_system_viewer_still_sees_everyone(self, engine):
        """显式系统身份仍全表（变异 M2 类守门）。"""
        from lantai.services.persona_service import list_personas

        with Session(engine) as s:
            self._seed(s, "per-A", "user-A")
            self._seed(s, "per-B", "user-B")
            s.commit()

        with Session(engine) as s:
            ids = sorted(p.id for p in list_personas(s, principal=_principal("__system__")))

        assert {"per-A", "per-B"} <= set(ids)
