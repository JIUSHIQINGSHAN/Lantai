"""目次（Muci）多维记忆知识目录与分级导航引擎 (ADR-0065)。

典籍出处：南朝梁·刘勰《文心雕龙·序志》「成岁既渐，目次来构」；部目次序、条贯井然，使开卷者一览无遗。
解决痛点：
1. 突破既有 ops/overview.py 仅统计总数/分轨的纯计数局限，落地面向 Agent 顶层导航的分级知识目录树；
2. 支持按辨域（domain）、分轨（lane）、核心实体/主题聚类与时序重要度分级编排；
3. 支持 compact（紧凑极低 token）与 full（详尽展开）双模式，正文统一经樊篱（wrap_as_data）安全围栏保护；
4. 严格执行行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from lantai.core.auth import Principal
from lantai.core.time import utcnow
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
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

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
        detail_level: str = "compact",
        max_entries_per_section: int = 3,
    ) -> dict[str, Any]:
        """构建多维分级目次结构。

        Args:
            domain: 可选限定辨域（user/session/agent）
            lane: 可选限定分轨（fact/rule/preference/experience/general）
            detail_level: "compact" (紧凑低Token大纲) 或 "full" (详尽包含条目摘要)
            max_entries_per_section: full 模式下每板块最大摘录条目数
        """
        session = self._get_session()
        query = select(MemoryItem).order_by(
            MemoryItem.importance.desc(), MemoryItem.updated_at.desc()
        )

        # 预过滤条件
        if domain:
            query = query.where(MemoryItem.domain == domain)
        if lane:
            query = query.where(MemoryItem.lane == lane)

        items = session.exec(query).all()

        # ACL 安全剪枝
        visible_items = [it for it in items if self._check_access(it)]

        # 按 (domain, lane) 二元聚合
        grouped: dict[tuple[str, str], list[MemoryItem]] = defaultdict(list)
        for it in visible_items:
            d = it.domain or "user"
            l = it.lane or "general"
            grouped[(d, l)].append(it)

        sections: list[dict[str, Any]] = []
        all_domains = set()
        all_lanes = set()
        total_memories = len(visible_items)
        active_memories = sum(1 for it in visible_items if it.status == "active")

        # 排序：确保输出稳定可测（按 domain 再按 lane）
        for (sec_domain, sec_lane), sec_items in sorted(
            grouped.items(), key=lambda x: (x[0][0], x[0][1])
        ):
            all_domains.add(sec_domain)
            all_lanes.add(sec_lane)

            # 聚合标签与最新时间
            tags_set = set()
            latest_time = None
            for it in sec_items:
                if it.tags:
                    for t in it.tags:
                        if isinstance(t, str) and t.strip():
                            tags_set.add(t.strip())
                if it.updated_at:
                    if latest_time is None or it.updated_at > latest_time:
                        latest_time = it.updated_at

            sec_data: dict[str, Any] = {
                "domain": sec_domain,
                "lane": sec_lane,
                "count": len(sec_items),
                "active_count": sum(1 for it in sec_items if it.status == "active"),
                "tags": sorted(list(tags_set))[:10],
                "latest_event_at": latest_time.isoformat() if latest_time else None,
            }

            if detail_level == "full":
                # 抽取精要条目，正文经樊篱（wrap_as_data）包裹
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
                            "updated_at": it.updated_at.isoformat() if it.updated_at else None,
                        }
                    )
                sec_data["entries"] = picked_entries

            sections.append(sec_data)

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
            "summary": " · ".join(summary_parts),
            "generated_at": utcnow().isoformat(),
        }
