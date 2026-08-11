"""Obsidian 双链 + 原文直存通道（Ticket 02，借鉴 aiduMEI v18.3）。

- sync_obsidian_note：笔记原文走 add_raw_memory（零 LLM 直存，content_hash 幂等）；
- extract_wikilinks：纯函数解析 [[页面]] / [[页面|别名]]，忽略 [[#锚点]]；
- 双链词与笔记标题沉淀为实体（memory_type="entity"，不建 FTS/向量索引——
  实体是图谱节点，不参与召回），笔记↔实体建 MemoryEdge(relation="links")，
  重复推送靠 content_hash + 实体名去重（宁 miss 不脏写，全程无 LLM）。
"""
import re

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.models.enums import MemoryTier
from lantai.models.schemas import ObsidianSyncReq, RawMemoryReq
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.services.memory_service import add_raw_memory
from lantai.storage import db

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# verbatim 通道内容上限（复用 RawMemoryReq 约束，超出拒绝而非截断）
_VERBATIM_MAX_CHARS = 200_000


def extract_wikilinks(text: str) -> list[str]:
    """解析双链：[[页面]] / [[页面|别名]] → 页面名；[[#锚点]] 与纯文本忽略。"""
    names: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(text or ""):
        raw = m.group(1).strip()
        if raw.startswith("#"):
            continue  # 锚点引用不是双链实体
        name = raw.split("|", 1)[0].strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _get_or_create_entity(s, name: str) -> MemoryItem:
    ent = s.exec(select(MemoryItem)
                 .where(MemoryItem.memory_type == "entity",
                        MemoryItem.namespace == "entity",
                        MemoryItem.key == name,
                        MemoryItem.status == "active")).first()
    if ent:
        return ent
    ent = MemoryItem(
        id=new_id("mem"),
        memory_type="entity",
        namespace="entity",
        key=name,
        content=name,
        lane="general",
        tier=MemoryTier.LONG_TERM,
        confidence=1.0,
        importance=0.0,
        decay_class="semantic",
    )
    s.add(ent)
    s.flush()
    return ent


def _link(s, source_id: str, target_id: str) -> bool:
    dup = s.exec(select(MemoryEdge)
                 .where(MemoryEdge.source_memory_id == source_id,
                        MemoryEdge.target_memory_id == target_id,
                        MemoryEdge.relation == "links")).first()
    if dup:
        return False
    s.add(MemoryEdge(id=new_id("edge"), source_memory_id=source_id,
                     target_memory_id=target_id, relation="links",
                     confidence=1.0))
    return True


def sync_obsidian_note(req: ObsidianSyncReq) -> dict:
    """笔记 → verbatim 直存 + 双链实体/边沉淀（幂等，重复推送不重复）。"""
    note_content = f"{req.title}\n\n{req.content}".strip() if req.title else req.content
    if len(note_content) > _VERBATIM_MAX_CHARS:
        raise ValueError("note content too long (verbatim max 200000 chars)")

    raw = add_raw_memory(RawMemoryReq(
        content=note_content, title=req.title, lane=req.lane,
        tags=req.tags, metadata=req.metadata,
    ))
    note_id = raw["memory_id"]

    names = extract_wikilinks(req.content)
    if req.title:
        names = [req.title] + names  # 笔记标题也沉淀为实体
    entity_names: list[str] = []
    links_created = 0
    with db.get_session() as s:
        for name in dict.fromkeys(names):  # 保序去重
            _get_or_create_entity(s, name)
            entity_names.append(name)
        note = s.get(MemoryItem, note_id)
        for name in entity_names:
            ent = _get_or_create_entity(s, name)
            if note is not None and _link(s, note.id, ent.id):
                links_created += 1
        s.commit()
    return {
        "note_id": note_id,
        "dedup": raw["dedup"],
        "entities": entity_names,
        "links_created": links_created,
    }
