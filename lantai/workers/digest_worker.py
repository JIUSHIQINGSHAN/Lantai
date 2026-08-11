"""盘点 worker——TTL 归档 + Daily Digest 每日盘点报告（Ticket 02/03）。

- run_candidate_ttl：每日清理超龄 pending_review 候选 → rejected（Ticket 02）
- run_digest_once：生成当日盘点报告 docs/memory-digest/YYYY-MM-DD.md（Ticket 03）
  五项统计：新增记忆 / 修改记忆 / 待审数 / 归档数 / 检索统计
"""
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlmodel import func, select

from lantai.core.scheduler import record_run
from lantai.core.settings import settings
from lantai.models.tables import (MemoryCandidate, MemoryItem,
                                    MemoryProposal, RetrievalEvent)
from lantai.services.candidate_service import run_candidate_ttl_once
from lantai.storage import db

# 仓库根 = lantai/workers/ → lantai/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DIGEST_DIR = _REPO_ROOT / "docs" / "memory-digest"

# 反思提案置信桶（回填校准报告区间，见 docs/memory-quality/reflect-calibration-2026-08-11.md）
_CONF_BUCKETS = (("0.5-0.6", 0.5, 0.6), ("0.6-0.7", 0.6, 0.7),
                 ("0.7-0.8", 0.7, 0.8), ("0.8-0.9", 0.8, 0.9),
                 ("0.9-1.0", 0.9, 1.0))


def run_candidate_ttl() -> dict:
    """每日 TTL 归档入口（scheduler job）。"""
    result = run_candidate_ttl_once()
    record_run("candidate_ttl")
    return result


# ── Daily Digest（Ticket 03）────────────────────────────────────────

def _digest_dir() -> Path:
    """报告输出目录：settings.DIGEST_OUTPUT_DIR 为空时默认仓库 docs/memory-digest。"""
    return Path(settings.DIGEST_OUTPUT_DIR) if settings.DIGEST_OUTPUT_DIR \
        else _DEFAULT_DIGEST_DIR


def _local_day_window_utc(day: date | None = None) -> tuple[datetime, datetime]:
    """本地日历日 → (start_utc_naive, end_utc_naive)。

    库内 created_at/updated_at 为 naive UTC；先把本地日界换算成 UTC，
    再与库值比较（避免 Chronos 时区类 bug）。
    """
    local_now = datetime.now().astimezone()
    if day is None:
        day = local_now.date()
    local_start = datetime.combine(day, time.min, tzinfo=local_now.tzinfo)
    local_end = local_start + timedelta(days=1)
    return (local_start.astimezone(timezone.utc).replace(tzinfo=None),
            local_end.astimezone(timezone.utc).replace(tzinfo=None))


