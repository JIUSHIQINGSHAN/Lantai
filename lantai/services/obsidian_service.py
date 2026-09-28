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


def _get_or_create_entity(s, name: str, principal=None) -> MemoryItem:
    """取/建实体行（`memory_type="entity"`，不建索引、不参与召回）。

    归属（票 `.scratch/readside-gaps/20`）：此前造实体**一个归属列都不填**
    → 实体行 `user_id` 恒 NULL → `graph_retriever._owns` 判「NULL 属主可见」
    → A 建的实体被 B 沿自己的边展开出来。补 `user_id` / `tenant_id` /
    `agent_id` 三列，`_owns` 才判得出「这不是 B 的实体」。

    **按 key 取已有行、不按属主**：实体是全局图谱节点（同名 [[链接]] 在全库
    是同一个概念），按属主过滤会让同一实体名在每人名下各建一份、把图谱拆碎。
    **既有无主实体行继续可复用**——它们是历史数据，补属主要猜「原本属于谁」，
    猜错比留 NULL 更脏（宁 miss 不脏写，不回溯补属主）。
    """
    ent = s.exec(
        select(MemoryItem).where(
            MemoryItem.memory_type == "entity",
            MemoryItem.namespace == "entity",
            MemoryItem.key == name,
            MemoryItem.status == "active",
        )
    ).first()
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
        user_id=getattr(principal, "user_id", None),
        tenant_id=getattr(principal, "tenant_id", None),
        agent_id=getattr(principal, "agent_id", None),
    )
    s.add(ent)
    s.flush()
    return ent


def _link(s, source_id: str, target_id: str, principal=None) -> bool:
    """建一条 `links` 边（重复推送幂等）。

    归属（票 `.scratch/readside-gaps/20`）：边落 `user_id` /
    `tenant_id` / `agent_id`，`graph_retriever._edge_scope` 才有判据
    （`user_id == viewer OR IS NULL`）。`principal=None` → 三列留 NULL
    （内部调用/脚本，读侧靠 NULL 口径兜住）。

    **幂等查重不按属主**：同一条 `source → target` 的 `links` 边在库里
    只应存在一条，按属主过滤会让同一对节点被每个人各连一遍。
    """
    dup = s.exec(
        select(MemoryEdge).where(
            MemoryEdge.source_memory_id == source_id,
            MemoryEdge.target_memory_id == target_id,
            MemoryEdge.relation == "links",
        )
    ).first()
    if dup:
        return False
    s.add(
        MemoryEdge(
            id=new_id("edge"),
            source_memory_id=source_id,
            target_memory_id=target_id,
            relation="links",
            confidence=1.0,
            user_id=getattr(principal, "user_id", None),
            tenant_id=getattr(principal, "tenant_id", None),
            agent_id=getattr(principal, "agent_id", None),
        )
    )
    return True


def sync_obsidian_note(req: ObsidianSyncReq, principal=None) -> dict:
    """笔记 → verbatim 直存 + 双链实体/边沉淀（幂等，重复推送不重复）。

    归属（票 `.scratch/readside-gaps/20`）：此前**一个身份都不取**——路由层
    `EXT_ROUTER` 带 `dependencies=AUTH` 所以认证了某人，却把身份丢掉了。
    A 提交一段与 B 的 verbatim 内容 sha256 相同的文本，`add_raw_memory` 的
    去重无归属过滤，返回 **B 的** `memory_id`；本函数拿它
    `s.get(MemoryItem, note_id)` 取到 B 的行，于是 **A 的双链实体名从 B 的
    记忆行连出去**（`links` 边方向是「笔记 → 实体」），污染 B 的图邻域，
    进而经 `expand_graph_associations` 影响 B 自己的召回。

    三层收口：
    1. `principal` 下传给 `add_raw_memory` → 去重按属主收窄，A 拿不到 B 的 id；
    2. `note_id` 取到行后过 `ensure_can_delete`（同票 04/06/11/13/18/19 范式）
       ——这条挡住「A 沿用 B 的记忆行」本身；
    3. `_get_or_create_entity` / `_link` 补归属 → `_owns` / `_edge_scope`
       才判得出「这不是 B 的实体/边」。

    `principal=None`（内部调用 / MCP / 脚本）三处都不过滤，与改动前逐字一致。
    越权返回 `{"ok": False, "reason": "forbidden: ..."}`（service 契约是 dict，
    403 语义在路由边界翻译，同 `promoter.delete_memory`）。
    """
    note_content = f"{req.title}\n\n{req.content}".strip() if req.title else req.content
    if len(note_content) > _VERBATIM_MAX_CHARS:
        raise ValueError("note content too long (verbatim max 200000 chars)")

    raw = add_raw_memory(
        RawMemoryReq(
            content=note_content,
            title=req.title,
            lane=req.lane,
            tags=req.tags,
            metadata=req.metadata,
        ),
        # 归属四元组随 principal 落列（票 20）：`add_raw_memory` 的形参默认
        # `user_id="default"`，不传的话 A 建的 verbatim 行属主恒为 "default"，
        # 下一步 `ensure_can_delete` 会判「resource belongs to another user」
        # 把 A 自己刚建的笔记拒掉。`principal=None` 时一个都不传，与改动前一致。
        user_id=(getattr(principal, "user_id", None) or "default")
        if principal is not None
        else "default",
        tenant_id=getattr(principal, "tenant_id", None),
        agent_id=getattr(principal, "agent_id", None),
        principal=principal,
    )
    note_id = raw["memory_id"]

    names = extract_wikilinks(req.content)
    if req.title:
        names = [req.title] + names  # 笔记标题也沉淀为实体
    entity_names: list[str] = []
    links_created = 0
    with db.get_session() as s:
        note = s.get(MemoryItem, note_id)
        # `session.get` 按主键直读、绕开 SQL 层 scope（票 14 踩过两次的盲区），
        # 所以单独判一次：非 admin 且行标了别人的 user_id → 拒绝沿用。
        if principal is not None and note is not None:
            from fastapi import HTTPException

            from lantai.core.acl import ensure_can_delete

            try:
                ensure_can_delete(
                    principal,
                    resource_user_id=note.user_id,
                    resource_tenant_id=note.tenant_id,
                    lane=note.lane,
                )
            except HTTPException as exc:
                return {"ok": False, "reason": f"forbidden: {exc.detail}"}
        for name in dict.fromkeys(names):  # 保序去重
            _get_or_create_entity(s, name, principal=principal)
            entity_names.append(name)
        for name in entity_names:
            ent = _get_or_create_entity(s, name, principal=principal)
            if note is not None and _link(s, note.id, ent.id, principal=principal):
                links_created += 1
        s.commit()
    return {
        "note_id": note_id,
        "dedup": raw["dedup"],
        "entities": entity_names,
        "links_created": links_created,
    }
