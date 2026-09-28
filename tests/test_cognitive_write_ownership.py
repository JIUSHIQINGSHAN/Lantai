"""认知写侧归属：`/cognitive/observe` 与 `/cognitive/reflect` 不再丢身份。

票 `.scratch/cognitive-write-gaps/01-cognitive-write-no-identity.md`

**先说影响**：`routes_cognitive_router` 在 `CORE_ROUTERS` 里
（`app.py:98`），`dependencies=AUTH` 认证了某人——**然后把身份丢掉了**。
四个 handler 里两个读的已修（票 03/14），两个写/触发的一个都没取：

- `POST /cognitive/observe` 落 `MemoryItem` + `Evidence` 两行
  **`user_id=NULL`**。而本仓 NULL 的口径是"未记录归属的老行，人人可见"
  （`_digest_scope` 等全部 `user_id == viewer OR IS NULL`）——于是 A 写的
  新观测对所有用户可见。`ObserveReq.content` 是自由文本，用户往里写什么
  完全不受控。
- `POST /cognitive/reflect` 的 `run_reflection` 四个查询一个 scope 都没有：
  统计是全表数；`detect_patterns` 拿 A 和 B 的观测**一起聚类**；晋升出的
  Belief/Rule 候选经 `db.add(b)` 落库又是不带 `user_id` 的 ownerless 行。

测试纪律（AGENTS.md）：核心函数不 mock 内部逻辑——真实内存 SQLite 建表、
真 `ReflectionEngine`、路由层真 `TestClient`。mock 只覆盖外部网络。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, Evidence, MemoryItem


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


A = _principal("user-A")
ADMIN = _principal("admin-1", role="admin")


def _obs(mid: str, user_id: str | None, content: str) -> MemoryItem:
    now = utcnow()
    return MemoryItem(
        id=mid,
        content=content,
        memory_type="text",
        role=CognitiveRole.OBSERVATION,
        confidence=0.5,
        user_id=user_id,
        created_at=now,
        updated_at=now,
    )


def _role(mid: str, user_id: str | None, role: CognitiveRole, content: str, conf: float):
    """任意认知角色的行（Belief/Rule 用于步骤 3/4 的衰减判定）。"""
    now = utcnow()
    return MemoryItem(
        id=mid,
        content=content,
        memory_type="text",
        role=role,
        confidence=conf,
        user_id=user_id,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def cog_env():
    """内存 SQLite 真实建表 + patch db.get_session（路由的 Depends 目标）。

    **override 的 key 必须是路由自己绑定的那个函数对象**：
    `routes_cognitive.py` 写的是 `from lantai.storage.db import get_session`，
    在本模块命名空间里留了原始函数；只 patch
    `lantai.storage.db.get_session` 动不了这个别名，而 FastAPI 的
    `dependency_overrides` 按**对象同一性**查表。第一版就是踩在这里——
    种子写不进、断言恒假（票 03 的 `app_isolated` 夹具同款教训）。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    from lantai.api import routes_cognitive as routes_cognitive_mod
    from lantai.api.app import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    def _override_session():
        yield session_factory()

    route_dep = routes_cognitive_mod.get_session
    app.dependency_overrides[route_dep] = _override_session
    try:
        with patch.object(db_module, "get_session", session_factory):
            with session_factory() as s:
                # 观测：A / B 各一条，内容互不相似（不该跨用户聚成 pattern）
                s.add(_obs("mem-A", "user-A", "A 的观测：本周要发版"))
                s.add(_obs("mem-B", "user-B", "B 的私有观测：公司融资计划有变"))
                # Belief（步骤 3 阈值 0.4）：A 的低置信会被计入 under_review，
                # B 的低置信**不该**被计入。
                s.add(_role("bel-A-low", "user-A", CognitiveRole.BELIEF, "A 的弱信念", 0.2))
                s.add(_role("bel-B-low", "user-B", CognitiveRole.BELIEF, "B 的弱信念", 0.2))
                s.add(_role("bel-A-ok", "user-A", CognitiveRole.BELIEF, "A 的强信念", 0.9))
                # Rule（步骤 4 阈值 0.5）：同理分属主。
                s.add(_role("rul-A-low", "user-A", CognitiveRole.RULE, "A 的弱规则", 0.3))
                s.add(_role("rul-B-low", "user-B", CognitiveRole.RULE, "B 的弱规则", 0.3))
                s.commit()
            yield session_factory
    finally:
        app.dependency_overrides.pop(route_dep, None)


