from fastapi import APIRouter
from sqlmodel import select

from lantai.core import scheduler
from lantai.core.settings import settings
from lantai.ingestion.coalesce import get_coalesce_buffer
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 公共路由（不需要鉴权）
router = APIRouter()
# 受保护路由（需要 API Key）
protected_router = APIRouter()


@router.get("/health")
def health():
    """简单存活探针——Docker HEALTHCHECK 用，公开；不暴露内部状态"""
    return {"ok": True}


@router.get("/api/memory/health")
def memory_health():
    """旧兼容端点"""
    return {"ok": True, "service": "lantai"}


@protected_router.get("/health/deep")
def health_deep():
    """深度健康检查——检查 SQLite/ChromaDB/LLM 端点"""
    checks = {}

    # SQLite 可写
    try:
        with db.get_session() as s:
            s.exec(select(MemoryItem).limit(1))
        checks["sqlite"] = "ok"
    except Exception as e:
        checks["sqlite"] = f"fail: {e}"

    # ChromaDB
    try:
        from lantai.storage.vector_store import get_vector_store

        get_vector_store()
        checks["chromadb"] = "ok"
    except Exception as e:
        checks["chromadb"] = f"fail: {e}"

    # FTS5 词汇召回通道（票 .scratch/fts-availability/01）
    # 只查 sqlite 表存在与 chromadb 可达都探不出 FTS 坏——而 FTS 坏的表现是
    # 「检索结果变少」，用户会以为记忆没存进去。故必须单独核验
    # memory_fts 的真实定义（IF NOT EXISTS 对错表是静默 no-op，只能读 sqlite_master）。
    try:
        with db.get_session() as s:
            row = (
                s.connection()
                .exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'memory_fts'")
                .fetchone()
            )
        ddl = (row[0] or "").lower() if row else ""
        if row and "fts5" in ddl and "trigram" in ddl:
            checks["fts"] = "ok"
        else:
            checks["fts"] = (
                "fail: memory_fts missing or not an FTS5/trigram table (lexical recall degraded)"
            )
    except Exception as e:
        checks["fts"] = f"fail: {e}"

    # LLM 端点（未配置 key 时跳过，避免每次探活触发外部调用）
    if not settings.OPENAI_API_KEY:
        checks["llm"] = "skipped (no key)"
    else:
        try:
            from lantai.llm.client import _client

            _client.models.list()
            checks["llm"] = "ok"
        except Exception as e:
            checks["llm"] = f"fail: {e}"

    all_ok = all(v in ("ok", "skipped (no key)") for v in checks.values())
    return {"ok": all_ok, "checks": checks}


@protected_router.get("/stats")
def stats():
    """记忆统计——SQL 聚合，避免全表加载到内存"""
    from sqlmodel import func

    with db.get_session() as s:
        total = s.exec(select(func.count()).select_from(MemoryItem)).one()
        lane_rows = s.exec(select(MemoryItem.lane, func.count()).group_by(MemoryItem.lane)).all()
        status_rows = s.exec(
            select(MemoryItem.status, func.count()).group_by(MemoryItem.status)
        ).all()
        tier_rows = s.exec(select(MemoryItem.tier, func.count()).group_by(MemoryItem.tier)).all()
        domain_rows = s.exec(
            select(MemoryItem.domain, func.count()).group_by(MemoryItem.domain)
        ).all()
        decay_rows = s.exec(
            select(MemoryItem.decay_class, func.count()).group_by(MemoryItem.decay_class)
        ).all()

    buffer = get_coalesce_buffer().water_level()
    return {
        "total_memories": total,
        "by_lane": {k: v for k, v in lane_rows},
        "by_status": {k: v for k, v in status_rows},
        "by_tier": {k: v for k, v in tier_rows},
        "by_domain": {k: v for k, v in domain_rows},
        "by_decay_class": {k: v for k, v in decay_rows},
        "coalesce_buffer": buffer,
        "workers": scheduler.WORKER_LAST_RUN,
    }


@protected_router.get("/usage")
def usage():
    """最近 7 天每日新增记忆数——单条 GROUP BY，不整表加载；缺日补零。"""
    from lantai.ops.usage import collect_usage

    return collect_usage(days=7)