def collect_digest_stats(day: date | None = None) -> dict:
    """聚合当日五项统计（真实 DB 查询，不 mock）。"""
    start, end = _local_day_window_utc(day)
    with db.get_session() as s:
        new_mem = s.exec(select(func.count()).select_from(MemoryItem)
                         .where(MemoryItem.created_at >= start,
                                MemoryItem.created_at < end)).one()
        modified_mem = s.exec(select(func.count()).select_from(MemoryItem)
                              .where(MemoryItem.updated_at >= start,
                                     MemoryItem.updated_at < end,
                                     MemoryItem.updated_at > MemoryItem.created_at)).one()
        total_mem = s.exec(select(func.count()).select_from(MemoryItem)).one()
        pending_total = s.exec(select(func.count()).select_from(MemoryCandidate)
                               .where(MemoryCandidate.status == "pending_review")).one()
        pending_new = s.exec(select(func.count()).select_from(MemoryCandidate)
                             .where(MemoryCandidate.status == "pending_review",
                                    MemoryCandidate.created_at >= start,
                                    MemoryCandidate.created_at < end)).one()
        archived_created_today = s.exec(select(func.count()).select_from(MemoryCandidate)
                                        .where(MemoryCandidate.status == "rejected",
                                               MemoryCandidate.created_at >= start,
                                               MemoryCandidate.created_at < end)).one()
        retr_total = s.exec(select(func.count()).select_from(RetrievalEvent)
                            .where(RetrievalEvent.created_at >= start,
                                   RetrievalEvent.created_at < end)).one()
        retr_zero = s.exec(select(func.count()).select_from(RetrievalEvent)
                           .where(RetrievalEvent.created_at >= start,
                                  RetrievalEvent.created_at < end,
                                  RetrievalEvent.zero_result == True)).one()  # noqa: E712
        retr_noise = s.exec(select(func.count()).select_from(RetrievalEvent)
                            .where(RetrievalEvent.created_at >= start,
                                   RetrievalEvent.created_at < end,
                                   RetrievalEvent.is_system_noise == True)).one()  # noqa: E712
        retr_avg_latency = s.exec(select(func.avg(RetrievalEvent.latency_ms))
                                  .where(RetrievalEvent.created_at >= start,
                                         RetrievalEvent.created_at < end)).one()
        # 反思/蒸馏统计：反思提案 = candidate_id IS NULL 的提案（零迁移标识）
        refl_created = s.exec(select(func.count()).select_from(MemoryProposal)
                              .where(MemoryProposal.candidate_id.is_(None),
                                     MemoryProposal.created_at >= start,
                                     MemoryProposal.created_at < end)).one()
        refl_applied = s.exec(select(func.count()).select_from(MemoryProposal)
                              .where(MemoryProposal.candidate_id.is_(None),
                                     MemoryProposal.status == "applied",
                                     MemoryProposal.applied_at >= start,
                                     MemoryProposal.applied_at < end)).one()
        refl_pending = s.exec(select(func.count()).select_from(MemoryProposal)
                              .where(MemoryProposal.candidate_id.is_(None),
                                     MemoryProposal.status == "pending",
                                     MemoryProposal.created_at >= start,
                                     MemoryProposal.created_at < end)).one()
        refl_rejected = s.exec(select(func.count()).select_from(MemoryProposal)
                               .where(MemoryProposal.candidate_id.is_(None),
                                      MemoryProposal.status == "rejected",
                                      MemoryProposal.created_at >= start,
                                      MemoryProposal.created_at < end)).one()
        # 今日新增反思提案的 类型×状态 分布（反馈回路/回填校准输入）
        refl_rows = s.exec(select(MemoryProposal.proposal_type,
                                  MemoryProposal.status, func.count())
                           .where(MemoryProposal.candidate_id.is_(None),
                                  MemoryProposal.created_at >= start,
                                  MemoryProposal.created_at < end)
                           .group_by(MemoryProposal.proposal_type,
                                     MemoryProposal.status)).all()
        refl_conf_values = s.exec(select(MemoryProposal.confidence)
                         .where(MemoryProposal.candidate_id.is_(None),
                                MemoryProposal.created_at >= start,
                                MemoryProposal.created_at < end)).all()
    by_type: dict[str, dict[str, int]] = {}
    for ptype, status, cnt in refl_rows:
        by_type.setdefault(ptype, {})[status] = int(cnt)
    conf_buckets = {label: 0 for label, _, _ in _CONF_BUCKETS}
    for conf in refl_conf_values:
        for label, lo, hi in _CONF_BUCKETS:
            if lo <= conf < hi or (label == "0.9-1.0" and conf == 1.0):
                conf_buckets[label] += 1
                break
    return {
        "day": day or datetime.now().astimezone().date(),
        "memories": {
            "new": int(new_mem),
            "modified": int(modified_mem),
            "total": int(total_mem),
        },
        "pending": {
            "total": int(pending_total),
            "new_today": int(pending_new),
        },
        "archived": {
            "created_today": int(archived_created_today),
            "ttl": 0,  # run_digest_once 先跑 TTL 后回填
        },
        "retrieval": {
            "total": int(retr_total),
            "zero_result": int(retr_zero),
            "noise": int(retr_noise),
            "avg_latency_ms": round(float(retr_avg_latency), 1) if retr_avg_latency else 0,
        },
        "reflection": {
            "created": int(refl_created),
            "applied": int(refl_applied),
            "pending": int(refl_pending),
            "rejected": int(refl_rejected),
            "by_type": by_type,
            "conf_buckets": conf_buckets,
        },
    }