def _client(principal) -> TestClient:
    from lantai.api.app import app
    from lantai.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: principal
    return TestClient(app)


# ── Red 1（决定性）：observe 落归属 ────────────────────────────────


class TestObserveWritesOwnership:
    def test_observe_stamps_caller_identity(self, cog_env):
        """A observe → MemoryItem 与 Evidence 都落 user-A。

        这是本票的决定性判据：修前两行都是 `user_id=None`。
        """
        with _client(A) as c:
            resp = c.post("/cognitive/observe", json={"content": "A 的新观测"})
        assert resp.status_code == 200, resp.text

        with cog_env() as s:
            rows = s.exec(select(MemoryItem).where(MemoryItem.content == "A 的新观测")).all()
            assert len(rows) == 1
            assert rows[0].user_id == "user-A", (
                f"observe 落的记忆没带身份：user_id={rows[0].user_id!r}"
            )
            evs = s.exec(select(Evidence).where(Evidence.source_memory_id == rows[0].id)).all()
            assert len(evs) == 1
            assert evs[0].user_id == "user-A", (
                f"observe 落的证据没带身份：user_id={evs[0].user_id!r}"
            )

    def test_observations_not_visible_to_other_user(self, cog_env):
        """A 的观测不进 B 的认知上下文（读侧链路票 03 已修，这里防写侧回潮）。"""
        with _client(A) as c:
            c.post("/cognitive/observe", json={"content": "A 的机密：下周并购谈判"})
        with _client(_principal("user-B")) as c:
            resp = c.get("/cognitive/context", params={"top_k": 50})
        assert resp.status_code == 200, resp.text
        facts = resp.json()["facts"]
        assert not any("下周并购谈判" in f.get("content", "") for f in facts), "A 的观测泄给了 B"


# ── Red 3/4：reflect 按归属收窄 ───────────────────────────────────


