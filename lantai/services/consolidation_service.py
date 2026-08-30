"""沉潜（ADR-0036）：闲时夜梦沉淀与记忆折叠压缩服务。

功能：
1. find_consolidation_clusters: 发现同域/同分轨下高重合度的碎片记忆集；
2. consolidate_cluster: 概念提纯并生成主记忆，折叠碎片记忆（status="consolidated"）；
3. prune_decayed_synapses: 自动修剪极度衰减的边缘碎片（status="archived"）；
4. run_consolidation_cycle: 调度执行完整沉潜周期。
"""
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional
import jieba.analyse
from sqlmodel import Session, select
from ulid import ULID

from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.llm.client import chat_json
from lantai.models.tables import MemoryCheckpoint, MemoryItem
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage import db

_LAST_CONSOLIDATION_REPORT: dict = {
    "last_run": None,
    "consolidated_groups": 0,
    "new_memories": 0,
    "pruned_count": 0,
    "status": "idle",
}


def find_consolidation_clusters(
    session: Session, min_cluster_size: int = 3
) -> list[list[MemoryItem]]:
    """扫描活跃记忆，按 domain/lane 与主题聚类出可折叠的碎片记忆集。"""
    active_items = session.exec(
        select(MemoryItem).where(MemoryItem.status == "active")
    ).all()

    # 1. 按 (domain, lane) 分组
    group_map = defaultdict(list)
    for m in active_items:
        # 跳过已是聚合主记忆（带有多 source_ids）的项，避免无限递归折叠
        if len(m.source_ids or []) >= 3:
            continue
        dom = getattr(m, "domain", "user")
        group_map[(dom, m.lane)].append(m)

    clusters: list[list[MemoryItem]] = []

    # 2. 组内主题关键词聚类
    for (dom, lane), items in group_map.items():
        if len(items) < min_cluster_size:
            continue

        keyword_item_map = defaultdict(list)
        for item in items:
            # 提取 2~4 个核心关键词
            tags = jieba.analyse.extract_tags(item.content, topK=4)
            for tag in tags:
                if len(tag.strip()) >= 2:
                    keyword_item_map[tag.strip()].append(item)

        # 找出命中同一关键词且数量 >= min_cluster_size 的子集
        clustered_ids = set()
        for kw, cand_items in keyword_item_map.items():
            unique_items = {i.id: i for i in cand_items if i.id not in clustered_ids}
            if len(unique_items) >= min_cluster_size:
                cluster = list(unique_items.values())
                clusters.append(cluster)
                for i in cluster:
                    clustered_ids.add(i.id)

    logger.info("沉潜：聚类扫描发现 %d 组可折叠碎片集", len(clusters))
    return clusters


