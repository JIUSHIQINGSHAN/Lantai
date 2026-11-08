"""Glass-box Terminal：透明化对话与记忆透视 SSE 端点"""

import json
import math
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.services.edge_service import list_edges_batch
from lantai.services.memory_service import list_memories
from lantai.storage.cas import atomic_update
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
        # 最多查前 20 个节点的边。批量一次取回（此前逐节点 `list_edges`，N+1
        # 次查询）；归属口径同 `list_edges`（票 .scratch/readside-gaps/22），
        # 结果按节点顺序展开，与逐个调用的输出逐字一致。
        edge_nodes = nodes[:20]
        try:
            by_node = list_edges_batch([n["id"] for n in edge_nodes], principal=principal)
        except Exception:
            logger.exception("terminal chat: edge batch lookup failed, degrade to no edges")
            by_node = {}
        for n in edge_nodes:
            for e in by_node.get(n["id"]) or []:
                edges.append(
                    {
                        "id": e.get("id", ""),
                        "source": e.get("source", ""),
                        "target": e.get("target", ""),
                        "relation": e.get("relation", "related"),
                        "confidence": e.get("confidence", 0.5),
                    }
                )
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
    # 前 50 个节点的边批量一次取回（此前逐节点 `list_edges`，N+1 次查询）。
    # 归属同 `terminal_chat_stream` Step 4（票 .scratch/readside-gaps/22）。
    edge_nodes = nodes[:50]
    try:
        by_node = list_edges_batch([n["id"] for n in edge_nodes], principal=principal)
    except Exception:
        logger.exception("terminal graph: edge batch lookup failed, degrade to no edges")
        by_node = {}
    for n in edge_nodes:
        for e in by_node.get(n["id"]) or []:
            eid = e.get("id", "")
            if eid not in seen:
                seen.add(eid)
                edges.append(
                    {
                        "id": eid,
                        "source": e.get("source", ""),
                        "target": e.get("target", ""),
                        "relation": e.get("relation", "related"),
                        "confidence": e.get("confidence", 0.5),
                    }
                )

    return {"nodes": nodes, "edges": edges}


@router.get("/terminal/memory/{memory_id}")
def get_single_memory(memory_id: str, principal=Depends(get_current_user)):
    """获取单条记忆的完整详情（归属校验，票 `.scratch/readside-gaps/24`）。

    修前签名里有 `principal`、函数体却一次都没引用——同文件另外 9 个
    `/terminal/memory/*` 路由全过 `_check_ownership`（笔削四操作在 P0 票04
    统一过），只有这条读路由漏了。后果：A 拿 B 的 memory_id 一次请求拿到
    **完整 41 字段**（content 全文、structure/provenance/tags JSON、
    confidence/importance/decay、lifecycle_status、superseded_by）。

    **这条比"没有 Depends"更危险**：代码审查看到
    `principal=Depends(get_current_user)` 就会打勾。路线普查 193 个入口里
    它是唯一一条"签名有、函数体不用"的形状。
    """
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
        # 归属校验（票 24）：复用笔削四操作的口径，不发明第二份判据。
        # `_check_ownership` 内部自己会 `_memory_or_404`，所以上下两处
        # 404 判据可以合并——但**上面那个裸 SQL 查询保留不动**：它不是安全
        # 问题，改它有回归风险。
        _check_ownership(session, memory_id, principal)
        return m.model_dump()
    finally:
        session.close()