class TestReflectScopedToCaller:
    def test_reflect_counts_exclude_other_users(self, cog_env):
        """A 触发反思，衰减统计只数 A 自己的行。

        夹具给 A / B 各种了一条**低置信** Belief（阈值 0.4）与一条
        **低置信** Rule（阈值 0.5）。所以 A 的视角该是
        `principles_under_review == 1` / `rules_weakened == 1`——
        若查询去归属，两个数都会变成 2（B 的也进来了）。
        **判据必须在没有泄漏时也非平凡**，否则变异杀不掉。
        """
        with _client(A) as c:
            resp = c.post("/cognitive/reflect")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["principles_under_review"] == 1, (
            f"A 的弱信念该是 1 条，实际 {body['principles_under_review']}（B 的混进来了？）"
        )
        assert body["rules_weakened"] == 1, (
            f"A 的弱规则该是 1 条，实际 {body['rules_weakened']}（B 的混进来了？）"
        )
        assert body["new_patterns"] == 0, f"A 的观测与 B 的不该聚成 pattern：{body}"

    def test_reflect_promoted_rows_carry_identity(self, cog_env):
        """晋升落库的候选必须带 user_id（修前又是一批 ownerless 行）。

        **要让 `propose_beliefs` 真的产出候选，光有两条重复观测不够**：
        `detect_patterns` 把 `pat.source_ids` 取自**观测行自己的 `source_ids`
        列**（`evolution.py:102`），而 `propose_beliefs` 只在 `pat.source_ids`
        非空时才查 Evidence（`evolution.py:144`）。所以这条链路必须按真实
        口径接起来：`MemoryItem.source_ids` 里放**记忆 id**（Evidence 按
        `source_memory_id` 反查）。第一版把 Evidence 的 id 放进去，命中 0 行，
        `independent_support` 恒 0，得分上限 0.595 < 阈值 0.70，候选永远是 0
        个——判据恒假，这个测试就白写了。接好后 eq=0.9 / indep=1.0 /
        rec=0.667，得分 0.755 越过 0.70。
        """
        with cog_env() as s:
            for mid, eid in (("mem-A2", "ev-A2"), ("mem-A3", "ev-A3")):
                m = _obs(mid, "user-A", "发布前必须跑全量测试")
                m.source_ids = ["mem-A2", "mem-A3"]
                s.add(m)
                s.add(
                    Evidence(
                        id=eid,
                        user_id="user-A",
                        evidence_type="observation",
                        source_memory_id=mid,
                        content="发布前必须跑全量测试",
                        reliability=0.9,
                        independence=1.0,
                        created_at=utcnow(),
                    )
                )
            s.commit()
        with _client(A) as c:
            resp = c.post("/cognitive/reflect")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["new_patterns"] == 1, f"两条重复观测该产出 1 个 pattern：{body}"
        assert body["belief_candidates"] >= 1, (
            f"pattern 该晋升出 Belief 候选（得分可能没过 0.70 阈值）：{body}"
        )

        with cog_env() as s:
            promoted = s.exec(
                select(MemoryItem).where(MemoryItem.user_id.is_(None))  # noqa: E712
            ).all()
            # 夹具里预置的行都有属主；reflect 不该新增 ownerless 行
            assert promoted == [], (
                f"reflect 落出了 {len(promoted)} 行无属主记忆：{[r.id for r in promoted]}"
            )
            bel = s.exec(select(MemoryItem).where(MemoryItem.role == CognitiveRole.BELIEF)).all()
            a_promoted = [b for b in bel if b.user_id == "user-A" and b.status == "candidate"]
            assert len(a_promoted) >= 1, "晋升出的 Belief 该带 user-A"

    def test_reflect_pattern_input_excludes_other_users(self, cog_env):
        """直接调 `run_reflection`（不打 HTTP）：B 的观测不参与 A 的聚类。

        用 spy 观察 `detect_patterns` 实际收到的观测集——比只断言输出
        数字更强（票 16 的教训：输出对了不代表输入对了）。夹具另有 A / B
        各一条低置信 Belief，所以 detect_patterns 也会被 failure 路径调到，
        `seen` 应有多次调用，逐次核对。
        """
        from lantai.cognition.evolution import EvolutionEngine
        from lantai.cognition.reflection import ReflectionEngine

        real_detect = EvolutionEngine.detect_patterns
        seen: list[list[str]] = []

        def spy(self, observations):
            seen.append([o.id for o in observations])
            return real_detect(self, observations)

        with cog_env() as s:
            with patch.object(EvolutionEngine, "detect_patterns", spy):
                ReflectionEngine(s, principal=A).run_reflection()

        assert seen, "detect_patterns 从未被调用"
        for ids in seen:
            assert "mem-B" not in ids, f"B 的观测进了 A 的聚类输入：{ids}"
            assert "bel-B-low" not in ids, f"B 的信念进了 A 的聚类输入：{ids}"


# ── Red 5：口径边界 ───────────────────────────────────────────────


