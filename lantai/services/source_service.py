"""来源与候选 service 层"""

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.models.schemas import SourceReq
from lantai.models.tables import MemoryCandidate, Source
from lantai.storage import db
from lantai.workers.ingest_worker import run_ingest_once

# 敏感键子串（大小写不敏感）：来源 config 里这些键的值不回显。
# 宁 miss 不脏写——只认已知凭证形态，**不认识的键原样返回**，
# 不做猜测式脱敏（猜错会把正常配置也抹掉，比泄漏更难排查）。
_SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
)

_REDACTED = "***"


def redact_config(config) -> dict:
    """脱敏来源配置：已知敏感键的值替换为 `***`，其余原样。

    独立于归属的第二道防线（票 10 口径 4）：即使归属修好、
    即使请求方是 admin，`GET /sources` 也不该把连接凭证明文回显。
    非 dict 输入原样返回（宁 miss 不脏写：不猜结构）。
    """
    if not isinstance(config, dict):
        return config
    out = {}
    for key, value in config.items():
        k = str(key).lower()
        if any(part in k for part in _SENSITIVE_KEY_PARTS):
            out[key] = _REDACTED
        elif isinstance(value, dict):
            out[key] = redact_config(value)  # 嵌套配置同样脱敏
        else:
            out[key] = value
    return out


def _source_scope(principal):
    """读侧归属条件：admin/`principal=None` → None（不过滤）；
    否则 `user_id == viewer OR IS NULL`（票 10，NULL 口径同票 03/04/06/09）。"""
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (Source.user_id == viewer) | (Source.user_id.is_(None))


def add_source(req: SourceReq, principal=None) -> dict:
    """创建来源。

    归属（票 10）：新建落 `principal.user_id`，否则又是一条谁都能
    看见的无主来源。`principal=None` 的内部调用留 NULL，
    与改动前逐字一致。
    """
    with db.get_session() as s:
        src = Source(
            id=new_id("src"),
            kind=req.kind,
            config=req.config,
            enabled=req.enabled,
            user_id=getattr(principal, "user_id", None),
            tenant_id=getattr(principal, "tenant_id", None),
        )
        s.add(src)
        s.commit()
        s.refresh(src)
        return src.model_dump(mode="json")


def list_sources(principal=None) -> dict:
    """列出来源（按归属收窄 + config 脱敏）。"""
    with db.get_session() as s:
        q = select(Source)
        scope = _source_scope(principal)
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q).all()
        out = []
        for r in rows:
            dumped = r.model_dump(mode="json")
            dumped["config"] = redact_config(dumped.get("config"))
            out.append(dumped)
        return {"sources": out}


def run_ingest() -> dict:
    """运行摄取 worker。"""
    run_ingest_once()
    return {"ok": True}


def list_candidates(status: str = "new", limit: int = 20, principal=None) -> dict:
    """列出候选记忆。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 2）：此前一个身份都不取，
    `select(MemoryCandidate).where(status==status)` 全表捞，把别人的
    `summary` / `claims` / `contradictions` 明文连同 `id` 一起吐给任何持 key 者
    （第三轮探测 `OBSERVED3.txt` 实证）。`MemoryCandidate` **有**完整归属四元组
    （`tables.py:73-78`），所以这里能直接按 viewer 收窄，不需要过渡推导。

    口径与 Ticket 02 的 `list_pending_candidates` **逐字一致**——复用
    `work_item_service._owner_scope` 单一判据：admin/system 全权；
    否则严格 `user_id = viewer`，**NULL 老行不可见**。候选正文是高敏字段，
    这是「宁 miss 不脏写」的选择：宁可少列，不可错放。
    （真实库 37 行候选全部 `user_id='default'`，单用户部署下列的就是自己的。）

    `principal=None`（内部 CLI/worker/MCP）不加过滤，与改动前逐字一致。
    """
    from lantai.services.work_item_service import _owner_scope

    with db.get_session() as s:
        q = select(MemoryCandidate).where(MemoryCandidate.status == status)
        scope = _owner_scope(MemoryCandidate.user_id, principal)
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q.limit(limit)).all()
        return {"candidates": [r.model_dump(mode="json") for r in rows]}