@router.put("/terminal/memory/{memory_id}")
def update_memory(memory_id: str, req: MemoryUpdateReq, principal=Depends(get_current_user)):
    """更新单条记忆的内容、重要性或置信度（归属校验 + retracted 拒改，现状整改票 03）"""
    session, conn = get_db_conn()
    try:
        from lantai.core.acl import ensure_can_delete
        from lantai.models.tables import MemoryItem

        m = session.query(MemoryItem).filter(MemoryItem.id == memory_id).first()
        if not m:
            raise HTTPException(404, "memory not found")
        # 与 DELETE 同一真源同一口径（P0 票04）：无归属/泳道越权一律 403
        ensure_can_delete(
            principal, resource_user_id=m.user_id, resource_tenant_id=m.tenant_id, lane=m.lane
        )
        # 撤回不变量（ADR-0047 D23）：retracted 改文会把已清空的 FTS/向量面重新填回
        if m.status == "retracted":
            raise HTTPException(409, "retracted memory cannot be updated")

        updates = 0
        if req.content is not None:
            # 獬豸：终端编辑与 merge / correct_memory 同族——新正文会重嵌入、进 FTS，
            # 下一轮就被 Hermes 注入。v0.64 只给 merge 补了闸，编辑口子仍敞开
            # （2026-10-31 实测：PUT 写入「忽略之前的所有指令…」返回 200 并同步向量）
            from lantai.security.injection_guard import assert_no_prompt_injection

            try:
                assert_no_prompt_injection(req.content)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            m.content = req.content
            updates += 1
        if req.importance is not None:
            # 票 35：importance 与保持强度公式直连（forgetting._lane_strength），无界
            # 负值让遗忘引擎确定性崩溃——更新面拒绝越界（422，宁 miss 不脏写）。
            # 不走 pydantic Field(ge/le)：FastAPI 422 响应体以 allow_nan=False 渲染，
            # 验证错误的 input 携带 NaN/inf 时 422 自身会渲染炸 500，故在 handler
            # 显式拒（与本文件 assert_no_prompt_injection 的 422 同一惯语）。
            if not math.isfinite(req.importance) or not (0.0 <= req.importance <= 1.0):
                raise HTTPException(422, "importance 必须是 [0.0, 1.0] 内的有限数值（票 35）")
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

        m.updated_at = utcnow()
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

        # 完璧级联（X-01）：事实与记忆同一事务；级联失败整体回滚并报 500，记忆不删
        try:
            record_ops_service.purge_facts_for_memory(session, memory_id)
            session.delete(m)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("delete: cascade fact deletion failed, rolled back")
            raise HTTPException(
                500, "cascade fact deletion failed; memory NOT deleted (rolled back)"
            ) from exc

        # 向量库在 SQL 提交成功后再删（与 promoter.delete_memory 同序）
        vector_removed = record_ops_service.sync_vector_delete(memory_id)
        if not vector_removed:
            warnings.append("vector delete failed")

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
    """路由级 404 + 归属校验（笔削四操作共用口径，P0 票04）。

    **读路由同样适用**（票 24）：`ensure_can_delete` 名字带 delete，但它只做
    「资源是否属于这个主体」的归属判定（`core/acl.py:117-126` 的 docstring 明说
    "删除等破坏性操作的资源归属校验"，实现里没有任何删除动作）。`get_single_memory`
    就是复用它的一条读路由——**不要因为名字里带 delete 就另写一套判据**，
    那正是票 05 `_monitor_full_view` 口径漂移的成因。
    """
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
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
    return result


@router.post("/terminal/memory/{memory_id}/unretract")
def unretract_memory_route(memory_id: str, principal=Depends(get_current_user)):
    """撤销撤回（笔削，仅 admin；误撤回后的人工恢复口）"""
    from lantai.services import record_ops_service

    if not getattr(principal, "is_admin", False):
        raise HTTPException(403, "unretract requires admin")
    result = record_ops_service.unretract_memory(memory_id, actor=principal.user_id or "")
    if not result["ok"]:
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
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
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
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
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
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
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
    return result


@router.post("/terminal/memory/{memory_id}/revive-consolidated")
def revive_consolidated_route(memory_id: str, req: RetractReq, principal=Depends(get_current_user)):
    """起复（笔削家族第七操作，ADR-0052）：巩固撤销面——碎片恢复 active 或主记忆撤销全簇。

    幂等：已撤销簇返回 already_revoked、已起复碎片返回 already_active。
    reason 必填（同 retract 口径）：撤销/恢复均须留痕。
    """
    from lantai.services import record_ops_service

    if not (req.reason or "").strip():
        raise HTTPException(422, "reason is required for revival")
    session, conn = get_db_conn()
    try:
        _check_ownership(session, memory_id, principal)
    finally:
        session.close()
    # 归属校验下沉到 service（票 .scratch/mcp-identity-gaps/01b）：上面的
    # `_check_ownership` 保留（路由层原有判据，先拦一道），service 内按同一
    # `ensure_can_delete` 再判一次——MCP 没有路由层，靠 service 那道兜住
    result = record_ops_service.revive_consolidated(
        memory_id, reason=req.reason.strip(), actor=principal.user_id or "", principal=principal
    )
    if not result["ok"]:
        raise HTTPException(404 if result["error"] == "memory not found" else 409, result["error"])
    return result


