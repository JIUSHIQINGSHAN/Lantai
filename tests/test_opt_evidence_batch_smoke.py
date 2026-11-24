"""等价性锁：候选详情的批量取数必须与逐条判定逐条一致。

`knowledge_candidate_service._evidence_rows` 原先对每条 evidence 各发一次
`select(MemoryItem)`，并对未命中 provenance 标记的来源再各发一次 checkpoint
查询。改成「一次 IN 取来源 + 一次 IN 取 checkpoint」之后，判据必须**逐条**与
`record_ops_service.is_candidate_origin` 相同——否则只是把 N+1 换成一次
更快的错判。

这里不抽样：同一批六种来源形态（普通 / 候选态 / 对账标记 / 复核回执命中 /
复核回执指向他人 / checkpoint 曾为候选）全量比对。不 mock 产品逻辑。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCheckpoint, MemoryItem
from lantai.services.record_ops_service import is_candidate_origin


@pytest.fixture
def session_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _item(mid: str, *, status="active", provenance=None) -> MemoryItem:
    now = utcnow()
    return MemoryItem(
        id=mid,
        key=f"k-{mid}",
        content=f"content-{mid}",
        lane="general",
        status=status,
        importance=0.5,
        created_at=now,
        updated_at=now,
        provenance=provenance,
    )


def _checkpoint(mid: str, *, before_status: str, after_status: str) -> MemoryCheckpoint:
    now = utcnow()
    return MemoryCheckpoint(
        id=f"ck-{mid}",
        memory_id=mid,
        version=1,
        before={"status": before_status},
        after={"status": after_status},
        created_at=now,
    )


def _seed(s: Session) -> list[MemoryItem]:
    items = [
        _item("plain"),
        _item("candidate_status", status="candidate"),
        _item("reconciled", provenance={"candidate_reconciliation": {"by": "worker"}}),
        _item(
            "receipt_self",
            provenance={
                "knowledge_candidate_review": {"receipt": {"candidate_id": "receipt_self"}}
            },
        ),
        _item(
            "receipt_other",
            provenance={
                "knowledge_candidate_review": {"receipt": {"candidate_id": "someone_else"}}
            },
        ),
        _item("checkpoint_candidate"),
    ]
    for it in items:
        s.add(it)
    # 只有最后一条需要靠 checkpoint 判定；前面几条都不该为此多查一次
    s.add(_checkpoint("checkpoint_candidate", before_status="candidate", after_status="active"))
    s.add(_checkpoint("plain", before_status="active", after_status="active"))
    s.commit()
    return items


def test_batch_verdict_matches_per_row_reference(session_env):
    from lantai.services import knowledge_candidate_service as svc

    items = _seed(session_env)
    batch = svc._candidate_origin_sources(session_env, items)
    reference = {m.id for m in items if is_candidate_origin(session_env, m)}
    assert batch == reference, (
        f"批量判定与逐条判定不一致：批量={sorted(batch)} 逐条={sorted(reference)}"
    )


def test_batch_verdict_is_not_vacuous(session_env):
    """反向保险：本锁必须能区分两种结果（全 True / 全 False 都算假绿）。"""
    from lantai.services import knowledge_candidate_service as svc

    items = _seed(session_env)
    batch = svc._candidate_origin_sources(session_env, items)
    assert batch, "一条都没判出候选出身——判据失效"
    assert len(batch) < len(items), "把全部来源都判成候选出身——判据过宽"
    assert "plain" not in batch
    assert "checkpoint_candidate" in batch
    assert "receipt_other" not in batch


def test_batch_does_one_checkpoint_query(session_env, monkeypatch):
    """checkpoint 取数必须是批量的：六条来源只应发一次 IN 查询。"""
    from lantai.services import knowledge_candidate_service as svc

    items = _seed(session_env)
    calls: list = []
    real_exec = Session.exec

    def counting_exec(self, statement, *a, **k):
        rendered = str(statement)
        if "memorycheckpoint" in rendered.lower():
            calls.append(rendered)
        return real_exec(self, statement, *a, **k)

    monkeypatch.setattr(Session, "exec", counting_exec, raising=True)
    svc._candidate_origin_sources(session_env, items)
    assert len(calls) == 1, f"checkpoint 被查了 {len(calls)} 次，批量未生效"