def render_digest_markdown(stats: dict) -> str:
    """报告正文：当日摘要 + 待审候选提醒。"""
    day = stats["day"]
    m, p, a, r = stats["memories"], stats["pending"], stats["archived"], stats["retrieval"]
    rf = stats.get("reflection") or {"created": 0, "applied": 0, "pending": 0,
                                     "rejected": 0, "by_type": {}, "conf_buckets": {}}
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"# 记忆日报 {day}",
        "",
        f"> 生成时间：{now_str}",
        "",
        "## 今日概览",
        "",
        "| 统计 | 数值 |",
        "|---|---|",
        f"| 新增记忆 | {m['new']} |",
        f"| 修改记忆 | {m['modified']} |",
        f"| 记忆总量 | {m['total']} |",
        f"| 待审候选 | {p['total']}（今日新增 {p['new_today']}） |",
        f"| 今日归档 | 自动 TTL {a['ttl']} + 当日创建即归档 {a['created_today']} |",
        f"| 今日检索 | {r['total']} 次（零结果 {r['zero_result']}，"
        f"系统噪音 {r['noise']}，平均 {r['avg_latency_ms']}ms） |",
        f"| 反思提案 | 今日 {rf['created']}（自动应用 {rf['applied']}，待审 {rf['pending']}，拒绝 {rf['rejected']}） |",
        "",
    ]
    if rf["created"]:
        lines += ["", "## 反思提案分布（今日新增）", "",
                  "| 类型 | 自动应用 | 待审 | 拒绝 |", "|---|---|---|---|"]
        status_cols = ("applied", "pending", "rejected")
        totals = {k: 0 for k in status_cols}
        for ptype in ("add", "update", "merge", "deprecate"):
            row = rf.get("by_type", {}).get(ptype, {})
            cells = [str(row.get(k, 0)) for k in status_cols]
            for k in status_cols:
                totals[k] += row.get(k, 0)
            lines.append(f"| {ptype} | {' | '.join(cells)} |")
        lines.append(f"| 合计 | {' | '.join(str(totals[k]) for k in status_cols)} |")
        buckets = rf.get("conf_buckets", {})
        if buckets:
            lines += ["", "置信桶（今日新增）：" +
                      " ".join(f"[{label}]×{buckets.get(label, 0)}"
                               for label, _, _ in _CONF_BUCKETS)]
    if p["total"]:
        lines += [
            "## 待审候选提醒",
            "",
            f"有 **{p['total']}** 条候选等待裁决（7 天未处理将自动归档为 rejected）。",
            "处理入口：`GET /candidates/pending` + `POST /candidates/{id}/review`，",
            "或在 Hermes 里说『查看待审候选』。",
            "",
        ]
    return "\n".join(lines)


def write_digest_report(stats: dict) -> Path:
    """写当日报告文件（YYYY-MM-DD.md），返回路径。"""
    out_dir = _digest_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stats['day'].isoformat()}.md"
    path.write_text(render_digest_markdown(stats), encoding="utf-8")
    return path


def run_digest_once(day: date | None = None) -> dict:
    """生成当日盘点报告：先跑 TTL 归档（使归档数准确），再聚合统计并落盘。"""
    ttl = run_candidate_ttl_once()
    stats = collect_digest_stats(day)
    stats["archived"]["ttl"] = int(ttl.get("archived", 0))
    path = write_digest_report(stats)
    record_run("digest")
    return {"ok": True, "day": stats["day"].isoformat(), "path": str(path),
            "content": render_digest_markdown(stats), "stats": stats}


def load_today_digest() -> dict:
    """今日报告（不存在则生成一次）；REST `GET /digest/today` 与 MCP `get_digest` 入口。"""
    day = datetime.now().astimezone().date()
    path = _digest_dir() / f"{day.isoformat()}.md"
    if not path.exists():
        return run_digest_once(day)
    stats = collect_digest_stats(day)
    return {"ok": True, "day": day.isoformat(), "path": str(path),
            "content": path.read_text(encoding="utf-8"), "stats": stats}