@router.post("/terminal/merge")
def merge_memories(req: MergeReq, principal=Depends(get_current_user)):
    """合并两条记忆：将 source 的内容追加到 target，然后删除 source（归属校验，P0 票04）

    X-03 防呆（v0.63 票 01）：source == target 时旧逻辑先自追加再
    `session.delete(src)`，把记忆物理删除——数据静默丢失。必须最先拦截，
    先于任何 DB 读取与写操作。
    """
    if req.source_id == req.target_id:
        raise HTTPException(400, "cannot merge a memory into itself")
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

        # 獬豸（X-03 同票闸门补全）：合并产生的新正文会落 FTS+向量并在
        # 检索面呈现——与 correct_memory 同族同闸，注入载荷整条拒
        from lantai.security.injection_guard import assert_no_prompt_injection

        try:
            assert_no_prompt_injection(merged_content)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

        # 票 23：tgt 版本入口 CAS（写阶段第一条 DML，对最新已提交状态求值）——
        # 读与写回之间 tgt 被并发纠错推进时，陈旧合并会把对方 correction 连同
        # 正文整体抹掉、版本重号（红测 terminal merge）。败方 409（路由既有
        # 冲突形状），重试即按最新版本重新合并。
        tgt_entry_version = tgt.version or 1
        claimed = atomic_update(
            session,
            MemoryItem,
            tgt.id,
            {"version": tgt_entry_version + 1},
            MemoryItem.version == tgt_entry_version,
        )
        if not claimed:
            raise HTTPException(409, "target memory changed concurrently; retry merge")

        tgt.content = merged_content
        tgt.importance = new_importance
        tgt.version = tgt_entry_version + 1
        src_id, tgt_id = src.id, tgt.id

        # FTS 同事务强一致（ADR-0008，票 20）：合并改文 + 删源各同步一份索引。
        # 改文若索引失同步会留下「SQL 新文、FTS 旧文」的幽灵正文——全仓改文路径
        # （correct_memory/tanyi/probing/promoter/rollback）全部双层同步，merge
        # 是唯一例外，本票收口；失败直接抛出随事务回滚，不静默降级。
        from lantai.storage.fts import sync_fts

        sync_fts(session, tgt_id, merged_content)
        sync_fts(session, src_id, None)

        # 完璧血统（X-01）：source 的事实改挂到 target，避免 source 删除后留下孤儿事实
        from lantai.services import record_ops_service

        record_ops_service.repoint_facts(session, src_id, tgt_id)

        # src 双向边清理 + supersedes 边收编（票 20，口径＝票面修复目标 2/3）：
        # supersedes(tgt→src) 是合并血缘，保留；其余 src 双向边是端点已物理删除
        # 的死拓扑，随合并删除。supersedes 边自 create_edge 独立 session 收编进
        # 合并同事务（promoter.py:401-408 同款 in-session 构造 + dup 查重）——
        # 原实现独立 commit 先于合并 commit 且被 contextlib.suppress 包裹：
        # 合并 commit 失败回滚会留幽灵 supersedes 边（两行 active 血缘在场，
        # _apply_supersedes_order 据此降权），反向则血缘缺失无痕。
        from sqlmodel import or_, select

        from lantai.core.ids import new_id
        from lantai.models.tables import MemoryEdge

        stale_edges = session.exec(
            select(MemoryEdge).where(
                or_(MemoryEdge.source_memory_id == src_id, MemoryEdge.target_memory_id == src_id)
            )
        ).all()
        for e in stale_edges:
            if (
                e.relation == "supersedes"
                and e.source_memory_id == tgt_id
                and e.target_memory_id == src_id
            ):
                continue  # 合并血缘（含历史既有边），保留
            session.delete(e)

        dup = session.exec(
            select(MemoryEdge).where(
                MemoryEdge.relation == "supersedes",
                MemoryEdge.source_memory_id == tgt_id,
                MemoryEdge.target_memory_id == src_id,
            )
        ).first()
        if dup is None:
            session.add(
                MemoryEdge(
                    id=new_id("edge"),
                    source_memory_id=tgt_id,
                    target_memory_id=src_id,
                    relation="supersedes",
                    confidence=1.0,
                    # 归属随 principal 落列（票 .scratch/ownership-gaps/03）：
                    # 合并边不带属主，DELETE 的 ACL 与审计都无从下手
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                    agent_id=principal.agent_id,
                    session_id=principal.session_id,
                )
            )

        session.delete(src)
        session.commit()

        # 向量同步（票 20）：commit 后执行（promoter 序——embed/向量调用不占数据
        # 库事务），失败如实回报 warnings 不静默（correct_memory :452-454 形态）。
        warnings: list[str] = []
        try:
            from lantai.retrieval.hybrid import delete_memory_item

            delete_memory_item(src_id)
        except Exception:
            logger.exception("vector delete of merged source failed (reported, not silent)")
            warnings.append("vector delete of source failed; source removed in SQL/FTS only")
        if not record_ops_service.sync_vector_upsert(tgt_id, tgt):
            warnings.append("vector resync of target failed; content updated in SQL/FTS only")
        return {
            "ok": True,
            "merged_into": req.target_id,
            "content": merged_content,
            "warnings": warnings,
        }
    finally:
        session.close()
