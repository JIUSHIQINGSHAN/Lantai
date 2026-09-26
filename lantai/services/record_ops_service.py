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
from lantai.llm.client import embed
from lantai.models.tables import MemoryAuditEvent, MemoryItem
from lantai.storage import db
from lantai.storage.fts import sync_fts

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_RETRACTED = "retracted"
STATUS_CONSOLIDATED = "consolidated"

AUDIT_ACTIONS = (
    "correct",
    "retract",
    "unretract",
    "archive",
    "unarchive",
    "delete",
    # 起复（ADR-0052）：巩固撤销面两操作——碎片起复 / 主记忆撤销全簇
    "revive",
    "unconsolidate",
)

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
    """向量库重同步（upsert 语义）；失败返回 False 由调用方如实上报。

    metadata 契约：与 memory_service 等 8 键对齐（hybrid 按 user_id/tenant_id 等
    做 Chroma where 过滤——缺键会让重同步后的记忆在属主过滤检索中永久不可见）。
    """
    try:
        from lantai.retrieval.hybrid import index_memory_item

        index_memory_item(
            memory_id,
            embed([item.content])[0],
            {
                "key": item.key or "",
                "memory_type": item.memory_type,
                "lane": item.lane or "general",
                "domain": getattr(item, "domain", "user") or "user",
                "tenant_id": getattr(item, "tenant_id", "") or "",
                "user_id": getattr(item, "user_id", "") or "",
                "session_id": getattr(item, "session_id", "") or "",
                "agent_id": getattr(item, "agent_id", "") or "",
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
            # 幂等重入：不假设首次撤回的索引同步结果，只认状态（如实，不造假 True）
            return {"ok": True, "already_retracted": True, "warnings": []}
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
            if fts_synced:
                warnings.append("vector resync failed; memory searchable via FTS only")
            else:
                warnings.append(
                    "vector resync failed; memory active but not searchable until resync"
                )
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
        if item.status != STATUS_ACTIVE:
            # 仅 active 可归档：candidate 绕晋升闸门、retracted 降级均拒
            return {
                "ok": False,
                "error": f"only active memories can be archived (status={item.status})",
            }
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
    event_time=None,
    event_time_precision: str | None = None,
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
        # 更漏（票 08）：纠错可更正事件时间——I1 校验，旧值随 corrections 留痕
        if event_time is not None or event_time_precision is not None:
            from lantai.core.time_precision import validate_event_time_pair

            new_etp = (
                event_time_precision
                if event_time_precision is not None
                else item.event_time_precision
            )
            parsed_et = event_time
            if isinstance(parsed_et, str):
                from lantai.core.time import parse_iso_utc

                try:
                    parsed_et = parse_iso_utc(parsed_et)
                except ValueError:
                    return {"ok": False, "error": "invalid event_time format"}
            if not validate_event_time_pair(parsed_et, new_etp):
                return {"ok": False, "error": "invalid event_time pair (I1)"}
            corrections[-1]["old_event_time"] = (
                item.event_time.isoformat() if item.event_time else None
            )
            corrections[-1]["old_event_time_precision"] = item.event_time_precision
            item.event_time = parsed_et
            item.event_time_precision = new_etp
        prov["corrections"] = corrections
        old_content = item.content
        old_version = item.version or 1
        item.provenance = prov
        item.version = old_version + 1
        item.content = text
        item.updated_at = utcnow()
        # FTS 同事务强一致（ADR-0008）：改文若索引失同步会留下可命中旧文的脏索引，
        # 属「脏写」——直接抛出随事务回滚，不静默降级（与 PATCH 更新路由同策略）
        sync_fts(s, memory_id, text)
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
            version_at=old_version,
        )
        s.commit()
        return {
            "ok": True,
            "version": item.version,
            "vector_synced": vector_synced,
            "warnings": warnings,
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def revive_consolidated(
    memory_id: str, *, reason: str = "", actor: str = "", session: Session | None = None
) -> dict:
    """起复（ADR-0052）：巩固撤销面——碎片恢复 active，或主记忆撤销全簇。

    输入二义性按形态判别（宁 miss 不脏写，不猜用户意图）：
      - **主记忆**（`source_ids` 非空，promoter 巩固 apply 的落库标记，
        `promoter.py:190`）→ 撤销全簇：主记忆 retracted + 全部 consolidated 碎片
        恢复 active + 删除 supersedes 边，一个事务。已 retracted 的主记忆同样受理
        ——**补完**没收尾的撤销（ADR-0050 决策 5 原欠账正是「只能 retract 主记忆、
        碎片恢复只剩手改 DB」）；
      - **碎片**（`status == "consolidated"`）→ 恢复 active + FTS/向量重同步 +
        checkpoint（`trigger="revive"`）+ audit（`action="revive"`）；
      - **两者皆非** → `{"ok": False, "error": "invalid target ..."}`（路由层 409）。

    幂等（同 `retract_memory` 先例，只认状态不假设首次同步结果）：
      - 簇已撤销（主记忆 retracted 且边清零、无 consolidated 碎片）→ `already_revoked`；
      - 碎片已 active 且带 `trigger="revive"` checkpoint（起复留下的精确标记，
        区别于普通 active 记忆与晚更正 supersedes 旧值）→ `already_active`。
    """

    def _revive_one(s: Session, item: MemoryItem, warnings: list[str]) -> None:
        """单条 consolidated → active：重同步索引 + checkpoint + 审计。"""
        from lantai.evolution.promoter import _make_checkpoint

        fts_synced, fts_warn = _sync_fts(s, item.id, item.content)
        if not fts_synced:
            warnings.extend(fts_warn)
        vector_synced = sync_vector_upsert(item.id, item)
        if not vector_synced:
            warnings.append(
                "vector resync failed; memory searchable via FTS only"
                if fts_synced
                else "vector resync failed; memory active but not searchable until resync"
            )
        before = item.model_dump(mode="json")
        item.status = STATUS_ACTIVE
        item.version = (item.version or 1) + 1
        item.updated_at = utcnow()
        s.add(item)
        _make_checkpoint(s, item, before, proposal_id="", trigger="revive")
        audit_event(
            s,
            memory_id=item.id,
            action="revive",
            actor=actor,
            reason=reason,
            content=item.content,
            version_at=item.version,
        )

    def _run(s: Session) -> dict:
        from sqlmodel import select

        from lantai.evolution.promoter import _make_checkpoint
        from lantai.models.tables import MemoryCheckpoint, MemoryEdge

        item = s.get(MemoryItem, memory_id)
        if item is None:
            return dict(_NOT_FOUND)

        # ① 主记忆形态：source_ids 非空即巩固产物（apply 时 promoter 落库的标记，
        #    不依赖边是否还在——边可能已被部分清理）
        if item.source_ids:
            edges = s.exec(
                select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == memory_id,
                    MemoryEdge.relation == "supersedes",
                )
            ).all()
            # 幂等：主记忆已撤回且无残留（边清零、无 consolidated 碎片）＝簇已撤销
            if (
                item.status == STATUS_RETRACTED
                and not edges
                and not any(
                    (frag := s.get(MemoryItem, fid)) is not None
                    and frag.status == STATUS_CONSOLIDATED
                    for fid in item.source_ids
                )
            ):
                return {"ok": True, "already_revoked": True, "warnings": []}

            warnings: list[str] = []
            if item.status == STATUS_RETRACTED:
                # 已被普通撤回（retract_memory 只置状态、不动碎片/边）：索引清理已做过，
                # 但那次若同步失败会有残留——不假设干净，重做一次清理并如实回报
                fts_removed, fts_warn = _sync_fts(s, memory_id, None)
                if not fts_removed:
                    warnings.extend(fts_warn)
                vector_removed = sync_vector_delete(memory_id)
                if not vector_removed:
                    warnings.append("vector delete failed; SQL status filter remains authoritative")
                warnings.append("master was already retracted; cluster cleanup completed")
            else:
                fts_removed, fts_warn = _sync_fts(s, memory_id, None)
                if not fts_removed:
                    warnings.extend(fts_warn)
                vector_removed = sync_vector_delete(memory_id)
                if not vector_removed:
                    warnings.append("vector delete failed; SQL status filter remains authoritative")
                master_before = item.model_dump(mode="json")
                item.status = STATUS_RETRACTED
                item.version = (item.version or 1) + 1
                item.updated_at = utcnow()
                s.add(item)
                _make_checkpoint(s, item, master_before, proposal_id="", trigger="unconsolidate")
            audit_event(
                s,
                memory_id=memory_id,
                action="unconsolidate",
                actor=actor,
                reason=reason,
                content=item.content,
                version_at=item.version,
            )
            revived = 0
            for edge in edges:
                frag = s.get(MemoryItem, edge.target_memory_id)
                if frag is None:
                    continue
                s.delete(edge)
                if frag.status == STATUS_CONSOLIDATED:
                    _revive_one(s, frag, warnings)
                    revived += 1
            s.commit()
            return {
                "ok": True,
                "scope": "cluster",
                "master_id": memory_id,
                "fragments_revived": revived,
                "edges_removed": len(edges),
                "fts_removed": fts_removed,
                "vector_removed": vector_removed,
                "warnings": warnings,
            }

        # ② 碎片形态：consolidated → active
        if item.status == STATUS_CONSOLIDATED:
            warnings = []
            _revive_one(s, item, warnings)
            s.commit()
            return {
                "ok": True,
                "scope": "fragment",
                "memory_id": memory_id,
                "warnings": warnings,
            }

        # ③ 幂等：曾被本函数起复过的碎片（trigger="revive" checkpoint 是精确标记，
        #    普通 active 记忆与晚更正 supersedes 旧值都不带此标记）
        if (
            item.status == STATUS_ACTIVE
            and s.exec(
                select(MemoryCheckpoint).where(
                    MemoryCheckpoint.memory_id == memory_id,
                    MemoryCheckpoint.trigger == "revive",
                )
            ).first()
        ):
            return {"ok": True, "already_active": True, "warnings": []}

        # ④ 两者皆非：拒绝（宁 miss 不脏写，不猜用户意图）
        return {
            "ok": False,
            "error": ("invalid target (not consolidated fragment nor consolidation master)"),
        }

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)