from lantai.evolution.promoter import delete_memory
from lantai.models.tables import DocumentChunk, MemoryEdge, MemoryItem, RawDocument


def delete_document(document_id: str, principal=None) -> dict:
    """Cascade delete a document and its exclusively derived memories.

    归属（票 `.scratch/readside-gaps/18`）：文档属于 A，**不等于**它指向的
    记忆也属于 A——边的另一端是另一张表的另一行，路由那道 `ensure_can_delete`
    只校验 `RawDocument` 行，够不着这里。票 17 修前 `MemoryEdge` 无归属过滤、
    票 16 的无归属 apply 也会造跨用户边，所以这条路径真的能把 B 的记忆删掉。

    **宁 miss 不脏写**：任一条目标记忆不属于当前主体 → 整次级联中止，
    且必须在**任何删除发生之前**中止。原实现在第 4 步就 `s.commit()` 了
    （文档/分块/候选/边全落库），那时再发现越权已是半途而废——所以这里
    先把所有权校验做完，再动手删。
    """
    with db.get_session() as s:
        # 1. 读文档（先只读不删）
        doc = s.get(RawDocument, document_id)

        # 2. 找边，算出目标记忆集
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.source_memory_id == document_id)).all()
        mem_ids_to_check = {e.target_memory_id for e in edges}

        # 3. **删任何东西之前**，逐条校验目标记忆归属
        #    文档属于 A 不意味着它的边指向的记忆也属于 A
        forbidden = []
        for mem_id in sorted(mem_ids_to_check):
            mem = s.get(MemoryItem, mem_id)
            if mem is None:
                continue  # 孤儿边，没有行可删（原行为即跳过）
            if principal is not None:
                from fastapi import HTTPException

                from lantai.core.acl import ensure_can_delete

                try:
                    ensure_can_delete(
                        principal,
                        resource_user_id=mem.user_id,
                        resource_tenant_id=mem.tenant_id,
                        lane=mem.lane,
                    )
                except HTTPException as exc:
                    forbidden.append(f"{mem_id}: {exc.detail}")
        if forbidden:
            # 整体中止——一条都没删，说清是谁拦的
            noun = "memory" if len(forbidden) == 1 else "memories"
            return {
                "ok": False,
                "reason": f"forbidden: cascade aborted, {len(forbidden)} target "
                f"{noun} not owned: " + "; ".join(forbidden[:3]),
                "deleted_document_id": None,
                "deleted_memories": 0,
            }

        # 4. 校验全过，开始删
        if doc:
            s.delete(doc)
        chunks = s.exec(select(DocumentChunk).where(DocumentChunk.document_id == document_id)).all()
        for chunk in chunks:
            s.delete(chunk)
        candidates = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.document_id == document_id)
        ).all()
        for cand in candidates:
            s.delete(cand)
        for e in edges:
            s.delete(e)
        s.commit()  # 边与文档先落库

        # 5. 删除「没有别的 doc 来源」的目标记忆
        deleted_mems = 0
        for mem_id in sorted(mem_ids_to_check):
            remaining_edges = s.exec(
                select(MemoryEdge).where(MemoryEdge.target_memory_id == mem_id)
            ).all()
            doc_sources = [
                e.source_memory_id for e in remaining_edges if e.source_memory_id.startswith("doc_")
            ]
            if not doc_sources:
                out = delete_memory(mem_id, principal=principal)
                if out.get("ok"):
                    deleted_mems += 1
                elif "missing" not in str(out.get("reason", "")):
                    # 已在第 3 步校验过，走到这里只可能是并发变动
                    return {
                        "ok": False,
                        "reason": out.get("reason"),
                        "deleted_document_id": document_id,
                        "deleted_memories": deleted_mems,
                    }

        return {
            "ok": True,
            "deleted_document_id": document_id,
            "deleted_memories": deleted_mems,
        }
