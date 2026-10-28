"""完璧（ADR-0057）：多实体事实检索与投影查询服务。

以事实主表（FactAtomic）为唯一权威，通过实体投影表（FactEntity）进行索引反查，
保证检索返回的多实体事实结构保整，不丢失关联主体与关系谓词。
"""

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.models.tables import FactAtomic, FactEntity


def _fact_scope(principal):
    """事实归属过滤：principal 为 None 或 admin 时放行，否则限制 user_id 匹配或为 NULL。"""
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    viewer = viewer_of(principal)
    return (FactAtomic.user_id == viewer) | (FactAtomic.user_id.is_(None))


def get_facts_by_entity(session: Session, entity_name: str, principal=None) -> list[dict]:
    """按归一化实体名称反查所有相关的原子事实，并聚合投影实体。

    返回列表元素形状：
    {
        "id": fact_id,
        "statement": 完整事实句,
        "predicate": 谓词,
        "object": 中心宾语,
        "confidence": 置信度,
        "valid_from": 生效时间,
        "valid_to": 失效时间,
        "source_memory_id": 来源记忆ID,
        "entities": [{"name": ..., "role": ...}, ...]
    }
    """
    if not entity_name:
        return []

    # 1. 查询匹配实体的 fact_id 集合
    stmt_entity = select(FactEntity.fact_id).where(FactEntity.entity_name == entity_name)
    fact_ids = session.exec(stmt_entity).all()
    if not fact_ids:
        return []

    # 2. 查询 FactAtomic 事实主表（带归属过滤）
    stmt_fact = select(FactAtomic).where(FactAtomic.id.in_(fact_ids))
    scope = _fact_scope(principal)
    if scope is not None:
        stmt_fact = stmt_fact.where(scope)
    facts = session.exec(stmt_fact).all()
    if not facts:
        return []

    # 3. 聚合事实对应的全部实体投影
    matched_ids = [f.id for f in facts]
    stmt_all_entities = select(FactEntity).where(FactEntity.fact_id.in_(matched_ids))
    all_proj = session.exec(stmt_all_entities).all()

    proj_map: dict[str, list[dict]] = {}
    for proj in all_proj:
        proj_map.setdefault(proj.fact_id, []).append({"name": proj.entity_name, "role": proj.role})

    # 4. 组装结果
    out = []
    for f in facts:
        out.append(
            {
                "id": f.id,
                "statement": f.statement,
                "predicate": f.predicate,
                "object": f.object,
                "confidence": f.confidence,
                "valid_from": f.valid_from,
                "valid_to": f.valid_to,
                "source_memory_id": f.source_memory_id,
                "entities": proj_map.get(f.id, []),
            }
        )
    return out


def get_facts_by_predicate(session: Session, predicate: str, principal=None) -> list[dict]:
    """按谓词查询原子事实。"""
    if not predicate:
        return []

    stmt_fact = select(FactAtomic).where(FactAtomic.predicate == predicate)
    scope = _fact_scope(principal)
    if scope is not None:
        stmt_fact = stmt_fact.where(scope)
    facts = session.exec(stmt_fact).all()
    if not facts:
        return []

    matched_ids = [f.id for f in facts]
    stmt_all_entities = select(FactEntity).where(FactEntity.fact_id.in_(matched_ids))
    all_proj = session.exec(stmt_all_entities).all()

    proj_map: dict[str, list[dict]] = {}
    for proj in all_proj:
        proj_map.setdefault(proj.fact_id, []).append({"name": proj.entity_name, "role": proj.role})

    out = []
    for f in facts:
        out.append(
            {
                "id": f.id,
                "statement": f.statement,
                "predicate": f.predicate,
                "object": f.object,
                "confidence": f.confidence,
                "valid_from": f.valid_from,
                "valid_to": f.valid_to,
                "source_memory_id": f.source_memory_id,
                "entities": proj_map.get(f.id, []),
            }
        )
    return out
