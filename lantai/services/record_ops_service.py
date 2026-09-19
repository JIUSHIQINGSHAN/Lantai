"""笔削——撤回/删除四分法核心操作（ADR-0047）。

四分语义单一真源：纠错 correct / 撤回 retract / 归档 archive / 删除 delete
（unretract / unarchive 为可逆侧；admin 限定在路由层判定）。约束：

- 同步失败不静默：FTS/向量同步结果如实进返回值（fts_* / vector_* / warnings）；
  SQL `status` 是检索权威过滤面（hybrid 既有谓词），索引纵深失败不阻断主语义
  （宁 miss 不脏写——响应可见，不假装干净）。
- 审计不含正文：MemoryAuditEvent 只记 hash 与长度，永不存 content。
- 不复活：本模块不提供任何把 retracted 自动翻回 active 的后台路径。
"""

import hashlib
import logging

from sqlmodel import Session

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryAuditEvent, MemoryItem
from lantai.llm.client import embed
from lantai.storage import db
from lantai.storage.fts import sync_fts

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_RETRACTED = "retracted"

AUDIT_ACTIONS = ("correct", "retract", "unretract", "archive", "unarchive", "delete")

_NOT_FOUND = {"ok": False, "error": "memory not found"}


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def audit_event(
    session: Session,
    *,
    memory_id: str,
    action: str,
    actor: str = "",
    reason: str = "",
    content: str = "",
    version_at: int = 0,
) -> None:
    """写一条笔削审计（不含正文，只记 hash/长度）；action 必须在枚举内。"""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action: {action}")
    session.add(
        MemoryAuditEvent(
            id=new_id("aud"),
            memory_id=memory_id,
            action=action,
            actor=actor or "",
            reason=reason or "",
            content_hash=_content_hash(content) if content else "",
            content_len=len(content),
            version_at=version_at,
        )
    )


def sync_vector_delete(memory_id: str) -> bool:
    """向量库删除（best-effort）；失败返回 False 由调用方如实上报。"""
    try:
        from lantai.retrieval.hybrid import delete_memory_item

        delete_memory_item(memory_id)
        return True
    except Exception:
        logger.exception("vector delete failed (reported, not silent)")
        return False


def sync_vector_upsert(memory_id: str, item: MemoryItem) -> bool:
    """向量库重同步（upsert 语义）；失败返回 False 由调用方如实上报。"""
    try:
        from lantai.retrieval.hybrid import index_memory_item

        index_memory_item(
            memory_id,
            embed([item.content]),
            {
                "key": item.key or "",
                "memory_type": item.memory_type,
                "lane": item.lane or "general",
                "domain": getattr(item, "domain", "user") or "user",
            },
        )
        return True
    except Exception:
        logger.exception("vector upsert failed (reported, not silent)")
        return False


def _sync_fts(s: Session, memory_id: str, content: str | None) -> tuple[bool, list[str]]:
    """同事务 FTS 同步（content=None=删行）；失败如实回报不静默。"""
    try:
        sync_fts(s, memory_id, content)
        return True, []
    except Exception:
        logger.exception("fts sync failed (reported, not silent)")
        return False, ["fts sync failed; SQL status filter remains authoritative"]