def consolidate_cluster(
    cluster_items: list[MemoryItem], session: Optional[Session] = None
) -> Optional[MemoryItem]:
    """对一组碎片记忆进行概念提纯与合成，生成主记忆并折叠子记忆。"""
    if not cluster_items:
        return None

    def _execute(s: Session) -> Optional[MemoryItem]:
        sources_text = "\n".join(
            f"- [ID: {m.id}] {m.content}" for m in cluster_items
        )
        sys_prompt = (
            "你是一个专业的认知记忆综合提纯专家。请将以下多条碎片化的日常记忆/偏好/事实，"
            "提纯归纳为 1 条高阶概括性、准确且简练的主记忆。\n"
            "要求：\n"
            "1. 保留关键实体、偏好参数、事实细节，去除重复冗余与口语口水话；\n"
            "2. 返回 JSON 格式：\n"
            "{\n"
            '  "consolidated_content": "提纯后的一句话主记忆内容",\n'
            '  "importance": 0.8,\n'
            '  "confidence": 0.95\n'
            "}"
        )
        user_prompt = f"待提纯碎片记忆列表：\n{sources_text}"

        try:
            res = chat_json(sys_prompt=sys_prompt, user_prompt=user_prompt)
        except Exception as exc:
            logger.warning("沉潜：LLM 提纯失败，保持原库不变（宁 miss 不脏写）: %s", exc)
            return None

        if not isinstance(res, dict) or not res.get("consolidated_content"):
            logger.warning("沉潜：LLM 提纯未返回有效内容，跳过折叠")
            return None

        content = str(res["consolidated_content"]).strip()
        importance = float(res.get("importance", 0.8))
        confidence = float(res.get("confidence", 0.9))

        dom = getattr(cluster_items[0], "domain", "user")
        lane = cluster_items[0].lane
        source_ids = [m.id for m in cluster_items]

        # 1. 记录 Checkpoint 快照
        cp = MemoryCheckpoint(
            id=f"cp_{ULID()}",
            memory_id="cluster_consolidation",
            version=1,
            before={"source_ids": source_ids},
            after={"content": content, "domain": dom, "lane": lane},
            trigger="consolidation",
            created_at=utcnow(),
        )
        s.add(cp)

        # 2. 创建提纯后的主记忆
        new_id = f"mem_{ULID()}"
        master = MemoryItem(
            id=new_id,
            content=content,
            domain=dom,
            lane=lane,
            source_ids=source_ids,
            confidence=confidence,
            importance=importance,
            decay_score=1.0,
            decay_class="semantic",
            status="active",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        s.add(master)

        # 3. 将原碎片记忆标记为 consolidated 折叠状态
        for m in cluster_items:
            m.status = "consolidated"
            m.updated_at = utcnow()
            s.add(m)

        s.commit()
        s.refresh(master)

        # 4. 同步更新向量库与 FTS 索引
        try:
            index_memory_item(master)
        except Exception as exc:
            logger.warning("沉潜：主记忆向量索引同步异常（已落库）: %s", exc)

        logger.info(
            "沉潜：成功将 %d 条碎片折叠为主记忆 %s: %s",
            len(cluster_items), master.id, content[:30],
        )
        return master

    if session is not None:
        return _execute(session)
    with db.get_session() as s:
        return _execute(s)


def prune_decayed_synapses(
    threshold: float = 0.05, session: Optional[Session] = None
) -> int:
    """自动修剪极度衰减的边缘碎片（转为 archived 休眠）。"""
    def _prune(s: Session) -> int:
        decayed_items = s.exec(
            select(MemoryItem)
            .where(MemoryItem.status == "active")
            .where(MemoryItem.decay_score < threshold)
            .where(MemoryItem.helpful_count == 0)
        ).all()

        pruned = 0
        for item in decayed_items:
            item.status = "archived"
            item.updated_at = utcnow()
            s.add(item)
            pruned += 1

        if pruned > 0:
            s.commit()
            logger.info("沉潜：成功修剪 %d 条极度衰减的边缘碎片记忆", pruned)
        return pruned

    if session is not None:
        return _prune(session)
    with db.get_session() as s:
        return _prune(s)


def run_consolidation_cycle(session: Optional[Session] = None) -> dict:
    """运行一次完整的沉潜夜梦沉淀周期。"""
    def _run(s: Session) -> dict:
        global _LAST_CONSOLIDATION_REPORT
        clusters = find_consolidation_clusters(s, min_cluster_size=3)
        new_count = 0
        for cluster in clusters:
            res = consolidate_cluster(cluster, session=s)
            if res is not None:
                new_count += 1

        pruned = prune_decayed_synapses(threshold=0.05, session=s)
        status = "success" if (new_count > 0 or pruned > 0) else "idle"

        report = {
            "last_run": utcnow().isoformat(),
            "consolidated_groups": len(clusters),
            "new_memories": new_count,
            "pruned_count": pruned,
            "status": status,
        }
        _LAST_CONSOLIDATION_REPORT = report
        return report

    if session is not None:
        return _run(session)
    with db.get_session() as s:
        return _run(s)


def get_consolidation_report() -> dict:
    """获取最近一次沉潜运行报告。"""
    return _LAST_CONSOLIDATION_REPORT
