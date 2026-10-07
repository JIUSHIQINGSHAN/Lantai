"""勘合——潜移异步摄取幂等回执（票据 .scratch/kanhe-idempotency/01）。

不 mock 冒烟：真 SQLite（内存引擎）、真线程池 worker、真 ingest_dialogue。
唯一 mock 面是「幂等层 DB 故障」（fail-open 路径，外部依赖异常注入，允许）。

核心不变量：
- 同一 (key,user,tenant)+同指纹在租约窗内至多执行一次；
- 任务失败后同键必须可重试（失败不得以 accepted/done 重放）；
- 回执表里永不出现正文。
"""

import json
import time
from contextlib import contextmanager

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.core.idempotency import (
    LEASE_SECONDS,
    TTL_SECONDS,
    auto_key,
    claim,
    fingerprint_payload,
    purge_expired,
    release,
    settle,
)


@pytest.fixture()
def mem_env(tmp_path):
    """文件 SQLite（tmp_path）+ 真实建表 + patch db.get_session。

    不用内存 StaticPool：潜移 worker 线程与测试主线程要并发开 Session，
    StaticPool 只有**一条**连接被两边共用，cursor 交错会间歇性炸 worker
    （worker 炸 → settle 释放键 → 幂等测试结果随线程时序漂移）。文件库
    每 Session 独立连接，锁交给 busy_timeout（与生产形态同源）。
    """
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    engine = create_engine(
        f"sqlite:///{tmp_path / 'kanhe_test.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    @contextmanager
    def factory():
        with Session(engine) as s:
            yield s

    real = db_module.get_session
    db_module.get_session = factory
    yield engine
    db_module.get_session = real


@pytest.fixture()
def clean_tasks():
    """潜移任务注册表清理（worker 线程真实跑，测试间隔离）。"""
    from lantai.services import async_ingest_service as ais

    ais.clear_tasks()
    yield ais
    ais.clear_tasks()


def _wait_task(task_id: str, timeout: float = 15.0) -> dict:
    """轮询到任务终结（真线程、真管线，不 mock）。"""
    from lantai.services.async_ingest_service import get_task_status

    deadline = time.time() + timeout
    while time.time() < deadline:
        st = get_task_status(task_id)
        if st.get("status") in ("completed", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内未终结")


def _row(key: str, user_id: str = "default", tenant_id: str = ""):
    import lantai.storage.db as db_module
    from lantai.models.tables import IdempotencyKey

    with db_module.get_session() as s:
        return s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.tenant_id == tenant_id,
            )
        ).first()


def _set_created(key: str, user_id: str, tenant_id: str, seconds_ago: float):
    import lantai.storage.db as db_module
    from lantai.models.tables import IdempotencyKey

    with db_module.get_session() as s:
        row = s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.tenant_id == tenant_id,
            )
        ).first()
        assert row is not None
        row.created_at = time.time() - seconds_ago
        s.add(row)
        s.commit()


# ------------------------------------------------------------------ claim 语义


