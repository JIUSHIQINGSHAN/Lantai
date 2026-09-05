"""autodream 蒸馏（一年内档，提前）：后台记忆合成 → 待审提案。

对照 OpenAI 2026-06 Dreaming（后台记忆合成）与 Mem0 报告：「后台合成」是 2026
主战场；Raw Drawer 已落地，语料积累条件满足，按调研建议提前到 P1 之后立即衔接。

最小版 v1：规则聚类（jieba 关键词）+ 确定性合并（去重、新值在前），产出
MemoryProposal(status=pending, decided_by="autodream") 交人工闸门裁决——
宁 miss 不脏写：只建提案，绝不自动应用；低置信度入 skipped 报告（不静默丢弃）。
LLM 精炼总结留作后续增强（当前为确定性、可复现的规则蒸馏）。
"""
import jieba
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryItem, MemoryProposal
from lantai.storage import db

_STOP = {"的", "了", "是", "在", "和", "与", "我", "你", "他", "她", "它", "有",
         "对", "把", "被", "这", "那", "也", "就", "都", "要", "会", "很", "等",
         "用", "为", "于", "到", "从", "及", "并", "或", "一个", "我们", "你们",
         "他们", "进行", "可以", "这个", "那个", "以及", "什么", "怎么"}


def _keywords(content: str) -> list[str]:
    return [w for w in jieba.lcut(content)
            if len(w) >= 2 and w.strip() and w not in _STOP]


def cluster_memories(items: list[MemoryItem],
                     min_size: int = 2) -> list[list[MemoryItem]]:
    """确定性聚类：同一 lane 且共享 ≥1 关键词的记忆归为一簇。

    贪心 + 按 (created_at, id) 排序保证可复现；簇 < min_size 丢弃。
    纯函数，零 DB（测试纪律：核心逻辑不 mock）。
    """
    ordered = sorted(items, key=lambda m: (m.created_at, m.id))
    clusters: list[list[MemoryItem]] = []
    cluster_lanes: list[str] = []
    cluster_keys: list[set[str]] = []
    for m in ordered:
        kws = set(_keywords(m.content))
        target = None
        for idx, (cl, ck) in enumerate(zip(cluster_lanes, cluster_keys, strict=False)):
            if m.lane == cl and (ck & kws):
                target = idx
                break
        if target is None:
            clusters.append([m])
            cluster_lanes.append(m.lane)
            cluster_keys.append(kws)
        else:
            clusters[target].append(m)
            cluster_keys[target] |= kws
    return [c for c in clusters if len(c) >= min_size]


def plan_distillation(cluster: list[MemoryItem]) -> dict:
    """规划一条蒸馏提案（纯函数，零 DB，零 LLM）。

    返回提案形状（proposal_type / evidence_ids / reason / proposed_patch /
    confidence）。宁 miss 不脏写：只规划，由 run_autodream_once 落成 pending
    提案，应用与否交人工闸门。
    """
    ordered = sorted(cluster, key=lambda m: (m.created_at, m.id))
    newest = ordered[-1]
    seen: set[str] = set()
    lines: list[str] = []
    for m in reversed(ordered):  # 新值在前（Chronos 语义）
        c = m.content.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        lines.append(c)
    n = len(ordered)
    return {
        "proposal_type": "add",
        "evidence_ids": [m.id for m in ordered],
        "reason": f"autodream 蒸馏：{cluster[0].lane} 簇 {n} 条记忆聚合"
                  f"（去重 {n - len(lines)} 条）",
        "proposed_patch": {
            "memory_type": "semantic",
            "key": newest.key or newest.content[:60],
            "content": "\n".join(f"- {c}" for c in lines),
            "lane": newest.lane,
            "structure": {},
        },
        "confidence": round(min(1.0, 0.5 + 0.15 * (n - 1)), 4),
    }


def run_autodream_once(namespace: str = "default", *,
                       dry_run: bool = True,
                       limit: int | None = None) -> dict:
    """执行一轮蒸馏：聚类 → 规划 → 落 pending 提案（dry_run 不写库）。

    返回 {"clusters", "plans", "created", "skipped"}；低置信度不静默丢弃，
    进 skipped 报告（宁 miss 不脏写）。
    """
    if not settings.AUTODREAM_ENABLED:
        return {"clusters": 0, "plans": 0, "created": 0,
                "skipped": ["AUTODREAM_ENABLED=false"]}
    with db.get_session() as s:
        q = select(MemoryItem).where(
            MemoryItem.status == "active",
            MemoryItem.namespace == namespace,
        )
        if limit:
            q = q.limit(limit)
        items = s.exec(q).all()
    clusters = cluster_memories(items, min_size=settings.AUTODREAM_MIN_CLUSTER)
    plans = [plan_distillation(c) for c in clusters]
    created = 0
    skipped: list[str] = []
    if not dry_run:
        with db.get_session() as s:
            for p in plans[:settings.AUTODREAM_MAX_DAILY]:
                if p["confidence"] < settings.AUTODREAM_MIN_CONFIDENCE:
                    skipped.append(f"low-conf:{p['confidence']}")
                    continue
                s.add(MemoryProposal(
                    id=new_id("prop"),
                    proposal_type=p["proposal_type"],
                    evidence_ids=p["evidence_ids"],
                    reason=p["reason"],
                    proposed_patch=p["proposed_patch"],
                    confidence=p["confidence"],
                    status=ProposalStatus.PENDING,
                    decided_by="autodream",
                ))
                created += 1
            s.commit()
    return {"clusters": len(clusters), "plans": len(plans),
            "created": created, "skipped": skipped}