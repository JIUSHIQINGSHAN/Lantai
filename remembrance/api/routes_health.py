from fastapi import APIRouter, Depends
from remembrance.core.auth import verify_api_key
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

    # LLM 端点
    try:
        from remembrance.llm.client import _client
        _client.models.list()
        checks["llm"] = "ok"
    except Exception as e:
        checks["llm"] = f"fail: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"ok": all_ok, "checks": checks}


@protected_router.get("/stats")
def stats():
    """记忆统计——总数/分布/coalesce 水位"""
    with db.get_session() as s:
        all_items = s.exec(select(MemoryItem)).all()
        total = len(all_items)

        lane_dist = {}
        status_dist = {}
        tier_dist = {}
        for m in all_items:
            lane_dist[m.lane] = lane_dist.get(m.lane, 0) + 1
            status_dist[m.status] = status_dist.get(m.status, 0) + 1
            tier_dist[m.tier] = tier_dist.get(m.tier, 0) + 1

    buffer = get_coalesce_buffer().water_level()

    return {
        "total_memories": total,
        "by_lane": lane_dist,
        "by_status": status_dist,
        "by_tier": tier_dist,
        "coalesce_buffer": buffer,
    }
