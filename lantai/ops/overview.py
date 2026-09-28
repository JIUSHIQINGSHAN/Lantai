"""轻量记忆概览（只读聚合，零写放大）。

给「我的记忆系统现在到底存了什么」一个一眼可见的答案：
- 记忆总数 / active / archived，按 lane 与 decay_class 分布
- 待审候选（pending_review）积压 —— 人工闸门待裁决
- 检查点（Checkpoint）版本数、待审提案（pending）数

build_overview(session) 是纯函数（测试直传临时 session，不 mock 内部逻辑）；
get_overview(principal=None) 打开默认会话执行。

归属（票 `.scratch/readside-gaps/05）：`build_overview` 被
`build_monitor_snapshot` 复用，此前全表不带身份——`/monitor/overview`
于是把全库记忆总量与待审积压吐给任何持 key 者。现在按 viewer 收窄，
口径同票 04 的 `_digest_scope`（`user_id == viewer OR IS NULL`，NULL 老行
可见：单人部署下 629/650 行 NULL，判不可见等于报表归零）。
`principal=None` / admin 全量（口径同前 18 票）。`get_overview(principal)`
是 MCP `mem_stats` 入口（票 `.scratch/mcp-identity-gaps/01a`）：宿主透传
`user_id` 才收窄，不透传留 `None` 全量——与改动前逐字一致。
"""

from datetime import UTC, datetime

from sqlmodel import func, select

from lantai.models.tables import (
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryItem,
    MemoryProposal,
)
from lantai.storage import db


def _overview_scope(column, principal):
    """概览计数的归属过滤条件（票 `.scratch/readside-gaps/05`）。

    与票 04 的 `_digest_scope` 逐字同口径：`user_id == viewer OR IS NULL`。
    NULL 老行**可见**——真实库 memoryitem 650 行里 629 行 NULL、persona
    那一行也是 NULL（单人部署）。判「NULL 不可见」会让概览对唯一的真实
    用户显示 0 条记忆，报表归零不是收窄，是修废。

    `principal=None`（内部 worker / MCP / 脚本）与 admin 全量：前者是
    定时任务/CLI 不带身份，一过滤就空转，那是把内部工具修废。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.core.acl import viewer_of

    viewer = viewer_of(principal)
    return (column == viewer) | (column.is_(None))


def build_overview(session, *, principal=None) -> dict:
    """只读聚合：给定 session 汇总记忆系统现状。

    归属（票 05）：`principal=None`（内部调用 / MCP / 脚本）与 admin/system
    全量；其余记忆/候选/提案三项计数与两个分布按 viewer 收窄。
    `MemoryCheckpoint` 没有归属列，仍是全量（另议）。
    """
    mem_scope = _overview_scope(MemoryItem.user_id, principal)
    cand_scope = _overview_scope(MemoryCandidate.user_id, principal)
    prop_scope = _overview_scope(MemoryProposal.user_id, principal)

    mem_total = session.exec(
        select(func.count())
        .select_from(MemoryItem)
        .where(*([mem_scope] if mem_scope is not None else []))
    ).one()
    mem_active = session.exec(
        select(func.count())
        .select_from(MemoryItem)
        .where(
            MemoryItem.status == "active",
            *([mem_scope] if mem_scope is not None else []),
        )
    ).one()
    mem_archived = session.exec(
        select(func.count())
        .select_from(MemoryItem)
        .where(
            MemoryItem.status == "archived",
            *([mem_scope] if mem_scope is not None else []),
        )
    ).one()

    by_lane = {
        lane: cnt
        for lane, cnt in session.exec(
            select(MemoryItem.lane, func.count())
            .group_by(MemoryItem.lane)
            .where(*([mem_scope] if mem_scope is not None else []))
        ).all()
    }
    by_decay_class = {
        cls: cnt
        for cls, cnt in session.exec(
            select(MemoryItem.decay_class, func.count())
            .group_by(MemoryItem.decay_class)
            .where(*([mem_scope] if mem_scope is not None else []))
        ).all()
    }

    candidates_pending = session.exec(
        select(func.count())
        .select_from(MemoryCandidate)
        .where(
            MemoryCandidate.status == "pending_review",
            *([cand_scope] if cand_scope is not None else []),
        )
    ).one()
    checkpoints = session.exec(select(func.count()).select_from(MemoryCheckpoint)).one()
    proposals_pending = session.exec(
        select(func.count())
        .select_from(MemoryProposal)
        .where(
            MemoryProposal.status == "pending",
            *([prop_scope] if prop_scope is not None else []),
        )
    ).one()

    # 提取来源（provenance）分布：按 prompt 分组计数，让"记忆质量变差"可溯源
    prov_query = select(MemoryItem.provenance)
    if mem_scope is not None:
        prov_query = prov_query.where(mem_scope)
    provenance_rows = session.exec(prov_query).all()
    by_prompt: dict[str, int] = {}
    for prov in provenance_rows:
        if not prov or not isinstance(prov, dict):
            continue
        prompt = prov.get("prompt") or "unknown"
        by_prompt[prompt] = by_prompt.get(prompt, 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "memories": {
            "total": int(mem_total),
            "active": int(mem_active),
            "archived": int(mem_archived),
            "by_lane": {k: int(v) for k, v in by_lane.items()},
            "by_decay_class": {k: int(v) for k, v in by_decay_class.items()},
        },
        "candidates_pending_review": int(candidates_pending),
        "checkpoints": int(checkpoints),
        "proposals_pending": int(proposals_pending),
        "provenance_by_prompt": {k: int(v) for k, v in by_prompt.items()},
    }


def get_overview(principal=None) -> dict:
    """打开默认会话执行概览（只读）。

    归属（票 .scratch/mcp-identity-gaps/01a）：`build_overview` 本来就有
    `principal` 形参与收窄逻辑（票 05），这里只是没往下传——于是 MCP 的
    `mem_stats` 无论宿主传不传 `user_id` 都是全量计数。补一个形参透传。
    """
    with db.get_session() as s:
        return build_overview(s, principal=principal)
