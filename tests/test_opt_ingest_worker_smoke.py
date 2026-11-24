"""重构守护锁（第四批）：摄取 worker 的两件事——单飞与短写事务。

1. **单飞**：cron、`/ingest/run`、`/monitor/workers/ingest/run` 是三个独立
   发起面，原先只有 APScheduler 自己的 `max_instances=1`——并发进入会让同一批
   source 跑两遍（重复 IngestJob、`content_hash` 唯一约束撞 IntegrityError 使
   整批回滚、双份 LLM 成本）。现已按 `evolve_worker._RUN_ONCE_LOCK` 范式加锁。
2. **短写事务**：原先 `extract_candidate()`（外部 LLM，单发 60s 预算）在写事务
   **内部**逐 doc 调用，N 条 doc 就把 SQLite 写锁持有 N×60s；本库
   `journal_mode=delete` + `busy_timeout=30s`，期间所有写排队等锁后报
   database is locked。现已拆成「无事务准备 + 短写事务两阶段」。

本文件不 mock 被测逻辑：`extract_candidate` 是真函数（配 conftest 假 chat_json
走真实降级路径），闸/去重/job 状态机全是真代码；只替 `fetch`（外部网络）与
尾部参数建议旁路（LLM 功能，非被测面）。

**事务边界用"另一条连接能不能写"直接取证**，不看代码结构——结构改了但行为仍
持锁时本锁照样红。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.core.scheduler as scheduler_mod
import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.models.tables import IngestJob, MemoryCandidate, RawDocument, Source

_OK_TEXT = "用户通勤路上喜欢喝乌龙茶，每周三做一次复盘。"


def _doc(doc_id: str, content: str = _OK_TEXT) -> RawDocument:
    return RawDocument(
        id=doc_id,
        source_type="article",
        source_id=doc_id,
        url=f"http://example.com/feed/{doc_id}",
        title=f"Digest {doc_id}",
        content_hash=f"hash-{doc_id}",
        content=content,
    )


class _FeedAdapter:
    """fetch 替身（外部网络面）：按 config 返回预置 docs。"""

    kind = "probe"

    def __init__(self, docs: list[RawDocument]):
        self.specs = [d.model_dump() for d in docs]

    def fetch(self, config: dict) -> list[RawDocument]:
        return [RawDocument.model_validate(s) for s in self.specs]


@pytest.fixture()
def ingest_env(monkeypatch, tmp_path):
    """file-backed SQLite（生产同款池语义，见 test_ingest_xiezhi_gate 的论证）。"""
    db_file = tmp_path / "opt-ingest.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"timeout": 2})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

    import lantai.workers.ingest_worker as worker_mod
    import lantai.workers.param_advice_worker as param_advice_mod

    monkeypatch.setattr(param_advice_mod, "run_param_advice_once", lambda: None)
    monkeypatch.setattr(worker_mod.settings, "PAPER_SIGNAL_ENABLED", False)

    saved = scheduler_mod.WORKER_LAST_RUN.pop("ingest", None)
    yield engine, worker_mod
    scheduler_mod.WORKER_LAST_RUN.pop("ingest", None)
    if saved is not None:
        scheduler_mod.WORKER_LAST_RUN["ingest"] = saved
    engine.dispose()


def _seed_source(engine, docs: list[RawDocument], kind: str = "probe"):
    with Session(engine) as s:
        s.add(Source(id="src-probe", kind=kind, config={"source_id": "src-probe"}, enabled=True))
        s.commit()


class TestIngestSingleFlight:
    def test_concurrent_run_is_rejected(self, ingest_env):
        """同一时刻第二次进入必须被拒（RuntimeError），不得双跑。"""
        engine, worker_mod = ingest_env
        _seed_source(engine, [_doc("d1")])
        worker_mod.ADAPTERS["probe"] = _FeedAdapter([_doc("d1")])

        # 占住锁：模拟「已经在跑」的那一路
        assert worker_mod._INGEST_LOCK.acquire(blocking=False)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                worker_mod.run_ingest_once()
        finally:
            worker_mod._INGEST_LOCK.release()

        # 空转验证：锁释放后同一条路径必须能真跑完
        worker_mod.run_ingest_once()
        with Session(engine) as s:
            jobs = s.exec(select(IngestJob)).all()
        assert len(jobs) == 1, f"单飞释放后应只留下一次运行：{[j.status for j in jobs]}"

    def test_lock_released_after_failure(self, ingest_env, monkeypatch):
        """异常路径也必须放锁（否则一次失败会把摄取永久锁死）。"""
        engine, worker_mod = ingest_env
        _seed_source(engine, [_doc("d1")])

        class _BoomAdapter:
            kind = "probe"

            def fetch(self, config):
                raise RuntimeError("network down")

        worker_mod.ADAPTERS["probe"] = _BoomAdapter()
        worker_mod.run_ingest_once()  # source 级异常被吞进 job.status=failed

        assert not worker_mod._INGEST_LOCK.locked(), "异常路径漏放锁"
        with Session(engine) as s:
            jobs = s.exec(select(IngestJob)).all()
        assert [j.status for j in jobs] == ["failed"]


class TestIngestShortWriteTransaction:
    def test_write_lock_not_held_during_extraction(self, ingest_env, monkeypatch):
        """提取（外部 LLM）期间不得持写锁：另一条连接必须能立刻写成功。

        取证方式：替身 extract_candidate 在被调用时**另开一条连接**做一次写 +
        commit，超时设 50ms。若主路径仍把 LLM 调用包在写事务里，这次写会撞
        锁超时（database is locked）→ 本用例红。
        """
        engine, worker_mod = ingest_env
        _seed_source(engine, [_doc("d1")])
        worker_mod.ADAPTERS["probe"] = _FeedAdapter([_doc("d1")])

        probe_engine = create_engine(
            f"sqlite:///{engine.url.database}", connect_args={"timeout": 0.05}
        )
        results: list[str] = []

        real_extract = worker_mod.extract_candidate

        def probing_extract(title, content):
            # 用独立连接写一行（真实 DML，不是结构断言）
            try:
                with Session(probe_engine) as s2:
                    s2.add(Source(id=f"probe-{len(results)}", kind="probe", config={}))
                    s2.commit()
                results.append("ok")
            except Exception as exc:  # noqa: BLE001 - 记录真实失败类型
                results.append(type(exc).__name__)
            return real_extract(title, content)

        monkeypatch.setattr(worker_mod, "extract_candidate", probing_extract)
        worker_mod.run_ingest_once()
        probe_engine.dispose()

        assert results, "提取必须被真实调用（否则本锁空转）"
        assert results == ["ok"] * len(results), f"提取期间写锁未释放：{results}"

    def test_batch_still_commits_all_documents(self, ingest_env):
        """反向保险：拆事务不得丢数据——三条 doc 的 RawDocument 与候选都要落库。"""
        engine, worker_mod = ingest_env
        docs = [_doc(f"d{i}", f"{_OK_TEXT} 第 {i} 条") for i in range(3)]
        _seed_source(engine, docs)
        worker_mod.ADAPTERS["probe"] = _FeedAdapter(docs)

        worker_mod.run_ingest_once()

        with Session(engine) as s:
            stored = {d.id for d in s.exec(select(RawDocument)).all()}
            cands = {c.document_id for c in s.exec(select(MemoryCandidate)).all()}
            job = s.exec(select(IngestJob)).one()
        assert stored == {"d0", "d1", "d2"}, f"RawDocument 丢了：{sorted(stored)}"
        assert cands == {"d0", "d1", "d2"}, f"候选丢了：{sorted(cands)}"
        assert job.status == "done"
        assert job.stats["docs"] == 3 and job.stats["candidates"] == 3
