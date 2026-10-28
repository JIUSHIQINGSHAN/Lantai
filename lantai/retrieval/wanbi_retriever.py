"""完璧（ADR-0057）：多实体事实检索与投影查询服务。

以事实主表（FactAtomic）为唯一权威，通过实体投影表（FactEntity）进行索引反查，
保证检索返回的多实体事实结构保整，不丢失关联主体与关系谓词。
"""

import re
from typing import Any

from sqlmodel import Session, col, select

from lantai.core.acl import viewer_of
from lantai.core.time import utcnow
from lantai.models.tables import FactAtomic, FactEntity


def _fact_scope(principal: Any = None):
    """事实归属过滤：principal 为 None 或 admin 时放行，否则限制 user_id 匹配或为 NULL。"""
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    viewer = viewer_of(principal)
    return (FactAtomic.user_id == viewer) | (FactAtomic.user_id.is_(None))


def _hydrate_facts_with_projections(session: Session, facts: list[FactAtomic]) -> list[dict]:
    """批量装配 FactAtomic 实例列表与其对应的 FactEntity 实体投影。消除重复代码。"""
    if not facts:
        return []
    fact_ids = [f.id for f in facts]
    stmt = select(FactEntity).where(col(FactEntity.fact_id).in_(fact_ids))
    all_proj = session.exec(stmt).all()

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


def get_facts_by_entity(session: Session, entity_name: str, principal: Any = None) -> list[dict]:
    """按归一化实体名称反查所有相关的原子事实，并聚合投影实体。"""
    if not entity_name:
        return []

    # 1. 查询匹配实体的 fact_id 集合
    stmt_entity = select(FactEntity.fact_id).where(FactEntity.entity_name == entity_name)
    fact_ids = session.exec(stmt_entity).all()
    if not fact_ids:
        return []

    # 2. 查询 FactAtomic 事实主表（带归属过滤与置信度过滤）
    stmt_fact = (
        select(FactAtomic)
        .where(col(FactAtomic.id).in_(fact_ids))
        .where(FactAtomic.confidence > 0.0)
    )
    scope = _fact_scope(principal)
    if scope is not None:
        stmt_fact = stmt_fact.where(scope)
    facts = session.exec(stmt_fact).all()
    return _hydrate_facts_with_projections(session, list(facts))


def get_facts_by_predicate(session: Session, predicate: str, principal: Any = None) -> list[dict]:
    """按谓词查询原子事实。"""
    if not predicate:
        return []

    stmt_fact = (
        select(FactAtomic)
        .where(FactAtomic.predicate == predicate)
        .where(FactAtomic.confidence > 0.0)
    )
    scope = _fact_scope(principal)
    if scope is not None:
        stmt_fact = stmt_fact.where(scope)
    facts = session.exec(stmt_fact).all()
    return _hydrate_facts_with_projections(session, list(facts))


def search_atomic_facts_by_query(
    session: Session, query: str, principal: Any = None, limit: int = 5
) -> list[dict]:
    """通过 query 中的实体名或子串反查原子事实。

    1. 从 query 提取候选词精准反查 FactEntity（非全表扫）；
    2. 或通过 query 匹配 statement（必须同时满足 confidence > 0 且有效）；
    3. 返回聚合了全部关联实体的原子事实列表。
    """
    if not query or not query.strip():
        return []

    # 从 query 中切取潜在实体词候选（jieba 分词 + 英文/中文词提取）
    candidates = set()
    try:
        import jieba

        for term in jieba.lcut(query):
            term = term.strip()
            if len(term) >= 2:
                candidates.add(term)
    except Exception:
        # 降级提取连续中文字符串或英文词
        candidates.update(re.findall(r"[一-龥]{2,}|[A-Za-z0-9_-]{2,}", query))

    matched_fact_ids = set()
    if candidates:
        stmt_entity = select(FactEntity.fact_id).where(
            col(FactEntity.entity_name).in_(list(candidates))
        )
        matched_fact_ids.update(session.exec(stmt_entity).all())

    # 查 FactAtomic，严格约束 confidence > 0.0（防已撤回事实泄漏）
    now_iso = utcnow().isoformat()
    stmt_fact = select(FactAtomic).where(FactAtomic.confidence > 0.0)

    scope = _fact_scope(principal)
    if scope is not None:
        stmt_fact = stmt_fact.where(scope)

    # 统一过滤条件：实体反查命中的 fact_ids 或 statement 包含检索词
    from sqlmodel import or_

    conditions = []
    if matched_fact_ids:
        conditions.append(col(FactAtomic.id).in_(list(matched_fact_ids)))

    # statement 包含候选词或 query 前段
    for term in sorted(candidates, key=len, reverse=True)[:5]:
        conditions.append(col(FactAtomic.statement).contains(term))
    if len(query.strip()) >= 2:
        conditions.append(col(FactAtomic.statement).contains(query.strip()[:30]))

    if conditions:
        stmt_fact = stmt_fact.where(or_(*conditions))

    # 有效期过滤（若设定了 valid_to，且已过期则不查）
    stmt_fact = stmt_fact.where((FactAtomic.valid_to.is_(None)) | (FactAtomic.valid_to > now_iso))

    facts = session.exec(stmt_fact.limit(limit)).all()
    return _hydrate_facts_with_projections(session, list(facts))
