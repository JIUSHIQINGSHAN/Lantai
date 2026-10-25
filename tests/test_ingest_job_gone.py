"""票 16 L6：`run_ingest_once` 收尾 `s.get(IngestJob, job.id)` 未判 None 即解引用。

并发删除（外部清理 / 另一进程）下两条收尾路径都会 AttributeError：
- done 收尾在 try 内，炸后被 except 捕获；
- failed 收尾再炸一次 → 直接传播出 worker，**中断整个 source 循环**，
  后续 source 全部停摆，尾部 `record_run("ingest")` 也不再执行。

mock 边界（AGENTS.md 测试纪律）：`adapter.fetch`（RSS/arxiv 网络抓取）与
`extract_candidate`（LLM 提取）是外部网络调用，允许替身；job 状态机、
去重、收尾防护全是真代码。「并发删除」在替身内开**真实新 session** 删行
模拟——同一内存库同表，与另一进程删除等价。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.core.scheduler as scheduler_mod
from lantai.models.tables import IngestJob, RawDocument, Source


class _GoneMidRunAdapter:
    """fetch 成功返回一篇文档；`delete_in_extract` 控制在哪个阶段并发删 job 行。

    job.id 由 worker 内部 new_id 生成，测试探针删**全部** IngestJob 行
    （本库只有 worker 自己创建的那一行）。
    """

    kind = "probe"

    def __init__(self, session_factory, delete_in_extract: bool):
        self.session_factory = session_factory
        self.delete_in_extract = delete_in_extract

    def _delete_job_rows(self):
        """另一个真实 session 删 job 行（模拟并发删除，同库等价另一进程）。"""
        with self.session_factory() as s:
            for row in s.exec(select(IngestJob)).all():
                s.delete(row)
            s.commit()

    def fetch(self, config):
        if not self.delete_in_extract:
            self._delete_job_rows()  # done 收尾路径：主流程成功，收尾时 job 已没
        return [
            RawDocument(
                id="doc-gone-1",
                source_type="rss",
                source_id="src-probe",
                url="http://example.com/gone-1",
                title="并发删除探针",
                content_hash="hash-gone-1",
                content="并发删除探针正文",
            )
        ]


def _extract_and_maybe_fail(session_factory, delete_in_extract: bool, fail: bool):
    """extract_candidate 替身：可选先删 job 行（failed 路径场景），可选抛错。"""

    def _extract(title, content):
        if delete_in_extract and fail:
            with session_factory() as s:
                for row in s.exec(select(IngestJob)).all():
                    s.delete(row)
                s.commit()
            raise RuntimeError("probe: extraction failed after concurrent delete")
        return {
            "topic": title,
            "summary": content,
            "claims": [],
            "methods": [],
            "constraints": [],
            "actions": [],
            "extractor_confidence": 0.9,
        }

    return _extract


@pytest.fixture()
def ingest_db(monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session（仅隔离存储，同 param_env 口径）。"""
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setitem(scheduler_mod.WORKER_LAST_RUN, "ingest", "")
    return engine


def _seed_source(session_factory) -> None:
    with session_factory() as s:
        s.add(Source(id="src-probe", kind="probe", config={}, enabled=True))
        s.commit()


def _wire(monkeypatch, session_factory, adapter, extract):
    import lantai.workers.ingest_worker as worker_mod
    import lantai.workers.param_advice_worker as param_advice_mod

    monkeypatch.setitem(worker_mod.ADAPTERS, "probe", adapter)
    monkeypatch.setattr(worker_mod, "extract_candidate", extract)
    # 尾部旁路（摄入后触发参数建议，LLM 功能）非本票被测逻辑，替身跳过
    monkeypatch.setattr(param_advice_mod, "run_param_advice_once", lambda: None)
    return session_factory


class TestIngestJobGoneSafe:
    """job 行被并发删除时两条收尾路径都不得 None 解引用——留痕跳过。"""

    def test_done_path_job_gone_no_crash(self, ingest_db, monkeypatch, caplog):
        """done 收尾：主流程成功但 job 行已没 → 留痕跳过，循环与 record_run 不中断。"""
        import logging

        session_factory = _wire(
            monkeypatch,
            lambda: Session(ingest_db),
            _GoneMidRunAdapter(lambda: Session(ingest_db), delete_in_extract=False),
            _extract_and_maybe_fail(lambda: Session(ingest_db), False, fail=False),
        )
        _seed_source(session_factory)

        from lantai.workers.ingest_worker import run_ingest_once

        with caplog.at_level(logging.WARNING):
            run_ingest_once()  # 改前：done 收尾 AttributeError → except → failed 收尾再炸 → 传播

        with session_factory() as s:
            docs = s.exec(select(RawDocument)).all()
        assert len(docs) == 1, "文档主流程应正常完成，不受 job 行消失影响"
        assert scheduler_mod.WORKER_LAST_RUN.get("ingest"), (
            "循环不得中断：尾部 record_run('ingest') 必须执行"
        )
        assert any("任务已不存在" in r.getMessage() for r in caplog.records), (
            "必须按「任务已不存在」留痕，不得静默"
        )

    def test_failed_path_job_gone_no_crash(self, ingest_db, monkeypatch, caplog):
        """failed 收尾：提取失败且 job 行已没 → 留痕跳过，不得二次炸出 worker。"""
        import logging

        session_factory = _wire(
            monkeypatch,
            lambda: Session(ingest_db),
            _GoneMidRunAdapter(lambda: Session(ingest_db), delete_in_extract=True),
            _extract_and_maybe_fail(lambda: Session(ingest_db), True, fail=True),
        )
        _seed_source(session_factory)

        from lantai.workers.ingest_worker import run_ingest_once

        with caplog.at_level(logging.WARNING):
            run_ingest_once()  # 改前：failed 收尾 AttributeError 直接传播出 worker

        assert scheduler_mod.WORKER_LAST_RUN.get("ingest"), (
            "循环不得中断：尾部 record_run('ingest') 必须执行"
        )
        assert any("任务已不存在" in r.getMessage() for r in caplog.records), (
            "必须按「任务已不存在」留痕，不得静默"
        )