class TestClaim:
    def test_claim_new_then_replay(self, mem_env):
        fp = fingerprint_payload("记住：测试", "default", "dialogue", "", None)
        first = claim("k1", "default", "", fp, task_id="task_aaaa")
        assert first["action"] == "new"
        assert isinstance(first["claimed_at"], float)
        # 回执从出生就有任务号
        assert _row("k1").response_json is not None
        assert json.loads(_row("k1").response_json)["task_id"] == "task_aaaa"

        second = claim("k1", "default", "", fp, task_id="task_bbbb")
        assert second["action"] == "replay"
        assert second["task_id"] == "task_aaaa"  # 重放原任务号，不是新号

    def test_claim_conflict_same_key_different_content(self, mem_env):
        fp1 = fingerprint_payload("内容一", "default", "dialogue", "", None)
        fp2 = fingerprint_payload("内容二", "default", "dialogue", "", None)
        assert claim("k1", "default", "", fp1, task_id="task_a")["action"] == "new"
        with pytest.raises(ValueError, match="conflict"):
            claim("k1", "default", "", fp2, task_id="task_b")

    def test_claim_scope_by_user_and_tenant(self, mem_env):
        """键的作用域是 (key, user_id, tenant_id)：A 的键与 B 互不相干。"""
        fp = fingerprint_payload("同文", "u1", "dialogue", "", None)
        assert claim("shared", "u1", "", fp, task_id="t1")["action"] == "new"
        assert claim("shared", "u2", "", fp, task_id="t2")["action"] == "new"
        assert claim("shared", "u1", "tenant-b", fp, task_id="t3")["action"] == "new"

    def test_accepted_lease_expiry_takeover(self, mem_env):
        """accepted 超租约（600s）= 原任务丢失，接管重新执行。"""
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        assert claim("k1", "default", "", fp, task_id="task_old")["action"] == "new"
        _set_created("k1", "default", "", LEASE_SECONDS + 5)
        again = claim("k1", "default", "", fp, task_id="task_new")
        assert again["action"] == "new"
        row = _row("k1")
        assert json.loads(row.response_json)["task_id"] == "task_new"

    def test_done_replay_within_ttl(self, mem_env):
        """显式键 settle 成 done 后，TTL 内同键同文重放。"""
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim("k1", "default", "", fp, task_id="task_a")
        settle(
            {
                "key": "k1",
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v["claimed_at"],
                "explicit": True,
            },
            ok=True,
            result={
                "ingested": True,
                "candidate_id": "c1",
                "status": "fastpath",
                "secret_text": "不该出现的正文",
            },
            task_id="task_a",
        )
        row = _row("k1")
        assert row.state == "done"
        receipt = json.loads(row.response_json)
        assert receipt["task_id"] == "task_a"
        assert receipt["durable"] is True
        assert receipt["candidate_id"] == "c1"
        # 回执零正文不变量：调用方 result 里的正文键不得进表——
        # 本表寿命长于记忆删除动作，正文进回执等于删除后仍可捞
        assert "secret_text" not in receipt
        assert "不该出现的正文" not in row.response_json

        again = claim("k1", "default", "", fp, task_id="task_b")
        assert again["action"] == "replay"
        assert again["task_id"] == "task_a"

    def test_done_expiry_takeover(self, mem_env):
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim("k1", "default", "", fp, task_id="task_a")
        settle(
            {
                "key": "k1",
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v["claimed_at"],
                "explicit": True,
            },
            ok=True,
            task_id="task_a",
        )
        _set_created("k1", "default", "", TTL_SECONDS + 10)
        assert claim("k1", "default", "", fp, task_id="task_b")["action"] == "new"

    def test_auto_key_never_settles_done(self, mem_env):
        """自动指纹键成功不落 done——行保持 accepted 到租约过期自然失效。"""
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim(auto_key(fp), "default", "", fp, task_id="task_a")
        outcome = settle(
            {
                "key": auto_key(fp),
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v["claimed_at"],
                "explicit": False,
            },
            ok=True,
            result={"ingested": True},
            task_id="task_a",
        )
        assert outcome == "n/a"
        assert _row(auto_key(fp)).state == "accepted"
        # 租约窗内重试仍拿到原任务号
        again = claim(auto_key(fp), "default", "", fp, task_id="task_b")
        assert again["action"] == "replay"
        assert again["task_id"] == "task_a"

    def test_claim_empty_key_is_passthrough(self, mem_env):
        assert claim("", "default", "", "fp")["action"] == "new"
        assert claim("  ", "default", "", "fp")["action"] == "new"

    def test_claim_disabled_on_db_failure(self, mem_env):
        """幂等层 DB 故障 fail-open（外部依赖异常注入，允许的 mock 面）。
        注入 OSError——那是 fail-open 的边界类（_DB_ERRORS），编程错误仍上抛。"""
        import lantai.storage.db as db_module

        def broken():
            raise OSError("db down")

        real = db_module.get_session
        db_module.get_session = broken
        try:
            verdict = claim("k1", "default", "", "fp", task_id="t")
        finally:
            db_module.get_session = real
        assert verdict["action"] == "disabled"


# ------------------------------------------------------------------ settle/release