class TestScopeBoundaries:
    def test_internal_call_principal_none_is_unfiltered(self, cog_env):
        """`principal=None`（内部 worker / 脚本）不过滤，口径同前 17 票。

        **判据要非平凡**：admin 视角数到的弱信念/弱规则各是 2（A+B），
        `None` 必须与 admin 同数——只断言 `report is not None` 杀不掉
        "scope 对 None 也过滤" 的变异。
        """
        from lantai.cognition.reflection import ReflectionEngine

        with cog_env() as s:
            report = ReflectionEngine(s, principal=None).run_reflection()
        assert report.principles_under_review == 2, (
            f"None 该看见全部 2 条弱信念，实际 {report.principles_under_review}"
        )
        assert report.rules_weakened == 2, (
            f"None 该看见全部 2 条弱规则，实际 {report.rules_weakened}"
        )

    def test_admin_sees_all(self, cog_env):
        """admin 全量（不能把运维修废）。"""
        from lantai.cognition.reflection import ReflectionEngine

        with cog_env() as s:
            report = ReflectionEngine(s, principal=ADMIN).run_reflection()
        assert report.principles_under_review == 2, (
            f"admin 该看见全部 2 条弱信念，实际 {report.principles_under_review}"
        )
        assert report.rules_weakened == 2

    def test_null_owner_rows_still_counted(self, cog_env):
        """NULL 属主老行仍计入——单人部署下这是大量真实数据。

        没有这条，"按归属收窄"会被实现成 `user_id == viewer`，
        单人部署的反思于是空转。判据：加一条 NULL 属主的弱信念后，
        A 视角的 `principles_under_review` 从 1 变 2。
        """
        from lantai.cognition.reflection import ReflectionEngine

        with cog_env() as s:
            s.add(_role("bel-null", None, CognitiveRole.BELIEF, "没有属主的老弱信念", 0.2))
            s.commit()
        with cog_env() as s:
            report = ReflectionEngine(s, principal=A).run_reflection()
        assert report.principles_under_review == 2, (
            f"NULL 老行被藏了（该是 A 自己 1 + NULL 1 = 2），实际 {report.principles_under_review}"
        )

    def test_dev_mode_fallback_principal_still_scoped(self, cog_env):
        """DEV MODE 回落到 `Principal(user_id="default")`——不是 None。

        票 05 的教训：把 `None` 与"无凭证回落"混为一谈会让收窄失效。
        判据：`"default"` 是**真实身份**，与 `user-A` / `user-B` 都不同，
        所以它既看不到 A 的弱信念也看不到 B 的——只看到 NULL 老行。
        夹具此时没有 NULL 弱信念，故 `principles_under_review == 0`；
        若实现误把 `"default"` 当 None（全量），这个数会是 2。
        """
        from lantai.cognition.reflection import ReflectionEngine

        dev = _principal("default")
        with cog_env() as s:
            report = ReflectionEngine(s, principal=dev).run_reflection()
        assert report.principles_under_review == 0, (
            f"DEV MODE 的 'default' 被当成全量视角了（该是 0），"
            f"实际 {report.principles_under_review}"
        )
        assert report.rules_weakened == 0


# ── 回归护栏 ───────────────────────────────────────────────────────


class TestNoRegressionOnReadSide:
    def test_read_handlers_still_carry_principal(self, cog_env):
        """读侧三个 handler 已修（票 03/14），本票不得回潮。"""
        import inspect

        import lantai.api.routes_cognitive as rc

        for name in ("cognitive_context", "cognitive_context_prompt", "cognitive_summary"):
            params = inspect.signature(getattr(rc, name)).parameters
            assert "ctx" in params, f"{name} 丢了 ctx 形参（读侧归属回潮）"

    def test_failures_count_is_flagged_unscoped(self, cog_env):
        """`FailureRecord` 无归属列，`failures` 计数必须标注为未收窄。

        谎报 `failures_scoped: true` 会让读报告的人把跨用户的失败数
        当成自己的——比不报更糟。
        """
        from lantai.cognition.reflection import ReflectionEngine

        with cog_env() as s:
            report = ReflectionEngine(s, principal=A).run_reflection()
        assert report.failures_scoped is False, (
            "failures_scoped 该是 False（FailureRecord 没有归属列，无从收窄）"
        )
        with _client(A) as c:
            resp = c.post("/cognitive/reflect")
        assert resp.status_code == 200, resp.text
        assert resp.json()["failures_scoped"] is False, "路由没把 failures_scoped 传出去"
