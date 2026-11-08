"""勘合——潜移异步摄取幂等回执（票据 .scratch/kanhe-idempotency/01）。

不 mock 冒烟：真 SQLite（内存引擎）、真线程池 worker、真 ingest_dialogue。
唯一 mock 面是「幂等层 DB 故障」（fail-open 路径，外部依赖异常注入，允许）。

核心不变量：
- 同一 (key,user,tenant)+同指纹在租约窗内至多执行一次；
- 任务失败后同键必须可重试（失败不得以 accepted/done 重放）；
- 回执表里永不出现正文。
"""

import json
import sys
import time
from contextlib import contextmanager
from unittest.mock import patch

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


def _advancing_clock(monkeypatch):
    """严格递增时钟（每调用 +1ms）——钉住「接管 + 带令牌删除」组合测试。

    Windows / CPython 3.11 的 time.time() 粒度约 15.6ms（实测 10 万次紧循环
    仅 5 个唯一值）：真时钟下 claim1 与接管 claim2 两次取时可落同一 tick
    同值，接管回写的 created_at 与 claim1 返回的 claimed_at **浮点相等**，
    带令牌 release/settle 的条件删除就命中新主行——测试间歇性翻红（v0.68
    票 69 终跑二轮实测炸出）。令牌相等性本身是生产令牌设计的缺陷，另立票
    （74-epoch-token-clock-granularity）；本文件要锁的是令牌失效后的删 0
    语义，不是宿主时钟粒度——事件模拟严格递增，不 mock 被测判定逻辑。
    """
    import datetime as _dt

    import lantai.core.idempotency as idem

    real = idem.utcnow
    seq = {"n": 0}

    def _clock():
        seq["n"] += 1
        return real() + _dt.timedelta(milliseconds=seq["n"])

    monkeypatch.setattr(idem, "utcnow", _clock)


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

    def test_settle_stale_token_does_not_clobber_new_owner(self, mem_env, monkeypatch):
        """行过期被接管后，迟到的 settle 只认自己的 created_at 令牌。"""
        _advancing_clock(monkeypatch)
        binding, fp = self._binding()
        _set_created("k1", "default", "", LEASE_SECONDS + 5)
        # 新主接管（takeover 改写了 created_at）
        assert claim("k1", "default", "", fp, task_id="task_owner2")["action"] == "new"
        # 老 worker 迟到结算：找不到自己那条 claim，不得覆盖新主回执
        assert settle(binding, ok=True, task_id="task_old_stale") == "gone"
        row = _row("k1")
        assert json.loads(row.response_json)["task_id"] == "task_owner2"
        assert row.state == "accepted"

    def test_settle_failure_after_takeover_cannot_delete_new_owner(self, mem_env, monkeypatch):
        _advancing_clock(monkeypatch)
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

    def test_release_without_token_spares_done(self, mem_env):
        """无令牌的 release 同样不删 done 回执（票 75）。

        此前 `state != "done"` 守卫只在带 claimed_at 分支内生效，无令牌调用把
        同键 done 回执一并删净——同键同文重投从 replay 退化为 re-execution
        （重复建候选，勘合立项要防的原病；探针
        `.scratch/_v068_probe_r9_release_nukes_done.py` 实锤）。
        """
        binding, fp = self._binding()
        settle(binding, ok=True, task_id="task_a")
        release("k1", "default", "")
        row = _row("k1")
        assert row is not None
        assert row.state == "done"
        # 后果臂：done 回执在，同键同文重投仍走 replay 原任务号
        assert claim("k1", "default", "", fp, task_id="task_b")["action"] == "replay"

    def test_release_without_token_clears_accepted(self, mem_env):
        """无令牌 release 的保留语义：清 accepted（运维面强释放），只保 done。"""
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        assert claim("k2", "default", "", fp, task_id="task_c")["action"] == "new"
        release("k2", "default", "")
        assert _row("k2") is None


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

    def test_purge_keeps_done_within_ttl(self, mem_env):
        """done 回执未超 TTL 必须保留（票 75：purge 下推 SQL 后的语义锁）。

        既有 purge 用例的 fresh 行是 accepted 态，「done 但年轻」这一态没有锁——
        SQL 下推写错（漏 or_ 的 state 臂）时它第一个被误删。
        """
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim("young_done", "default", "", fp, task_id="t9")
        settle(
            {
                "key": "young_done",
                "user_id": "default",
                "tenant_id": "",
                "claimed_at": v["claimed_at"],
                "explicit": True,
            },
            ok=True,
            task_id="t9",
        )
        _set_created("young_done", "default", "", TTL_SECONDS // 2)

        assert purge_expired() == 0
        row = _row("young_done")
        assert row is not None and row.state == "done"


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
        # 重放返回的是原任务号，等它终结再进 teardown（票 05 续26 孤儿竞态）
        _wait_task(r2["task_id"])


# ------------------------------------------------------------------ 票 24 子项 1：接管/结算竞态


class TestTakeOverRace:
    """票 24 子项 1：过期接管是「SELECT 令牌复核 → Python 判 → ORM 主键 UPDATE」
    三步——令牌比对只在 SELECT 里，落账 UPDATE 按 (key,user,tenant) 主键定位、
    对 created_at 已被并发方推进的行照写（last-writer-wins）→ 两并发接管都读到
    旧令牌都判赢 → 同键双执行。

    交错缝：_take_over 的回执计算（_receipt_json，SELECT 复核之后、提交之前，
    A 首条 DML 之前的真实代码边界）——垫片先调真实现，再在返回前让 B 走完整
    真实 claim 流程（mem_env 文件库下 B 持独立连接、真实提交）。单线程顺序
    执行，确定性复现，无 sleep。修复语义：单条条件 UPDATE
    （WHERE 主键三元组 AND created_at=旧令牌）+ rowcount 判定胜负。
    """

    def test_concurrent_takeover_single_winner(self, mem_env):
        import lantai.core.idempotency as idem

        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        assert claim("k-race", "default", "", fp, task_id="task_seed")["action"] == "new"
        _set_created("k-race", "default", "", LEASE_SECONDS + 5)

        racing = {"done": False}
        real_receipt = idem._receipt_json
        b_result: dict = {}

        def _receipt_with_interleave(receipt):
            out = real_receipt(receipt)
            if racing["done"]:
                return out
            if sys._getframe(1).f_code.co_name == "_take_over":
                racing["done"] = True
                b_result.update(claim("k-race", "default", "", fp, task_id="task_b"))
            return out

        with patch.object(idem, "_receipt_json", new=_receipt_with_interleave):
            a_result = claim("k-race", "default", "", fp, task_id="task_a")

        assert b_result, "交错未发生：B 侧未执行（缝位失配，测试失效）"
        winners = [who for who, v in (("a", a_result), ("b", b_result)) if v.get("action") == "new"]
        assert len(winners) == 1, (
            f"双执行（票 24 子项 1）：并发接管同键双方都判赢 a={a_result.get('action')} "
            f"b={b_result.get('action')}——令牌比对不在同一条原子 UPDATE 内，"
            "ORM 主键 UPDATE 对已提交新令牌照写"
        )


class TestSettleTakeOverRace:
    """票 24 子项 1 姊妹窗口：settle 的令牌比对同样只在 SELECT 里
    （idempotency.py:238-247），落账 UPDATE/DELETE 走主键——接管插在
    SELECT 与落账之间时，迟到结算把新主回执覆写成旧主回执（ok=True 臂）、
    或把新主的 claim 整行删掉（ok=False 臂，重试即双执行）。
    顺序形态已有两把锁（test_settle_stale_token_* / *_after_takeover_*），
    这里锁的是交错窗口内的真竞态。缝位：settle 的回执计算（_receipt_json，
    SELECT 之后、落账之前）。
    """

    def test_late_settle_cannot_clobber_takeover_in_flight(self, mem_env):
        import lantai.core.idempotency as idem

        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim("k-st", "default", "", fp, task_id="task_a")
        binding = {
            "key": "k-st",
            "user_id": "default",
            "tenant_id": "",
            "claimed_at": v["claimed_at"],
            "explicit": True,
        }
        _set_created("k-st", "default", "", LEASE_SECONDS + 5)

        racing = {"done": False}
        real_receipt = idem._receipt_json

        def _receipt_with_interleave(receipt):
            out = real_receipt(receipt)
            if racing["done"]:
                return out
            if sys._getframe(1).f_code.co_name == "settle":
                racing["done"] = True
                assert claim("k-st", "default", "", fp, task_id="task_owner2")["action"] == "new"
            return out

        with patch.object(idem, "_receipt_json", new=_receipt_with_interleave):
            outcome = settle(binding, ok=True, result={"ingested": True}, task_id="task_old_stale")

        row = _row("k-st")
        assert outcome == "gone", (
            f"迟到结算判 {outcome}：接管已换令牌，旧令牌结算不得落账（票 24 子项 1 姊妹窗口）"
        )
        assert row is not None and row.state == "accepted"
        assert json.loads(row.response_json)["task_id"] == "task_owner2", (
            "新主回执被迟到结算覆写（票 24）：令牌比对不在同一条原子 UPDATE 内"
        )


# ------------------------------------------------------------------ 票 24 子项 3：feedback 重放


class TestFeedbackReplayIdempotency:
    """票 24 子项 3：/evolution/feedback（REST routes_evolution.py:80 与 MCP
    cli/mcp.py:766 双入口收敛在 record_feedback_entry）无去重键——宿主/网络层
    原样重发同一份反馈，三计数各再加一次（use_count/helpful_count 是考功/
    遗忘的输入，虚增即「高频低效」误判面）。

    票 23 F1 关掉的是并发丢失轴（原子递增）；重放轴不在此列——修法选
    **勘合票据**（自动指纹键，键=整个请求体+viewer 的 sha256，重试窗=
    LEASE_SECONDS 600s 勘合教义：重试发生在秒-分钟尺度；窗内同体重放判
    pending → ack 不再计数，窗后同文再发 = 新反馈事件照常计数）。指纹覆盖
    整个请求体（含 query）：不同 query 的两次「有用」是两个真实事件。
    服务层 record_feedback 保持纯函数（票 23 F1 并发双计数契约与直调不动）。
    """

    def _seed(self, memory_id: str):
        import lantai.storage.db as db_module
        from lantai.models.tables import MemoryItem

        with db_module.get_session() as s:
            s.add(
                MemoryItem(
                    id=memory_id,
                    content="反馈目标记忆",
                    memory_type="text",
                    user_id="default",
                    status="active",
                )
            )
            s.commit()

    def _counts(self, memory_id: str):
        import lantai.storage.db as db_module
        from lantai.models.tables import MemoryItem

        with db_module.get_session() as s:
            mem = s.get(MemoryItem, memory_id)
            return mem.use_count, mem.helpful_count

    def _entry(self, memory_id: str, *, helped=True, query="q", user="default"):
        from lantai.core.auth import Principal
        from lantai.models.schemas import FeedbackReq
        from lantai.services.evolution_service import record_feedback_entry

        return record_feedback_entry(
            FeedbackReq(
                memory_id=memory_id,
                query=query,
                helped=helped,
                user_accepted=True,
                hallucination_risk=0.0,
            ),
            principal=Principal(user_id=user),
        )

    def test_replayed_feedback_counts_once(self, mem_env):
        self._seed("mem_fb")
        r1 = self._entry("mem_fb")
        r2 = self._entry("mem_fb")  # 宿主重试：原样重发同一份
        assert r1.get("ok") is True, f"首次反馈失败：{r1}"
        assert r2.get("ok") is True, f"重放未得到 ack：{r2}"
        use_count, helpful_count = self._counts("mem_fb")
        assert use_count == 1, f"重放双计数（票 24 子项 3）：use_count={use_count}"
        assert helpful_count == 1, f"重放双计数：helpful_count={helpful_count}"

    def test_different_query_is_distinct_event(self, mem_env):
        """语义锁：不同 query 的两次「有用」是两个真实反馈事件，都计数。"""
        self._seed("mem_fb2")
        assert self._entry("mem_fb2", query="问题一").get("ok") is True
        assert self._entry("mem_fb2", query="问题二").get("ok") is True
        use_count, helpful_count = self._counts("mem_fb2")
        assert (use_count, helpful_count) == (2, 2), (
            f"不同 query 被误去重：{(use_count, helpful_count)}——指纹必须覆盖整个请求体"
        )

    def test_different_verdict_is_distinct_event(self, mem_env):
        """语义锁：同一记忆上不同裁决（helped 翻转）是独立事件，都计数。

        跨用户形态不在此测（跨用户 feedback 被 ACL 拒，readside-gaps/13 契约；
        键作用域含 viewer 由勘合本体的 test_claim_scope_by_user_and_tenant 锁）。
        """
        self._seed("mem_fb3")
        assert self._entry("mem_fb3", helped=True).get("ok") is True
        assert self._entry("mem_fb3", helped=False).get("ok") is True
        use_count, helpful_count = self._counts("mem_fb3")
        assert use_count == 2 and helpful_count == 1, (
            f"不同裁决被误去重：{(use_count, helpful_count)}——指纹必须覆盖 helped 字段"
        )


class TestTakeOverStateGuard:
    """复查轮补刀（票 24 验收「并发窗口是否真的关上」）：仅令牌守卫挡不住
    settle 的 state 前移——settle(ok=True) finalize **不改 created_at**，
    judge 判 accepted 过期与 worker finalize 之间交错时，接管把新鲜 done
    回执覆写回 accepted、已完成的任务将被重执行。修法：接管 WHERE 加
    state=扫描值 守卫（三处调用点都持扫描期 state）。
    """

    def test_takeover_cannot_clobber_fresh_done_settled_in_flight(self, mem_env):
        import datetime as _dt

        import lantai.core.idempotency as idem

        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        v = claim("k-fs", "default", "", fp, task_id="task_a")
        binding = {
            "key": "k-fs",
            "user_id": "default",
            "tenant_id": "",
            "claimed_at": v["claimed_at"],
            "explicit": True,
        }

        racing = {"done": False}
        real_receipt = idem._receipt_json
        real_utcnow = idem.utcnow

        def _shifted_utcnow():
            # 时钟平移模拟租约过期：行 created_at 保持原值（与物理时序同构，
            # settle 的令牌才仍然匹配——_set_created 改写 created_at 会让
            # settle 令牌错配成 gone，测不到目标窗口）
            return real_utcnow() + _dt.timedelta(seconds=LEASE_SECONDS + 5)

        def _receipt_with_interleave(receipt):
            out = real_receipt(receipt)
            if racing["done"]:
                return out
            if sys._getframe(1).f_code.co_name == "_take_over":
                racing["done"] = True
                assert (
                    settle(binding, ok=True, result={"ingested": True}, task_id="task_a")
                    == "finalized"
                )
            return out

        with (
            patch.object(idem, "utcnow", _shifted_utcnow),
            patch.object(idem, "_receipt_json", new=_receipt_with_interleave),
        ):
            again = claim("k-fs", "default", "", fp, task_id="task_takeover")

        row = _row("k-fs")
        assert row is not None and row.state == "done", (
            f"新鲜 done 回执被接管覆写成 {row.state if row else None}（票 24）："
            "settle 不改 created_at，仅令牌守卫挡不住 state 前移"
        )
        assert json.loads(row.response_json)["task_id"] == "task_a"
        assert again["action"] == "pending", f"接管判 {again.get('action')}：state 已前移应判负"

    def test_release_with_stale_token_spares_new_owner(self, mem_env, monkeypatch):
        """顺序回归锁（票 24）：接管换令牌后，旧主的带令牌 release 删 0 行。

        release 的交错窗口无模块级函数缝可钉（SELECT→DELETE 之间无调用边界），
        修复与 settle 同款单条条件 DELETE；本锁按修复语义锁定：条件 DELETE 对
        最新已提交状态求值，旧令牌删不到新主的行（旧实现 SELECT 带令牌过滤，
        顺序形态本就安全——交错窗靠条件语句按构造关死，如实申报非红转绿）。
        """
        fp = fingerprint_payload("文本", "default", "dialogue", "", None)
        _advancing_clock(monkeypatch)
        v = claim("k-rl", "default", "", fp, task_id="task_old")
        _set_created("k-rl", "default", "", LEASE_SECONDS + 5)
        assert claim("k-rl", "default", "", fp, task_id="task_new")["action"] == "new"
        release("k-rl", "default", "", claimed_at=v["claimed_at"])
        row = _row("k-rl")
        assert row is not None, "旧主 release 删掉了新主的 claim（重试即双执行）"
        assert json.loads(row.response_json)["task_id"] == "task_new"
