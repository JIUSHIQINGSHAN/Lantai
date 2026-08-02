from fastapi import APIRouter, Depends
from remembrance.core.auth import verify_api_key
from remembrance.core import scheduler
from remembrance.core.settings import settings
from remembrance.storage import db
from remembrance.models.tables import MemoryItem
from remembrance.ingestion.coalesce import get_coalesce_buffer
from sqlmodel import select

# 公共路由（不需要鉴权）
router = APIRouter()
# 受保护路由（需要 API Key）
protected_router = APIRouter()


@router.get("/health")
def health():
    """简单存活探针——Docker HEALTHCHECK 用，公开"""
    return {"ok": True}


@router.get("/api/memory/health")
def memory_health():
    """旧兼容端点"""
    return {"ok": True, "service": "remembrance"}


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
        from remembrance.storage.vector_store import get_vector_store
        store = get_vector_store()
        checks["chromadb"] = "ok"
    except Exception as e:
        checks["chromadb"] = f"fail: {e}"

    # LLM 端点（未配置 key 时跳过，避免每次探活触发外部调用）
    if not settings.OPENAI_API_KEY:
        checks["llm"] = "skipped (no key)"
    else:
        try:
            from remembrance.llm.client import _client
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
        lane_rows = s.exec(select(MemoryItem.lane, func.count())
                           .group_by(MemoryItem.lane)).all()
        status_rows = s.exec(select(MemoryItem.status, func.count())
                             .group_by(MemoryItem.status)).all()
        tier_rows = s.exec(select(MemoryItem.tier, func.count())
                           .group_by(MemoryItem.tier)).all()

    buffer = get_coalesce_buffer().water_level()
    return {
        "total_memories": total,
        "by_lane": {k: v for k, v in lane_rows},
        "by_status": {k: v for k, v in status_rows},
        "by_tier": {k: v for k, v in tier_rows},
        "coalesce_buffer": buffer,
        "workers": scheduler.WORKER_LAST_RUN,
    }