class TestSettleRelease:
    def _binding(self, key="k1", explicit=True):
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim(key, "default", "", fp, task_id="task_a")
        return (
            {
                "key": key,
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v["claimed_at"],
                "explicit": explicit,
            },
            fp,
        )

    def test_settle_failure_releases_key(self, mem_env):
        """失败必须释放：重试可重新执行（上游 S-2/S-8 教训）。"""
        binding, fp = self._binding()
        assert settle(binding, ok=False) == "released"
        assert _row("k1") is None  # 行已删
        assert claim("k1", "default", "", fp, task_id="task_retry")["action"] == "new"

    def test_settle_stale_token_does_not_clobber_new_owner(self, mem_env):
        """行过期被接管后，迟到的 settle 只认自己的 created_at 令牌。"""
        binding, fp = self._binding()
        _set_created("k1", "default", "", LEASE_SECONDS + 5)
        # 新主接管（takeover 改写了 created_at）
        assert claim("k1", "default", "", fp, task_id="task_owner2")["action"] == "new"
        # 老 worker 迟到结算：找不到自己那条 claim，不得覆盖新主回执
        assert settle(binding, ok=True, task_id="task_old_stale") == "gone"
        row = _row("k1")
        assert json.loads(row.response_json)["task_id"] == "task_owner2"
        assert row.state == "accepted"

    def test_settle_failure_after_takeover_cannot_delete_new_owner(self, mem_env):
        binding, fp = self._binding()
        _set_created("k1", "default", "", LEASE_SECONDS + 5)
        assert claim("k1", "default", "", fp, task_id="task_owner2")["action"] == "new"
        assert settle(binding, ok=False) == "gone"
        assert _row("k1") is not None  # 新主的行还在

    def test_settle_without_binding_is_skipped(self, mem_env):
        assert settle(None, ok=True) == "skipped"
        assert settle({}, ok=False) == "skipped"

    def test_release_with_token_spares_done(self, mem_env):
        """带令牌的 release 不删 done 回执（已完成的结果不能被顺手抹掉重放）。"""
        binding, fp = self._binding()
        settle(binding, ok=True, task_id="task_a")
        release("k1", "default", "", claimed_at=binding["claimed_at"])
        assert _row("k1") is not None
        assert _row("k1").state == "done"


# ------------------------------------------------------------------ purge


class TestPurge:
    def test_purge_removes_expired_keeps_fresh(self, mem_env):
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        assert claim("fresh", "default", "", fp, task_id="t1")["action"] == "new"
        v_done = claim("old_done", "default", "", fp, task_id="t2")
        v_acc = claim("old_acc", "default", "", fp, task_id="t3")
        settle(
            {
                "key": "old_done",
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v_done["claimed_at"],
                "explicit": True,
            },
            ok=True,
            task_id="t2",
        )
        assert v_acc["action"] == "new"
        _set_created("old_done", "default", "", TTL_SECONDS + 10)
        _set_created("old_acc", "default", "", LEASE_SECONDS + 10)

        removed = purge_expired()
        assert removed == 2
        assert _row("fresh") is not None
        assert _row("old_done") is None
        assert _row("old_acc") is None


# ------------------------------------------------------------------ 潜移集成（真 worker）