def retract_memory(
    memory_id: str, *, reason: str, actor: str = "", session: Session | None = None
) -> dict:
    """撤回（削）：主张停止使用，全检索面禁用；行保留供审计；不可自动复活。幂等。"""

    def _run(s: Session) -> dict:
        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)
        if item.status == STATUS_RETRACTED:
            return {
                "ok": True,
                "already_retracted": True,
                "fts_removed": True,
                "vector_removed": True,
                "warnings": [],
            }
        warnings: list[str] = []
        fts_removed, fts_warn = _sync_fts(s, memory_id, None)
        warnings.extend(fts_warn)
        vector_removed = sync_vector_delete(memory_id)
        if not vector_removed:
            warnings.append("vector delete failed; SQL status filter remains authoritative")
        item.status = STATUS_RETRACTED
        item.updated_at = utcnow()
        audit_event(
            s,
            memory_id=memory_id,
            action="retract",
            actor=actor,
            reason=reason,
            content=item.content,
            version_at=item.version,
        )
        s.commit()
        return {
            "ok": True,
            "fts_removed": fts_removed,
            "vector_removed": vector_removed,
            "warnings": warnings,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def unretract_memory(
    memory_id: str, *, actor: str = "", reason: str = "", session: Session | None = None
) -> dict:
    """撤销撤回（仅 admin，路由层判定）：status→active 并重同步索引；同步失败如实回报。"""

    def _run(s: Session) -> dict:
        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)
        if item.status != STATUS_RETRACTED:
            return {"ok": False, "error": f"memory is not retracted (status={item.status})"}
        warnings: list[str] = []
        fts_synced, fts_warn = _sync_fts(s, memory_id, item.content)
        warnings.extend(fts_warn)
        vector_synced = sync_vector_upsert(memory_id, item)
        if not vector_synced:
            warnings.append("vector resync failed; memory searchable via FTS only")
        item.status = STATUS_ACTIVE
        item.updated_at = utcnow()
        audit_event(
            s,
            memory_id=memory_id,
            action="unretract",
            actor=actor,
            reason=reason,
            content=item.content,
            version_at=item.version,
        )
        s.commit()
        return {
            "ok": True,
            "fts_synced": fts_synced,
            "vector_synced": vector_synced,
            "warnings": warnings,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def archive_memory(
    memory_id: str, *, actor: str = "", reason: str = "", session: Session | None = None
) -> dict:
    """归档（藏）：可逆退出常规检索；FTS/Chroma 保留（复原零成本），靠 SQL 过滤。幂等。"""

    def _run(s: Session) -> dict:
        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)
        if item.status == STATUS_ARCHIVED:
            return {"ok": True, "already_archived": True}
        if item.status == STATUS_RETRACTED:
            return {"ok": False, "error": "retracted memory cannot be archived"}
        item.status = STATUS_ARCHIVED
        item.updated_at = utcnow()
        audit_event(
            s,
            memory_id=memory_id,
            action="archive",
            actor=actor,
            reason=reason,
            content=item.content,
            version_at=item.version,
        )
        s.commit()
        return {"ok": True}

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def unarchive_memory(
    memory_id: str, *, actor: str = "", reason: str = "", session: Session | None = None
) -> dict:
    """恢复归档：archived→active；仅归档态可恢复。"""

    def _run(s: Session) -> dict:
        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)
        if item.status != STATUS_ARCHIVED:
            return {"ok": False, "error": f"memory is not archived (status={item.status})"}
        item.status = STATUS_ACTIVE
        item.updated_at = utcnow()
        audit_event(
            s,
            memory_id=memory_id,
            action="unarchive",
            actor=actor,
            reason=reason,
            content=item.content,
            version_at=item.version,
        )
        s.commit()
        return {"ok": True}

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def correct_memory(
    memory_id: str,
    *,
    new_content: str,
    reason: str = "",
    actor: str = "",
    session: Session | None = None,
) -> dict:
    """纠错（笔）：就地改文并保留版本历史（旧文进 provenance.corrections）。"""

    def _run(s: Session) -> dict:
        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)
        text = (new_content or "").strip()
        if not text:
            return {"ok": False, "error": "content is required"}
        if text == item.content:
            return {"ok": False, "error": "content unchanged"}
        if item.status == STATUS_RETRACTED:
            return {"ok": False, "error": "retracted memory cannot be corrected"}
        warnings: list[str] = []
        prov = dict(item.provenance or {})
        corrections = list(prov.get("corrections") or [])
        corrections.append(
            {
                "old_content": item.content,
                "reason": reason,
                "actor": actor,
                "at": utcnow().isoformat(),
                "from_version": item.version,
            }
        )
        prov["corrections"] = corrections
        old_content = item.content
        item.provenance = prov
        item.version = (item.version or 1) + 1
        item.content = text
        item.updated_at = utcnow()
        fts_synced, fts_warn = _sync_fts(s, memory_id, text)
        warnings.extend(fts_warn)
        vector_synced = sync_vector_upsert(memory_id, item)
        if not vector_synced:
            warnings.append("vector resync failed; content updated in SQL/FTS only")
        audit_event(
            s,
            memory_id=memory_id,
            action="correct",
            actor=actor,
            reason=reason,
            content=old_content,
            version_at=item.version,
        )
        s.commit()
        return {
            "ok": True,
            "version": item.version,
            "fts_synced": fts_synced,
            "vector_synced": vector_synced,
            "warnings": warnings,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)
