"""轻量记忆概览（只读聚合，零写放大）。

给「我的记忆系统现在到底存了什么」一个一眼可见的答案：
- 记忆总数 / active / archived，按 lane 与 decay_class 分布
- 待审候选（pending_review）积压 —— 人工闸门待裁决
- 检查点（Checkpoint）版本数、待审提案（pending）数

build_overview(session) 是纯函数（测试直传临时 session，不 mock 内部逻辑）；
get_overview() 打开默认会话执行。
"""
from datetime import datetime, timezone

from sqlmodel import func, select

from lantai.models.tables import (
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryItem,
    MemoryProposal,
)
from lantai.storage import db


def build_overview(session) -> dict:
    """只读聚合：给定 session 汇总记忆系统现状。"""
    mem_total = session.exec(select(func.count()).select_from(MemoryItem)).one()
    mem_active = session.exec(
        select(func.count()).select_from(MemoryItem)
        .where(MemoryItem.status == "active")).one()
    mem_archived = session.exec(
        select(func.count()).select_from(MemoryItem)
        .where(MemoryItem.status == "archived")).one()

    by_lane = {lane: cnt for lane, cnt in session.exec(
        select(MemoryItem.lane, func.count())
        .group_by(MemoryItem.lane)).all()}
    by_decay_class = {cls: cnt for cls, cnt in session.exec(
        select(MemoryItem.decay_class, func.count())
        .group_by(MemoryItem.decay_class)).all()}

    candidates_pending = session.exec(
        select(func.count()).select_from(MemoryCandidate)
        .where(MemoryCandidate.status == "pending_review")).one()
    checkpoints = session.exec(
        select(func.count()).select_from(MemoryCheckpoint)).one()
    proposals_pending = session.exec(
        select(func.count()).select_from(MemoryProposal)
        .where(MemoryProposal.status == "pending")).one()

    # 提取来源（provenance）分布：按 prompt 分组计数，让"记忆质量变差"可溯源
    provenance_rows = session.exec(select(MemoryItem.provenance)).all()
    by_prompt: dict[str, int] = {}
    for prov in provenance_rows:
        if not prov or not isinstance(prov, dict):
            continue
        prompt = prov.get("prompt") or "unknown"
        by_prompt[prompt] = by_prompt.get(prompt, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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


def get_overview() -> dict:
    """打开默认会话执行概览（只读）。"""
    with db.get_session() as s:
        return build_overview(s)