class TestSubmitIntegration:
    TEXT = "记住：勘合集成测试条目"

    def test_auto_key_same_content_replays(self, mem_env, clean_tasks):
        """同文快速重提（宿主重试形态）：同一任务号，只有一个任务注册。"""
        ais = clean_tasks
        r1 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        r2 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        assert r1["task_id"] == r2["task_id"]
        assert r2.get("replayed") is True
        assert len(ais._TASKS) == 1
        st = _wait_task(r1["task_id"])
        assert st["status"] == "completed"

    def test_different_content_gets_new_task(self, mem_env, clean_tasks):
        ais = clean_tasks
        r1 = ais.submit_async_dialogue("记住：第一条", user_id="default")
        r2 = ais.submit_async_dialogue("记住：第二条", user_id="default")
        assert r1["task_id"] != r2["task_id"]
        assert not r2.get("replayed")
        _wait_task(r1["task_id"])
        _wait_task(r2["task_id"])

    def test_explicit_key_replay_and_conflict(self, mem_env, clean_tasks):
        ais = clean_tasks
        r1 = ais.submit_async_dialogue(
            "记住：显式键文本", user_id="default", idempotency_key="host-1"
        )
        r2 = ais.submit_async_dialogue(
            "记住：显式键文本", user_id="default", idempotency_key="host-1"
        )
        assert r1["task_id"] == r2["task_id"]
        assert r2.get("replayed") is True
        with pytest.raises(ValueError, match="conflict"):
            ais.submit_async_dialogue(
                "记住：换一段不同的文本", user_id="default", idempotency_key="host-1"
            )
        _wait_task(r1["task_id"])

    def test_completed_task_replays_done_receipt(self, mem_env, clean_tasks):
        """显式键任务完成后重提：done 回执重放，不重新执行。"""
        ais = clean_tasks
        r1 = ais.submit_async_dialogue(self.TEXT, user_id="default", idempotency_key="host-2")
        st = _wait_task(r1["task_id"])
        assert st["status"] == "completed"
        r2 = ais.submit_async_dialogue(self.TEXT, user_id="default", idempotency_key="host-2")
        assert r2["task_id"] == r1["task_id"]
        assert r2.get("replayed") is True
        # 重放期间没有新任务注册
        assert len(ais._TASKS) == 1

    def test_failed_task_releases_key_for_retry(self, mem_env, clean_tasks):
        """任务失败释放键：同键重提拿到**新**任务号（可重试不变量）。"""
        ais = clean_tasks
        bad = "x" * 50001  # 超 ingest_dialogue 50k 上限 → worker 内 ValueError
        r1 = ais.submit_async_dialogue(bad, user_id="default", idempotency_key="host-3")
        st = _wait_task(r1["task_id"])
        assert st["status"] == "failed"
        r2 = ais.submit_async_dialogue(bad, user_id="default", idempotency_key="host-3")
        assert r2["task_id"] != r1["task_id"]
        assert not r2.get("replayed")
        st2 = _wait_task(r2["task_id"])
        assert st2["status"] == "failed"  # 内容本身非法，如实再失败

    def test_restart_within_lease_still_replays(self, mem_env, clean_tasks):
        """重启丢 _TASKS 后租约窗内重提：耐久表仍重放原任务号（轮询如实
        not_found 是诚实行为——任务真丢了，回执不冒充结果）。"""
        ais = clean_tasks
        r1 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        _wait_task(r1["task_id"])
        ais.clear_tasks()  # 模拟进程重启
        r2 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        assert r2["task_id"] == r1["task_id"]
        assert r2.get("replayed") is True

    def test_mcp_handler_passes_idempotency_key(self, mem_env, clean_tasks):
        from lantai.cli.mcp import handle_dialogue_add_async

        r1 = handle_dialogue_add_async({"text": "记住：MCP 链路", "idempotency_key": "host-mcp"})
        r2 = handle_dialogue_add_async({"text": "记住：MCP 链路", "idempotency_key": "host-mcp"})
        assert r1["task_id"] == r2["task_id"]
        assert r2.get("replayed") is True
        _wait_task(r1["task_id"])

    def test_mcp_schema_declares_idempotency_key(self):
        from lantai.cli.mcp import TOOLS

        props = TOOLS["dialogue_add_async"]["inputSchema"]["properties"]
        assert "idempotency_key" in props

    def test_rest_route_replays(self, mem_env, clean_tasks):
        """REST 路由透传幂等键：重提同文返回原 task_id。"""
        from lantai.api.routes_dialogue import DialogueIngestReq, dialogue_async_route
        from lantai.core.auth import Principal

        ctx = Principal(user_id="default")
        r1 = dialogue_async_route(
            DialogueIngestReq(text="记住：REST 链路", idempotency_key="host-rest"), ctx
        )
        r2 = dialogue_async_route(
            DialogueIngestReq(text="记住：REST 链路", idempotency_key="host-rest"), ctx
        )
        assert r1["task_id"] == r2["task_id"]
        assert r2.get("replayed") is True
        _wait_task(r1["task_id"])

    def test_replay_response_shape_is_submit_compatible(self, mem_env, clean_tasks):
        """重放响应保持 submit 契约（task_id/status），只多 replayed 标记——
        宿主把它当普通 submit 响应轮询即可，无需特判。"""
        ais = clean_tasks
        r1 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        r2 = ais.submit_async_dialogue(self.TEXT, user_id="default")
        assert set(r1.keys()) <= set(r2.keys()) | {"replayed"}
        assert r2["status"] in ("queued", "completed", "processing", "pending")
