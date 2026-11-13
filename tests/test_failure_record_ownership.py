"""票 61：FailureRecord 表无归属列——三读面全库裸拉，他人教训物化为本人 Belief。

`.scratch/v0.68-next-window-2026-11/issues/61-failurerecord-no-ownership-columns.md`

**根因（架构缺口）**：`FailureRecord` 表没有 tenant_id/user_id 归属列——
失败记录从出生就无法归属，任何读面想过滤都无从过滤。三个下游读面全库裸拉：

1. 润物摘要 `runwu.py:140`：`select(FailureRecord)` 无过滤——A 的失败教训
   进 B 的认知摘要（rules 轨票 57 已收敛，failures 轨本票收口）；
2. 反省 `reflection.py:146`：全库失败蒸馏 + `_stamp_owner` 把**他人教训
   蒸馏出的 Belief 盖上调用者属主**——A 的教训以 B 的名义落库、被 B 召回；
3. 心斋切面 `context.py:723`：失败切面无身份过滤、无排序、`top_k` 负值
   反向切片（把最旧而非最新失败喂进 prompt）。

**修法**（票面修复路径）：
- 根修：FailureRecord 加 `tenant_id`/`user_id` 归属列（迁移 v43，nullable
  保存量兼容）；唯一产生点 `reflector.record_feedback` 出生即回填归属。
- 三读面按 `viewer_of` 收敛（None→"default"，NULL 老行 `OR IS NULL` 人人
  可读——同票 57 家族口径）；反省蒸馏来源限定「本人 + ownerless 系统态」；
  `_stamp_owner` 只盖过滤后来源；切面排序 + top_k 钳制（票 66 口径：
  MCP 面已钳 [1,50]，引擎面负值显式拒绝——拒绝而非静默修正）。
- MCP 入口透传身份：`handle_search` 的认知摘要注入位点此前不传身份，
  rules/failures 双裸——与本票摘要臂同收。

禁 mock 被测链路：真 SQLite 内存库直调三引擎；迁移臂真建老库跑真迁移链。
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.models.tables import FailureRecord, MemoryItem


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
    db_module.get_session = original


def _seed_failure(
    s: Session, fid: str, lesson: str, *, user_id=None, tenant_id=None
) -> FailureRecord:
    """种一条失败记录；票 61 后归属列出生即填，红测期（无列）只落行不归属。

    红态（表无归属列）与绿态（有列）都**必须**让行落库——红态先 commit
    落行再判列；归属补齐用裸 SQL UPDATE（列存在才执行）。禁 rollback：
    rollback 会把 add 的行一并吞掉，红测假绿（库空→摘要空→断言空过）。
    """
    f = FailureRecord(
        id=fid,
        task=f"task-{fid}",
        action="recall_memory",
        expected="ok",
        actual=f"actual-{fid}",
        cause=f"cause-{fid}",
        lesson=lesson,
        severity=0.9,
        source_ids=[],
    )
    s.add(f)
    s.commit()  # 先落行（红绿两态行都在）
    # 归属补齐走独立 engine 连接——StaticPool 下 session.connection() 在
    # commit 后已关闭复用会 ResourceClosedError；且裸 UPDATE 用独立连接
    # 避免污染被测 session 的事务状态
    eng = s.get_bind()
    with eng.connect() as upd:
        cols = [
            row[1] for row in upd.exec_driver_sql("PRAGMA table_info(failurerecord)").fetchall()
        ]
        if "user_id" in cols:
            upd.exec_driver_sql(
                "UPDATE failurerecord SET user_id=?, tenant_id=? WHERE id=?",
                (user_id, tenant_id, fid),
            )
            upd.commit()
    # 让被测 session 重新读到最新行（expire 缓存）
    s.expire_all()
    return f


class TestSummaryFaceOwnership:
    """臂 1：认知摘要面——B 的摘要不含 A 的教训；MCP 无身份收敛 default。"""

    def test_b_summary_excludes_a_failures(self, test_db):
        """修复前：runwu.py:140 全库裸拉，B 的摘要含 A 的教训（红）。"""
        from lantai.runtime.runwu import RunwuEngine

        _seed_failure(test_db, "fail-A1", "A 的教训：不要在生产环境直接改配置", user_id="user-A")
        _seed_failure(test_db, "fail-B1", "B 的教训：部署前必须跑迁移链", user_id="user-B")
        test_db.commit()

        engine = RunwuEngine(db=test_db)
        summary_b = engine.extract_summary(
            task="准备部署并修改配置", user_id="user-B", max_failures=5
        )
        joined = "; ".join(summary_b.failures)
        assert "A 的教训" not in joined, f"B 的摘要泄入 A 的教训：{summary_b.failures}"
        assert "B 的教训" in joined, "本人教训仍须可见（回归臂）"

        # A 对称：摘要只含自己的教训
        summary_a = engine.extract_summary(
            task="准备部署并修改配置", user_id="user-A", max_failures=5
        )
        joined_a = "; ".join(summary_a.failures)
        assert "B 的教训" not in joined_a
        assert "A 的教训" in joined_a

    def test_tenant_narrowing_keeps_null_tenant_rows(self, test_db):
        """自审臂（Standards 轴覆盖缺口）：tenant 收窄不排 NULL——ownerless
        旧行（tenant_id IS NULL）不因租户条件丢失；他租户行被排除。"""
        from lantai.runtime.runwu import RunwuEngine

        _seed_failure(
            test_db, "fail-T1", "本租户的教训：配置先校验", user_id="user-B", tenant_id="t1"
        )
        _seed_failure(
            test_db, "fail-T2", "他租户的教训：跨租户行", user_id="user-B", tenant_id="t2"
        )
        _seed_failure(test_db, "fail-T3", "ownerless 老行教训：NULL 租户", user_id="user-B")
        test_db.commit()

        engine = RunwuEngine(db=test_db)
        summary = engine.extract_summary(
            task="配置校验", user_id="user-B", tenant_id="t1", max_failures=5
        )
        joined = "; ".join(summary.failures)
        assert "本租户的教训" in joined, "同租户行必须可见"
        assert "ownerless 老行教训" in joined, "NULL 租户旧行不因租户条件被排除"
        assert "他租户的教训" not in joined, f"他租户行被排除：{joined}"

    def test_mcp_no_identity_converges_default(self, test_db):
        """MCP 无身份入口收敛 default：default 只见自己的 + ownerless 老行。"""
        from lantai.runtime.runwu import RunwuEngine

        _seed_failure(test_db, "fail-D1", "default 的教训：日志不要带正文", user_id="default")
        _seed_failure(test_db, "fail-A2", "A 的教训：默认桶不是兜底", user_id="user-A")
        test_db.commit()

        engine = RunwuEngine(db=test_db)
        # user_id=None（MCP 无身份）→ runwu 内部收敛 "default"
        summary = engine.extract_summary(task="检查日志", user_id=None, max_failures=5)
        joined = "; ".join(summary.failures)
        assert "A 的教训" not in joined, f"无身份摘要泄入具名用户教训：{summary.failures}"
        assert "default 的教训" in joined

    def test_search_summary_injection_passes_identity(self, test_db):
        """MCP handle_search 的认知摘要注入位点透传身份（修复前不传=全库）。"""
        from lantai.cli.mcp import handle_search

        _seed_failure(test_db, "fail-A3", "A 的教训：摘要注入面泄漏", user_id="user-A")
        test_db.commit()

        ret = handle_search(
            {"query": "部署检查", "task": "部署检查", "user_id": "user-B", "top_k": 1}
        )
        cs = ret.get("cognitive_summary") or ""
        assert "A 的教训" not in cs, f"handle_search 摘要注入泄入他人教训：{cs}"


class TestReflectionFaceOwnership:
    """臂 2：反省面——B reflect 的 Belief 来源不含 A 的失败；A 本人仍含。"""

    def test_b_reflect_does_not_ingest_a_failures(self, test_db):
        """修复前：reflection.py:146 全库蒸馏 + _stamp_owner 盖 B 属主（红）。

        两条同签名失败才聚得出 pattern（detect_patterns len<2 跳过——
        单条失败蒸馏不出 Belief，红臂会空过假绿）。"""
        from lantai.cognition.reflection import ReflectionEngine

        a_lesson = "教训：A 的私有部署流程必须先冻结快照"
        _seed_failure(test_db, "fail-A4a", a_lesson, user_id="user-A")
        _seed_failure(test_db, "fail-A4b", a_lesson, user_id="user-A")
        test_db.commit()

        # B 跑反省（带 B 身份）
        engine_b = ReflectionEngine(test_db, principal=_principal("user-B"))
        report_b = engine_b.run_reflection()

        # 票 61 修复后行为锁：B 视角的失败计数为 0（A 的失败被 scope 过滤，
        # B 的旧 ownerless 行不存在）——蒸馏源空，patterns 自然 0；
        # 同时 failures_scoped 如实为 True
        assert report_b.failures == 0, (
            f"B 的反省仍拉到 {report_b.failures} 条失败记录（应为 0：A 的失败被过滤）"
        )
        assert report_b.failures_scoped is True

        # B 产出的 failure belief 候选不得源自 A 的失败记录
        for b in report_b.failure_belief_candidates:
            sources = getattr(b, "source_ids", None) or []
            assert "fail-A4a" not in (sources or []) and "fail-A4b" not in (sources or []), (
                f"B 的 Belief 直接引用了 A 的失败记录：{b.content}"
            )
            assert "A 的私有部署流程" not in (b.content or ""), (
                f"A 的教训被蒸馏进 B 的 Belief：{b.content}"
            )

        # 全库扫一遍：任何 B 属主的新候选不得含 A 教训文本
        rows = test_db.exec(select(MemoryItem).where(MemoryItem.user_id == "user-B")).all()
        for r in rows:
            assert "A 的私有部署流程" not in (r.content or ""), (
                f"A 的教训物化为 B 的行 {r.id}：{r.content}"
            )

    def test_a_reflect_still_sees_own_failures(self, test_db):
        """回归臂：A 本人 reflect 仍含自己的教训（修复不得修废主路径）。

        两条同签名失败聚出 pattern（同 B 臂防假绿前置）。"""
        from lantai.cognition.reflection import ReflectionEngine

        a_lesson = "教训：A 的发布闸门必须先跑 lint"
        _seed_failure(test_db, "fail-A5a", a_lesson, user_id="user-A")
        _seed_failure(test_db, "fail-A5b", a_lesson, user_id="user-A")
        test_db.commit()

        engine_a = ReflectionEngine(test_db, principal=_principal("user-A"))
        report_a = engine_a.run_reflection()
        assert report_a.failures >= 2, "本人失败记录计数不得为 0（回归臂）"
        assert report_a.failures_scoped is True, "过滤后 failures_scoped 必须如实为 True"
        # detect_patterns 词袋清洗 lowercase——内容比对大小写不敏感
        contents = [(b.content or "").lower() for b in report_a.failure_belief_candidates]
        assert any("a 的发布闸门" in c for c in contents), (
            f"本人教训应可蒸馏进本人 Belief：{contents}"
        )


class TestContextFaceOwnershipAndClamp:
    """臂 3：心斋切面——身份过滤 + 排序 + top_k 钳制（票 66 口径）。"""

    def test_failures_section_identity_filtered(self, test_db):
        """切面失败区只含本人 + ownerless 老行（修复前全库裸拉）。"""
        from lantai.cognition.context import CognitiveContextBuilder

        _seed_failure(test_db, "fail-A6", "A 的切面教训", user_id="user-A")
        _seed_failure(test_db, "fail-B6", "B 的切面教训", user_id="user-B")
        _seed_failure(test_db, "fail-N6", "老行切面教训", user_id=None)
        test_db.commit()

        builder = CognitiveContextBuilder(test_db, principal=_principal("user-B"))
        ctx = builder.build(task="部署", top_k=10)
        ids = [f["id"] for f in ctx.failures]
        assert "fail-A6" not in ids, f"切面泄入 A 的失败：{ids}"
        assert "fail-B6" in ids
        assert "fail-N6" in ids, "NULL 老行人人可读（票 57 家族口径）"

    def test_top_k_negative_rejected_not_reversed(self, test_db):
        """top_k=-2 必须显式拒绝（票 66 口径：拒绝而非静默修正）——
        修复前 `failures[:top_k]` 负值反向切片把最旧失败喂进 prompt（红）。"""
        from lantai.cognition.context import CognitiveContextBuilder

        for i in range(4):
            _seed_failure(test_db, f"fail-C{i}", f"切面教训 {i}", user_id="user-B")
        test_db.commit()

        builder = CognitiveContextBuilder(test_db, principal=_principal("user-B"))
        with pytest.raises(ValueError):
            builder.build(task="部署", top_k=-2)

    def test_top_k_positive_takes_newest(self, test_db):
        """top_k 正值取**最新** k 条（修复前无排序=插入序不定，红）。"""
        from lantai.cognition.context import CognitiveContextBuilder
        from lantai.core.time import utcnow

        for i in range(4):
            _seed_failure(test_db, f"fail-K{i}", f"新序教训 {i}", user_id="user-B")
            # 手动拉开 created_at 保证排序断言确定
            test_db.exec(
                __import__("sqlalchemy")
                .text("UPDATE failurerecord SET created_at=:t WHERE id=:i")
                .bindparams(t=utcnow().replace(microsecond=i * 1000), i=f"fail-K{i}")
            )
        test_db.commit()

        builder = CognitiveContextBuilder(test_db, principal=_principal("user-B"))
        ctx = builder.build(task="部署", top_k=2)
        ids = [f["id"] for f in ctx.failures]
        assert len(ids) <= 2, f"top_k=2 溢出：{ids}"
        # 最新的两条（K2/K3 拥有最大 created_at）
        assert "fail-K3" in ids and "fail-K2" in ids, f"应取最新 k 条：{ids}"


class TestMigrationAndLegacy:
    """臂 4：迁移 + 存量兼容——旧行（NULL 归属）按回填口径可读不炸。"""

    def test_v43_migration_adds_ownership_columns(self, tmp_path):
        """v42 老库 → v43：failurerecord 补 tenant_id/user_id 列，存量行保持。"""
        from lantai.storage.migrations import TARGET_SCHEMA_VERSION, apply_migrations

        path = tmp_path / "v42.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE failurerecord (
                id TEXT PRIMARY KEY, task TEXT, action TEXT, expected TEXT,
                actual TEXT, cause TEXT, lesson TEXT, severity REAL,
                recurrence_count INTEGER, source_ids TEXT, created_at DATETIME
            );
            """
        )
        conn.execute(
            "INSERT INTO failurerecord (id, task, action, expected, actual,"
            " cause, lesson, severity, recurrence_count, source_ids, created_at)"
            " VALUES ('f-old', '老任务', 'act', 'exp', 'act-res', 'cause', '老教训',"
            " 0.5, 1, '[]', '2026-01-01')"
        )
        conn.execute("PRAGMA user_version = 42")
        conn.commit()

        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        cols = [row[1] for row in conn.execute("PRAGMA table_info(failurerecord)").fetchall()]
        assert "tenant_id" in cols and "user_id" in cols
        # 存量行数据不丢
        row = conn.execute(
            "SELECT id, task, lesson, user_id, tenant_id FROM failurerecord WHERE id='f-old'"
        ).fetchone()
        assert row[0] == "f-old" and row[2] == "老教训"
        # 旧行归属为 NULL（回填口径：ownerless 保系统态——ready-for-human 决策点）
        assert row[3] is None and row[4] is None
        # 幂等
        apply_migrations(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TARGET_SCHEMA_VERSION
        conn.close()

    def test_legacy_null_rows_readable_after_migration(self, test_db):
        """存量兼容臂：NULL 归属旧行人人可读、不炸（OR IS NULL 口径）。"""
        from lantai.cognition.context import CognitiveContextBuilder
        from lantai.runtime.runwu import RunwuEngine

        _seed_failure(test_db, "fail-OLD", "老行教训：迁移前写入", user_id=None)
        test_db.commit()

        engine = RunwuEngine(db=test_db)
        summary = engine.extract_summary(task="老任务", user_id="user-B", max_failures=5)
        assert "老行教训" in "; ".join(summary.failures)

        builder = CognitiveContextBuilder(test_db, principal=_principal("user-B"))
        ctx = builder.build(task="老任务", top_k=5)
        assert "fail-OLD" in [f["id"] for f in ctx.failures]

    def test_write_face_stamps_ownership_at_birth(self, test_db):
        """写面：reflector.record_feedback 产生的 FailureRecord 出生即带归属。"""
        from lantai.models.tables import MemoryItem as MI

        m = MI(
            id="mem-fb61",
            memory_type="semantic",
            key="key-fb61",
            content="待反馈记忆内容",
            namespace="default",
            status="active",
            user_id="user-A",
        )
        test_db.add(m)
        test_db.commit()

        from lantai.evolution.reflector import record_feedback

        res = record_feedback(
            memory_id="mem-fb61",
            query="部署查询",
            helped=False,
            user_accepted=False,
            hallucination_risk=0.8,
            principal=_principal("user-A"),
        )
        assert res["ok"] is True
        f = test_db.exec(select(FailureRecord).where(FailureRecord.task == "部署查询")).first()
        assert f is not None, "反馈闭环应产生 FailureRecord"
        assert f.user_id == "user-A", f"出生未回填归属：user_id={f.user_id!r}"
