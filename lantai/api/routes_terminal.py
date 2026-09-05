"""Glass-box Terminal：透明化对话与记忆透视 SSE 端点"""
import contextlib
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/terminal/chat")
async def terminal_chat_stream(req: ChatReq):
    """SSE 流式端点：透明化展示记忆检索全过程"""
    from lantai.services.gate_service import check_gate
    from lantai.services.search_service import search_memories

    def generate():
        # Step 1: 闸门判定
        yield _sse("step", {"phase": "gate_check", "message": f"正在判定查询意图: '{req.query}'"})
        try:
            gate = check_gate(req.query)
            gate_data = {"needs_memory": gate.get("needs_memory", True),
                         "reason": gate.get("reason", "")}
        except Exception as e:
            gate_data = {"needs_memory": True, "reason": f"闸门异常({e})，默认放行"}
        yield _sse("gate", gate_data)

        # Step 2: 记忆检索
        yield _sse("step", {"phase": "retrieval", "message": f"正在执行四路检索 (top_k={req.top_k})..."})
        try:
            results = search_memories(
                query=req.query,
                top_k=req.top_k,
                force=req.force,
                domain=req.domain if req.domain != "all" else None,
            )
            items = results.get("results", [])
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
                for e in (node_edges if isinstance(node_edges, list) else []):
                    edges.append({
                        "id": e.get("id", ""),
                        "source": e.get("source_memory_id", ""),
                        "target": e.get("target_memory_id", ""),
                        "relation": e.get("relation", "related"),
                        "confidence": e.get("confidence", 0.5),
                    })
            except Exception:
                pass
        yield _sse("edges", {"edges": edges})

        # Step 5: 完成
        yield _sse("complete", {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "message": f"检索完成：命中 {len(nodes)} 条记忆，{len(edges)} 条关系边",
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/terminal/graph")
def terminal_graph(domain: str = "", limit: int = 100):
    """获取记忆图谱数据（节点 + 边）用于 D3 力导向图"""
    memories = list_memories(lane="", status="", decay_class="",
                             memory_type="", limit=limit, offset=0)
    items = memories.get("memories") or memories.get("items") or []
    if domain:
        items = [m for m in items if m.get("domain") == domain]

    nodes = []
    for m in items:
        nodes.append({
            "id": m.get("id", ""),
            "content": (m.get("content", "") or "")[:80],
            "domain": m.get("domain", "user"),
            "lane": m.get("lane", "general"),
            "importance": m.get("importance", 0.8),
            "decay_score": m.get("decay_score", 1.0),
            "confidence": m.get("confidence", 0.9),
            "memory_type": m.get("memory_type", "semantic"),
            "version": m.get("version", 1),
        })

    edges = []
    seen = set()
    for n in nodes[:50]:
        try:
            node_edges = list_edges(n["id"])
            for e in (node_edges if isinstance(node_edges, list) else []):
                eid = e.get("id", "")
                if eid not in seen:
                    seen.add(eid)
                    edges.append({
                        "id": eid,
                        "source": e.get("source_memory_id", ""),
                        "target": e.get("target_memory_id", ""),
                        "relation": e.get("relation", "related"),
                        "confidence": e.get("confidence", 0.5),
                    })
        except Exception:
            pass

    return {"nodes": nodes, "edges": edges}


@router.get("/terminal/memory/{memory_id}")
def get_single_memory(memory_id: str):
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
def update_memory(memory_id: str, req: MemoryUpdateReq):
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

        session.commit()
        return {"ok": True, "message": f"updated {updates} field(s)"}
    finally:
        session.close()


@router.delete("/terminal/memory/{memory_id}")
def delete_memory(memory_id: str):
    """删除单条记忆"""
    session, conn = get_db_conn()
    try:
        from lantai.models.tables import MemoryItem
        m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
        if not m:
            raise HTTPException(404, "memory not found")
        session.delete(m)
        session.commit()
        return {"ok": True}
    finally:
        session.close()


@router.post("/terminal/merge")
def merge_memories(req: MergeReq):
    """合并两条记忆：将 source 的内容追加到 target，然后删除 source"""
    session, conn = get_db_conn()
    try:
        from lantai.models.tables import MemoryItem
        src = session.query(MemoryItem).filter(MemoryItem.id == req.source_id).first()
        tgt = session.query(MemoryItem).filter(MemoryItem.id == req.target_id).first()
        if not src:
            raise HTTPException(404, f"source memory {req.source_id} not found")
        if not tgt:
            raise HTTPException(404, f"target memory {req.target_id} not found")

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
