"""目次（Muci）多维记忆知识目录与分级导航引擎 (ADR-0065)。

典籍出处：南朝梁·刘勰《文心雕龙·序志》「成岁既渐，目次来构」；部目次序、条贯井然，使开卷者一览无遗。
解决痛点：
1. 突破既有 ops/overview.py 仅统计总数/分轨的纯计数局限，落地面向 Agent 顶层导航的分级知识目录树；
2. 支持按辨域（domain）、分轨（lane）、记忆标签主题聚类与事件时间/重要度分级编排；
3. 支持 compact（紧凑极低 token）与 full（详尽展开）双模式，正文统一经樊篱（wrap_as_data）安全围栏保护；
4. 严格执行行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import is_system_viewer
from lantai.core.auth import Principal
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.storage import db


@dataclass
class CatalogSection:
    """目次单板块（Domain + Lane 组织单元）。"""

    domain: str
    lane: str
    count: int = 0
    active_count: int = 0
    tags: list[str] = field(default_factory=list)
    latest_event_at: datetime | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)


def _normalized_tags(item: MemoryItem) -> set[str]:
    return {
        tag.strip().casefold() for tag in (item.tags or []) if isinstance(tag, str) and tag.strip()
    }


def _latest_event_at(items: list[MemoryItem]) -> datetime | None:
    known_events = [item.event_time for item in items if item.event_time is not None]
    return max(known_events, key=lambda value: iso_utc(value) or "") if known_events else None


def _average_importance(items: list[MemoryItem]) -> float:
    if not items:
        return 0.0
    return sum(item.importance if item.importance is not None else 0.5 for item in items) / len(
        items
    )


def _memory_summary(items: list[MemoryItem]) -> dict[str, Any]:
    return {
        "count": len(items),
        "active_count": sum(
            1
            for item in items
            if item.status == "active" and (item.lifecycle_status or "") == "active"
        ),
        "avg_importance": round(_average_importance(items), 3),
        "latest_event_at": iso_utc(_latest_event_at(items)) or None,
    }


class MuciEngine:
    """目次·多维记忆知识目录聚合引擎。"""

    def __init__(
        self,
        session: Session | None = None,
        principal: Principal | None = None,
    ):
        self.session = session
        self.principal = principal

    def _get_session(self) -> Session:
        if self.session is not None:
            return self.session
        return db.get_session()

    def _check_access(self, item: MemoryItem) -> bool:
        """行级租户与属主 ACL 隔离校验（严格宁 miss 不脏写）。"""
        if getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal):
            return True
        if self.principal is None:
            # 票 57：无身份收敛 "default" 属主空间（同 zhaji 口径），不再全放
            return item.user_id in (None, "default")

        # 1. 租户隔离
        if self.principal.tenant_id:
            if item.tenant_id and item.tenant_id != self.principal.tenant_id:
                return False

        # 2. 用户属主隔离（viewer_of 口径：属于当前用户，或公共条目）
        user_id = self.principal.user_id
        if user_id:
            if item.user_id and item.user_id != user_id:
                return False

        # 3. 授权泳道限制（allowed_lanes 隔离）
        if self.principal.allowed_lanes is not None:
            if item.lane not in self.principal.allowed_lanes:
                return False

        return True

    def build_catalog(
        self,
        *,
        domain: str | None = None,
        lane: str | None = None,
        topic: str | None = None,
        detail_level: str = "compact",
        max_entries_per_section: int = 3,
    ) -> dict[str, Any]:
        """构建多维分级目次结构。

        Args:
            domain: 可选限定辨域（user/session/agent）
            lane: 可选限定分轨（fact/rule/preference/experience/general）
            topic: 可选精确主题筛选（匹配规范化后的记忆标签）
            detail_level: "compact" (紧凑低Token大纲) 或 "full" (详尽包含条目摘要)
            max_entries_per_section: full 模式下每板块最大摘录条目数
        """
        if topic is not None and (not isinstance(topic, str) or not topic.strip()):
            raise ValueError("topic must be a non-empty string")
        own_session = self.session is None
        session = self._get_session()
        try:
            # 票 62：读面 lifecycle 过滤（口径=tables.py 正交注释 + 票 15 shiyi 先例）：
            # status 是管道态、lifecycle_status 是认识论态，supersede 只写 lifecycle
            # （status 仍 active）——只看 status 时 archived/retracted 之外的
            # superseded 行全文混进目录、活跃计数虚高。白名单 = 管道 active +
            # 认识论 {active, weakened}（weakened 语义「仍参与检索但权重降低」，
            # retired「等同于 archived」排除）。DDL NOT NULL DEFAULT 'active'，
            # 无需 NULL 防御分支。
            query = (
                select(MemoryItem)
                .where(
                    MemoryItem.status == "active",
                    MemoryItem.lifecycle_status.in_(("active", "weakened")),
                )
                .order_by(
                    MemoryItem.importance.desc(), MemoryItem.updated_at.desc(), MemoryItem.id.asc()
                )
            )

            # 预过滤条件
            if domain:
                query = query.where(MemoryItem.domain == domain)
            if lane:
                query = query.where(MemoryItem.lane == lane)

            items = session.exec(query).all()

            # ACL 安全剪枝
            visible_items = [it for it in items if self._check_access(it)]
            if topic is not None:
                topic_key = topic.strip().casefold()
                visible_items = [it for it in visible_items if topic_key in _normalized_tags(it)]

            # 按 (domain, lane) 二元聚合
            grouped: dict[tuple[str, str], list[MemoryItem]] = defaultdict(list)
            domain_groups: dict[str, list[MemoryItem]] = defaultdict(list)
            for it in visible_items:
                d = it.domain or "user"
                l = it.lane or "general"
                grouped[(d, l)].append(it)
                domain_groups[d].append(it)

            sections: list[dict[str, Any]] = []
            outline: list[dict[str, Any]] = []
            all_domains = set()
            all_lanes = set()
            total_memories = len(visible_items)
            active_memories = sum(
                1
                for it in visible_items
                if it.status == "active" and (it.lifecycle_status or "") == "active"
            )

            for sec_domain in sorted(domain_groups):
                domain_items = domain_groups[sec_domain]
                lane_nodes: list[dict[str, Any]] = []
                all_domains.add(sec_domain)
                for sec_lane in sorted(key[1] for key in grouped if key[0] == sec_domain):
                    sec_items = grouped[(sec_domain, sec_lane)]
                    all_lanes.add(sec_lane)
                    tags_set = {
                        tag.strip()
                        for it in sec_items
                        for tag in (it.tags or [])
                        if isinstance(tag, str) and tag.strip()
                    }
                    sec_data: dict[str, Any] = {
                        "domain": sec_domain,
                        "lane": sec_lane,
                        **_memory_summary(sec_items),
                        "tags": sorted(tags_set)[:10],
                    }

                    if detail_level == "full":
                        picked_entries = []
                        for it in sec_items[:max_entries_per_section]:
                            safe_content = wrap_as_data(it.content, item_id=it.id)
                            picked_entries.append(
                                {
                                    "id": it.id,
                                    "content": safe_content,
                                    "importance": round(it.importance, 2)
                                    if it.importance is not None
                                    else 0.5,
                                    "decay_class": it.decay_class,
                                    "updated_at": iso_utc(it.updated_at) or None,
                                }
                            )
                        sec_data["entries"] = picked_entries
                    sections.append(sec_data)

                    topic_groups: dict[str, list[MemoryItem]] = defaultdict(list)
                    untagged_count = 0
                    for it in sec_items:
                        item_topics = _normalized_tags(it)
                        if not item_topics:
                            untagged_count += 1
                        for topic_key in item_topics:
                            topic_groups[topic_key].append(it)
                    ranked_topics = [
                        (topic_key, topic_items, _memory_summary(topic_items))
                        for topic_key, topic_items in topic_groups.items()
                    ]
                    ranked_topics.sort(
                        key=lambda entry: (
                            -entry[2]["count"],
                            -_average_importance(entry[1]),
                            entry[0],
                        )
                    )
                    topic_limit = None if detail_level == "full" else 5
                    shown_topics = (
                        ranked_topics if topic_limit is None else ranked_topics[:topic_limit]
                    )
                    lane_nodes.append(
                        {
                            "lane": sec_lane,
                            **_memory_summary(sec_items),
                            "untagged_count": untagged_count,
                            "topic_count": len(ranked_topics),
                            "omitted_topic_count": len(ranked_topics) - len(shown_topics),
                            "topics": [
                                {"topic": topic_key, **topic_summary}
                                for topic_key, _, topic_summary in shown_topics
                            ],
                        }
                    )

                outline.append(
                    {"domain": sec_domain, **_memory_summary(domain_items), "lanes": lane_nodes}
                )

            global_topics: dict[str, list[MemoryItem]] = defaultdict(list)
            for it in visible_items:
                for topic_key in _normalized_tags(it):
                    global_topics[topic_key].append(it)
            landmarks = []
            ranked_global_topics = [
                (topic_key, topic_items, _memory_summary(topic_items))
                for topic_key, topic_items in global_topics.items()
            ]
            ranked_global_topics.sort(
                key=lambda entry: (
                    -entry[2]["count"],
                    -_average_importance(entry[1]),
                    entry[0],
                )
            )
            for topic_key, topic_items, topic_summary in ranked_global_topics[:8]:
                landmarks.append(
                    {
                        "topic": topic_key,
                        **topic_summary,
                        "domain_lane_count": len(
                            {
                                (item.domain or "user", item.lane or "general")
                                for item in topic_items
                            }
                        ),
                    }
                )

            # 构建综述摘要
            summary_parts = [f"记忆总数 {total_memories} 条（活跃 {active_memories} 条）"]
            if sections:
                sec_summary = ", ".join(
                    f"{s['domain']}/{s['lane']}({s['count']})" for s in sections[:4]
                )
                summary_parts.append(f"核心板块: {sec_summary}")

            return {
                "total_memories": total_memories,
                "active_memories": active_memories,
                "domains": sorted(list(all_domains)),
                "lanes": sorted(list(all_lanes)),
                "detail_level": detail_level,
                "sections": sections,
                "outline": outline,
                "landmarks": landmarks,
                "summary": " · ".join(summary_parts),
                "generated_at": iso_utc(utcnow()),
            }
        finally:
            if own_session:
                session.close()
