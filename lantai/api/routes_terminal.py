"""Glass-box Terminal：透明化对话与记忆透视 SSE 端点"""

import contextlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.core.logger import logger
from lantai.services.edge_service import list_edges
from lantai.services.memory_service import list_memories
from lantai.storage.db import get_session

router = APIRouter()


def get_db_conn():
    session = get_session()
    conn = session.connection().connection
    return session, conn


class ChatReq(BaseModel):
    query: str
    domain: str = "user"
    top_k: int = 8
    force: bool = True


class MemoryUpdateReq(BaseModel):
    content: str | None = None
    importance: float | None = None
    confidence: float | None = None
    memory_type: str | None = None


class MergeReq(BaseModel):
    source_id: str
    target_id: str


class RetractReq(BaseModel):
    """撤回请求：reason 必填非空（主张为何停用必须留痕）。"""

    reason: str


class ReasonReq(BaseModel):
    """归档/恢复归档请求：reason 可选。"""

    reason: str = ""


class CorrectReq(BaseModel):
    """纠错请求：content 必填，reason 建议提供。"""

    content: str
    reason: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/terminal/chat")
async def terminal_chat_stream(req: ChatReq, principal=Depends(get_current_user)):
    """SSE 流式端点：透明化展示记忆检索全过程"""
    from lantai.gate.prefilter import relevance_check as check_gate
    from lantai.retrieval.hybrid import hybrid_search

    def generate():
        # Step 1: 闸门判定
        yield _sse("step", {"phase": "gate_check", "message": f"正在判定查询意图: '{req.query}'"})
        try:
            gate = check_gate(req.query)
            gate_data = {
                "needs_memory": gate.get("needs_memory", True),
                "reason": gate.get("reason", ""),
            }
        except Exception as e:
            gate_data = {"needs_memory": True, "reason": f"闸门异常({e})，默认放行"}
        yield _sse("gate", gate_data)

        # Step 2: 记忆检索
        yield _sse(
            "step", {"phase": "retrieval", "message": f"正在执行四路检索 (top_k={req.top_k})..."}
        )
        try:
            items = hybrid_search(
                query=req.query,
                top_k=req.top_k,
                use_rerank=True,
                domain=req.domain if req.domain != "all" else None,
                principal=principal,
            )
            # hybrid_search returns a list of items directly, unlike search_memories
            # convert list to match what the old code expected or just use directly
        except Exception as e:
            items = []
            yield _sse("error", {"message": f"检索异常: {e}"})

        # Step 3: 逐条推送命中节点
        nodes = []
        for i, item in enumerate(items):
            m = item.get("memory", item)
            node = {
                "id": m.get("id", f"node-{i}"),
                "content": m.get("content", m.get("key", "")),
                "domain": m.get("domain", "user"),
                "lane": m.get("lane", "general"),
                "score": item.get("score", 1.0),
                "decay_score": m.get("decay_score", 1.0),
                "confidence": m.get("confidence", 0.9),
                "importance": m.get("importance", 0.8),
                "memory_type": m.get("memory_type", "semantic"),
                "version": m.get("version", 1),
                "created_at": m.get("created_at", ""),
            }
            nodes.append(node)
            yield _sse("node_hit", {"index": i, "node": node})
            time.sleep(0.05)  # 微延迟营造逐条涌现效果

        # Step 4: 推送边关系
        yield _sse("step", {"phase": "edges", "message": "正在加载记忆关系图谱..."})
        edges = []
        for n in nodes[:20]:  # 最多查前 20 个节点的边
            try:
                node_edges = list_edges(n["id"])
                for e in node_edges if isinstance(node_edges, list) else []:
                    edges.append(
                        {
                            "id": e.get("id", ""),
                            "source": e.get("source_memory_id", ""),
                            "target": e.get("target_memory_id", ""),
                            "relation": e.get("relation", "related"),
                            "confidence": e.get("confidence", 0.5),
                        }
                    )
            except Exception:
                pass
        yield _sse("edges", {"edges": edges})

        # Step 5: 完成
        yield _sse(
            "complete",
            {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "message": f"检索完成：命中 {len(nodes)} 条记忆，{len(edges)} 条关系边",
            },
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/terminal/graph")
def terminal_graph(domain: str = "", limit: int = 100, principal=Depends(get_current_user)):
    """获取记忆图谱数据（节点 + 边）用于 D3 力导向图"""
    memories = list_memories(
        lane="",
        status="",
        decay_class="",
        memory_type="",
        limit=limit,
        offset=0,
        principal=principal,
    )
    items = memories.get("memories") or memories.get("items") or []
    if domain:
        items = [m for m in items if m.get("domain") == domain]

    nodes = []
    for m in items:
        nodes.append(
            {
                "id": m.get("id", ""),
                "content": (m.get("content", "") or "")[:80],
                "domain": m.get("domain", "user"),
                "lane": m.get("lane", "general"),
                "importance": m.get("importance", 0.8),
                "decay_score": m.get("decay_score", 1.0),
                "confidence": m.get("confidence", 0.9),
                "memory_type": m.get("memory_type", "semantic"),
                "version": m.get("version", 1),
            }
        )

    edges = []
    seen = set()
    for n in nodes[:50]:
        try:
            node_edges = list_edges(n["id"])
            for e in node_edges if isinstance(node_edges, list) else []:
                eid = e.get("id", "")
                if eid not in seen:
                    seen.add(eid)
                    edges.append(
                        {
                            "id": eid,
                            "source": e.get("source_memory_id", ""),
                            "target": e.get("target_memory_id", ""),
                            "relation": e.get("relation", "related"),
                            "confidence": e.get("confidence", 0.5),
                        }
                    )
        except Exception:
            pass

    return {"nodes": nodes, "edges": edges}


@router.get("/terminal/memory/{memory_id}")
def get_single_memory(memory_id: str, principal=Depends(get_current_user)):
    """获取单条记忆的完整详情"""
    session, conn = get_db_conn()
    try:
        row = conn.execute("SELECT * FROM memoryitem WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            raise HTTPException(404, "memory not found")
        # Sqlite3 row may not be directly serializable, convert to dict using cursor description if needed
        # Or simpler for SQLModel, query via session
        from lantai.models.tables import MemoryItem

        m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
        if not m:
            raise HTTPException(404, "memory not found")
        return m.model_dump()
    finally:
        session.close()


@router.put("/terminal/memory/{memory_id}")
def update_memory(memory_id: str, req: MemoryUpdateReq, principal=Depends(get_current_user)):
    """更新单条记忆的内容、重要性或置信度"""
    session, conn = get_db_conn()
    try:
        from lantai.models.tables import MemoryItem

        m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
        if not m:
            raise HTTPException(404, "memory not found")

        updates = 0
        if req.content is not None:
            m.content = req.content
            updates += 1
        if req.importance is not None:
            m.importance = req.importance
            updates += 1
        if req.confidence is not None:
            m.confidence = req.confidence
            updates += 1
        if req.memory_type is not None:
            m.memory_type = req.memory_type
            updates += 1

        if not updates:
            return {"ok": True, "message": "no changes"}

        m.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if req.content is not None:
            # FTS 同事务同步（ADR-0008 强一致：失败随事务回滚，不再吞异常静默）
            from lantai.storage.fts import sync_fts

            sync_fts(session, m.id, m.content)
        session.commit()

        payload = {"ok": True, "message": f"updated {updates} field(s)"}
        if req.content is not None:
            # 向量重同步 best-effort，结果如实回报（旧 vs.update 不存在被 except 静默吞，隐患修复）
            from lantai.services import record_ops_service

            vector_synced = record_ops_service.sync_vector_upsert(memory_id, m)
            payload["vector_synced"] = vector_synced
            if not vector_synced:
                payload["warnings"] = ["vector resync failed; content updated in SQL/FTS only"]
        return payload
    finally:
        session.close()


@router.delete("/terminal/memory/{memory_id}")
def delete_memory(memory_id: str, principal=Depends(get_current_user)):
    """删除单条记忆（含归属校验，P0 票04；笔削：同步结果如实回报 + 无正文审计，ADR-0047）"""
    session, conn = get_db_conn()
    try:
        from lantai.core.acl import ensure_can_delete
        from lantai.models.tables import MemoryItem
        from lantai.services import record_ops_service
        from lantai.storage.fts import sync_fts

        m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
        if not m:
            raise HTTPException(404, "memory not found")
        ensure_can_delete(
            principal, resource_user_id=m.user_id, resource_tenant_id=m.tenant_id, lane=m.lane
        )
        warnings: list[str] = []
        try:
            sync_fts(session, memory_id, None)
            fts_removed = True
        except Exception:
            logger.exception("delete: fts removal failed (reported, not silent)")
            fts_removed = False
            warnings.append("fts removal failed")
        vector_removed = record_ops_service.sync_vector_delete(memory_id)
        if not vector_removed:
            warnings.append("vector delete failed")
        try:
            # 审计 best-effort（票05）：失败不阻断删除主语义，如实进 warnings
            record_ops_service.audit_event(
                session,
                memory_id=memory_id,
                action="delete",
                actor=principal.user_id or "",
                content=m.content or "",
                version_at=m.version or 0,
            )
        except Exception:
            logger.exception("delete: audit write failed (reported, not silent)")
            warnings.append("audit write failed; deletion itself succeeded")
        session.delete(m)
        session.commit()

        return {
            "ok": True,
            "fts_removed": fts_removed,
            "vector_removed": vector_removed,
            "warnings": warnings,
        }
    finally:
        session.close()


def _memory_or_404(session, memory_id: str):
    from lantai.models.tables import MemoryItem

    m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
    if not m:
        raise HTTPException(404, "memory not found")
    return m


def _check_ownership(session, memory_id: str, principal):
    """路由级 404 + 归属校验（笔削四操作共用口径，P0 票04）。"""
    from lantai.core.acl import ensure_can_delete

    m = _memory_or_404(session, memory_id)
    ensure_can_delete(
        principal, resource_user_id=m.user_id, resource_tenant_id=m.tenant_id, lane=m.lane
    )


@router.post("/terminal/memory/{memory_id}/retract")
def retract_memory_route(memory_id: str, req: RetractReq, principal=Depends(get_current_user)):
    """撤回（笔削·削，ADR-0047）：主张停止使用，全检索面禁用；不可自动复活（unretract 仅 admin）。"""
    from lantai.services import record_ops_service

    if not (req.reason or "").strip():
        raise HTTPException(422, "reason is required for retraction")
    session, conn = get_db_conn()
    try:
        _check_ownership(session, memory_id, principal)
    finally:
        session.close()
    result = record_ops_service.retract_memory(
        memory_id, reason=req.reason.strip(), actor=principal.user_id or ""
    )
    if not result["ok"]:
        raise HTTPException(
            404 if result["error"] == "memory not found" else 409, result["error"]
        )
    return result


@router.post("/terminal/memory/{memory_id}/unretract")
def unretract_memory_route(memory_id: str, principal=Depends(get_current_user)):
    """撤销撤回（笔削，仅 admin；误撤回后的人工恢复口）"""
    from lantai.services import record_ops_service

    if not getattr(principal, "is_admin", False):
        raise HTTPException(403, "unretract requires admin")
    result = record_ops_service.unretract_memory(memory_id, actor=principal.user_id or "")
    if not result["ok"]:
        raise HTTPException(
            404 if result["error"] == "memory not found" else 409, result["error"]
        )
    return result


@router.post("/terminal/memory/{memory_id}/archive")
def archive_memory_route(
    memory_id: str, req: ReasonReq | None = None, principal=Depends(get_current_user)
):
    """归档（笔削·藏，ADR-0047）：可逆退出常规检索"""
    from lantai.services import record_ops_service

    session, conn = get_db_conn()
    try:
        _check_ownership(session, memory_id, principal)
    finally:
        session.close()
    result = record_ops_service.archive_memory(
        memory_id, actor=principal.user_id or "", reason=(req.reason if req else "") or ""
    )
    if not result["ok"]:
        raise HTTPException(
            404 if result["error"] == "memory not found" else 409, result["error"]
        )
    return result


@router.post("/terminal/memory/{memory_id}/unarchive")
def unarchive_memory_route(
    memory_id: str, req: ReasonReq | None = None, principal=Depends(get_current_user)
):
    """恢复归档（笔削，可逆侧）"""
    from lantai.services import record_ops_service

    session, conn = get_db_conn()
    try:
        _check_ownership(session, memory_id, principal)
    finally:
        session.close()
    result = record_ops_service.unarchive_memory(
        memory_id, actor=principal.user_id or "", reason=(req.reason if req else "") or ""
    )
    if not result["ok"]:
        raise HTTPException(
            404 if result["error"] == "memory not found" else 409, result["error"]
        )
    return result


@router.post("/terminal/memory/{memory_id}/correct")
def correct_memory_route(memory_id: str, req: CorrectReq, principal=Depends(get_current_user)):
    """纠错（笔削·笔，ADR-0047）：就地改文并保留版本历史（旧文进 provenance.corrections）"""
    from lantai.services import record_ops_service

    session, conn = get_db_conn()
    try:
        _check_ownership(session, memory_id, principal)
    finally:
        session.close()
    result = record_ops_service.correct_memory(
        memory_id,
        new_content=req.content,
        reason=req.reason or "",
        actor=principal.user_id or "",
    )
    if not result["ok"]:
        raise HTTPException(
            404 if result["error"] == "memory not found" else 409, result["error"]
        )
    return result


@router.post("/terminal/merge")
def merge_memories(req: MergeReq, principal=Depends(get_current_user)):
    """合并两条记忆：将 source 的内容追加到 target，然后删除 source（归属校验，P0 票04）"""
    session, conn = get_db_conn()
    try:
        from lantai.core.acl import ensure_can_delete
        from lantai.models.tables import MemoryItem

        src = session.query(MemoryItem).filter(MemoryItem.id == req.source_id).first()
        tgt = session.query(MemoryItem).filter(MemoryItem.id == req.target_id).first()
        if not src:
            raise HTTPException(404, f"source memory {req.source_id} not found")
        if not tgt:
            raise HTTPException(404, f"target memory {req.target_id} not found")
        for m in (src, tgt):
            ensure_can_delete(
                principal, resource_user_id=m.user_id, resource_tenant_id=m.tenant_id, lane=m.lane
            )

        merged_content = f"{tgt.content}\n---\n{src.content}"
        new_importance = max(tgt.importance or 0.8, src.importance or 0.8)

        tgt.content = merged_content
        tgt.importance = new_importance
        tgt.version = (tgt.version or 1) + 1

        session.delete(src)

        # 创建 supersedes 边记录
        from lantai.services.edge_service import add_edge

        with contextlib.suppress(Exception):
            add_edge(req.target_id, req.source_id, "supersedes", 1.0)

        session.commit()
        return {"ok": True, "merged_into": req.target_id, "content": merged_content}
    finally:
        session.close()
