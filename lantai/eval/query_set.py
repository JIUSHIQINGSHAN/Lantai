"""查询集构造：从 RetrievalEvent 干净事件构造可重复评估样本集。

核心函数 build_query_set——契约见 docs/dry-run-eval-task-split.md。
"""
from sqlmodel import Session, select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.eval.models import EvalQuerySet
from lantai.models.tables import RetrievalEvent
from lantai.storage import db


def build_query_set(name: str, *, noise_excluded: bool = True,
                    dedup: bool = True, limit: int | None = None) -> EvalQuerySet:
    """从 retrieval_event 干净事件构造查询集（去重 norm_hash），入库并返回。

    参数：
        name: 查询集名（唯一标识，重复时覆盖）
        noise_excluded: 排除 is_system_noise=1 的事件（默认 True）
        dedup: 按 query_norm_hash 去重（默认 True，保留每个 hash 最新一条）
        limit: 最多取 N 条（None=全部）

    返回：已入库的 EvalQuerySet（sample_count=去重后条数）
    """
    with db.get_session() as s:
        stmt = select(RetrievalEvent)
        if noise_excluded:
            stmt = stmt.where(RetrievalEvent.is_system_noise == False)  # noqa: E712
        stmt = stmt.order_by(RetrievalEvent.created_at.desc())  # 最新优先，去重保留最新
        if limit:
            stmt = stmt.limit(limit)
        events = s.exec(stmt).all()

    # 去重：按 query_norm_hash 保留最新（events 已按 created_at desc 排序）
    seen_hashes: set[str] = set()
    queries: list[dict] = []
    for ev in events:
        norm_hash = ev.query_norm_hash or ""
        if dedup and norm_hash:
            if norm_hash in seen_hashes:
                continue
            seen_hashes.add(norm_hash)
        queries.append({
            "query": ev.query_text,
            "event_id": ev.id,
            "lane": ev.lane or "",
            "norm_hash": norm_hash,
        })

    qs = EvalQuerySet(
        id=new_id("eqs"),
        name=name,
        criteria={
            "noise_excluded": noise_excluded,
            "dedup": dedup,
            "source": "retrieval_event",
            "limit": limit,
        },
        sample_count=len(queries),
        queries=queries,
    )
    with db.get_session() as s:
        # 同名查询集先删旧（覆盖语义，保证 name 唯一）
        old = s.exec(select(EvalQuerySet).where(EvalQuerySet.name == name)).all()
        for o in old:
            s.delete(o)
        s.add(qs)
        s.commit()
        s.refresh(qs)

    logger.info("eval query set built: name=%s samples=%d (dedup=%s noise_excluded=%s)",
                name, len(queries), dedup, noise_excluded)
    return qs


def load_query_set(name: str, session: Session | None = None) -> EvalQuerySet | None:
    """按名加载查询集（供 runner 使用）。"""
    def _load(s: Session) -> EvalQuerySet | None:
        rows = s.exec(select(EvalQuerySet).where(EvalQuerySet.name == name)).all()
        return rows[0] if rows else None
    if session is not None:
        return _load(session)
    with db.get_session() as s:
        return _load(s)
