"""盘点 worker——TTL 归档 + Daily Digest 每日盘点报告（Ticket 02/03）。

- run_candidate_ttl：每日清理超龄 pending_review 候选 → rejected（Ticket 02）
- run_digest_once：生成当日盘点报告 docs/memory-digest/YYYY-MM-DD.md（Ticket 03）
  五项统计：新增记忆 / 修改记忆 / 待审数 / 归档数 / 检索统计
"""
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlmodel import func, select

from lantai.core.scheduler import record_run
from lantai.core.settings import settings
from lantai.models.tables import (
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    ReflectRun,
    RetrievalEvent,
)
from lantai.services.candidate_service import run_candidate_ttl_once
from lantai.storage import db

# 仓库根 = lantai/workers/ → lantai/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DIGEST_DIR = _REPO_ROOT / "docs" / "memory-digest"

# 反思提案置信桶（ADR-0002 零硬编码：边界走 settings，见校准报告）
_CONF_BUCKETS = tuple(settings.DIGEST_CONF_BUCKETS)
_TYPE_ORDER = ("add", "update", "merge", "deprecate")
_STATUS_COLS = ("applied", "pending", "rejected", "other")


def _type_status_rows(refl: dict) -> list[str]:
    """类型×状态表行（含合计）——日报与校准报告共用渲染。"""
    totals = {k: 0 for k in _STATUS_COLS}
    rows = []
    for ptype in _TYPE_ORDER:
        row = refl.get("by_type", {}).get(ptype, {})
        cells = [str(row.get(k, 0)) for k in _STATUS_COLS]
        for k in _STATUS_COLS:
            totals[k] += row.get(k, 0)
        rows.append(f"| {ptype} | {' | '.join(cells)} |")
    rows.append("| 合计 | " + " | ".join(str(totals[k]) for k in _STATUS_COLS) + " |")
    return rows


def _aggregate_reflection(s, start: datetime, end: datetime) -> dict:
    """反思提案窗口聚合：created/applied/pending/rejected/other + 类型×状态 + 置信桶。

    反思提案 = `decided_by == 'reflect'`（reflector 唯一打标；evolve auto /
    autodream 等其他无候选提案不混入——校准口径修复 2026-08-15）。start/end 为
    naive UTC（库内时间约定）。全部计数统一用 created_at 窗口（「今日创建」语义，与
    渲染行一致）；applied 也按创建窗口而非 applied_at，避免跨日应用导致「今日 0（自动
    应用 1）」自相矛盾。other = 窗口内 created 中非三类状态者（approved/
    rolled_back 等），保证 applied+pending+rejected+other == created。
    """
    created = s.exec(select(func.count()).select_from(MemoryProposal)
                     .where(MemoryProposal.decided_by == "reflect",
                            MemoryProposal.created_at >= start,
                            MemoryProposal.created_at < end)).one()
    applied = s.exec(select(func.count()).select_from(MemoryProposal)
                     .where(MemoryProposal.decided_by == "reflect",
                            MemoryProposal.status == "applied",
                            MemoryProposal.created_at >= start,
                            MemoryProposal.created_at < end)).one()
    pending = s.exec(select(func.count()).select_from(MemoryProposal)
                     .where(MemoryProposal.decided_by == "reflect",
                            MemoryProposal.status == "pending",
                            MemoryProposal.created_at >= start,
                            MemoryProposal.created_at < end)).one()
    rejected = s.exec(select(func.count()).select_from(MemoryProposal)
                      .where(MemoryProposal.decided_by == "reflect",
                             MemoryProposal.status == "rejected",
                             MemoryProposal.created_at >= start,
                             MemoryProposal.created_at < end)).one()
    other = s.exec(select(func.count()).select_from(MemoryProposal)
                   .where(MemoryProposal.decided_by == "reflect",
                          MemoryProposal.created_at >= start,
                          MemoryProposal.created_at < end,
                          MemoryProposal.status.not_in(
                              ["applied", "pending", "rejected"]))).one()
    rows = s.exec(select(MemoryProposal.proposal_type,
                         MemoryProposal.status, func.count())
                  .where(MemoryProposal.decided_by == "reflect",
                         MemoryProposal.created_at >= start,
                         MemoryProposal.created_at < end)
                  .group_by(MemoryProposal.proposal_type,
                            MemoryProposal.status)).all()
    conf_values = s.exec(select(MemoryProposal.confidence)
                         .where(MemoryProposal.decided_by == "reflect",
                                MemoryProposal.created_at >= start,
                                MemoryProposal.created_at < end)).all()
    by_type: dict[str, dict[str, int]] = {}
    for ptype, status, cnt in rows:
        by_type.setdefault(ptype, {})[status] = int(cnt)
    conf_buckets = {label: 0 for label, _, _ in _CONF_BUCKETS}
    for conf in conf_values:
        for label, lo, hi in _CONF_BUCKETS:
            if lo <= conf < hi or (label == "0.9-1.0" and conf == 1.0):
                conf_buckets[label] += 1
                break
        else:
            conf_buckets["其他"] = conf_buckets.get("其他", 0) + 1
    return {
        "created": int(created),
        "applied": int(applied),
        "pending": int(pending),
        "rejected": int(rejected),
        "other": int(other),
        "by_type": by_type,
        "conf_buckets": conf_buckets,
    }


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
    return (local_start.astimezone(UTC).replace(tzinfo=None),
            local_end.astimezone(UTC).replace(tzinfo=None))


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
        refl = _aggregate_reflection(s, start, end)
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
        "reflection": refl,
    }


def render_digest_markdown(stats: dict) -> str:
    """报告正文：当日摘要 + 待审候选提醒。"""
    day = stats["day"]
    m, p, a, r = stats["memories"], stats["pending"], stats["archived"], stats["retrieval"]
    rf = stats.get("reflection") or {"created": 0, "applied": 0, "pending": 0,
                                     "rejected": 0, "other": 0,
                                     "by_type": {}, "conf_buckets": {}}
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
                  "| 类型 | 自动应用 | 待审 | 拒绝 | 其他 |",
                  "|---|---|---|---|---|"]
        lines += _type_status_rows(rf)
        buckets = rf.get("conf_buckets", {})
        if buckets:
            labels = [b[0] for b in _CONF_BUCKETS]
            labels += [k for k in buckets if k not in labels]
            lines += ["", "置信桶（今日新增）：" +
                      " ".join(f"[{label}]×{buckets.get(label, 0)}"
                               for label in labels)]
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


def collect_calibration_stats(days: int | None = None) -> dict:
    """观察期回填输入：窗口内反思提案分布 + 裁决原因 + 水位。

    对标 dry-run 校准报告（docs/memory-quality/reflect-calibration-2026-08-11.md），
    8/18 观察期结束后生成真实分布回填表。窗口按 naive UTC 计算（库内约定），
    默认天数取 REFLECT_IMPORTANCE_WINDOW_DAYS（水位/提案同窗口）。
    """
    if days is None:
        days = settings.REFLECT_IMPORTANCE_WINDOW_DAYS
    # 窗口边界秒级截断 + 1s 顶边过悬（2026-08-15 竞态修复）：微秒精度下
    # 「写入毫秒前 + 采样 now」可能同秒撞界（run_at < end 字符串比较失败），
    # 秒级边界 + 1s 过悬使窗口对采样时刻免疫
    end = (datetime.now(UTC).replace(tzinfo=None, microsecond=0)
           + timedelta(seconds=1))
    start = end - timedelta(days=days)
    with db.get_session() as s:
        refl = _aggregate_reflection(s, start, end)
        water = s.exec(select(func.coalesce(func.sum(MemoryItem.importance), 0.0))
                       .where(MemoryItem.created_at >= start,
                              MemoryItem.created_at < end)).one()
        reason_rows = s.exec(select(MemoryProposal.decision_reason, func.count())
                             .where(MemoryProposal.decided_by == "reflect",
                                    MemoryProposal.status == "rejected",
                                    MemoryProposal.decision_reason != "",
                                    MemoryProposal.created_at >= start,
                                    MemoryProposal.created_at < end)
                             .group_by(MemoryProposal.decision_reason)
                             .order_by(func.count().desc())
                             .limit(10)).all()
        # 反思运行记录：区分 空闲/正常/异常/LLM 失败（观察期去噪）
        run_rows = s.exec(select(ReflectRun.skipped, ReflectRun.error,
                                 ReflectRun.curate_failed,
                                 ReflectRun.rejecter_failed,
                                 ReflectRun.proposals_created)
                          .where(ReflectRun.run_at >= start,
                                 ReflectRun.run_at < end)).all()
    runs = {
        "total": len(run_rows),
        "idle": sum(1 for r in run_rows if r[0] == "idle"),
        "errored": sum(1 for r in run_rows if r[1]),
        "llm_failed": sum(1 for r in run_rows
                          if not r[1] and (r[2] or (r[3] or 0) > 0)),
        "productive": sum(1 for r in run_rows
                          if r[0] != "idle" and not r[1] and not r[2]
                          and not (r[3] or 0) and r[4] > 0),
        "zero_outcome": sum(1 for r in run_rows
                            if r[0] != "idle" and not r[1] and not r[2]
                            and not (r[3] or 0) and r[4] == 0),
    }
    return {
        "window_days": days,
        "reflection": refl,
        "water_level": round(float(water), 2),
        "reason_top": [(r, int(c)) for r, c in reason_rows],
        "runs": runs,
    }


def render_calibration_markdown(stats: dict) -> str:
    """回填校准报告正文（markdown；真实观察数据 → 阈值二次校准）。"""
    refl = stats["reflection"]
    lines = [
        "# 反思阈值回填校准（真实观察数据）",
        "",
        f"> 观察窗口：最近 {stats['window_days']} 天 | "
        f"水位（窗口内 importance 累加）：{stats['water_level']}",
        "",
        "## 反思提案分布（窗口新增）",
        "",
        "| 类型 | 自动应用 | 待审 | 拒绝 | 其他 |",
        "|---|---|---|---|---|",
    ]
    lines += _type_status_rows(refl)
    buckets = refl.get("conf_buckets", {})
    if buckets:
        lines += ["", "## 置信桶（窗口新增）", "", "| 桶 | 数量 |", "|---|---|"]
        labels = [b[0] for b in _CONF_BUCKETS]
        labels += [k for k in buckets if k not in labels]
        for label in labels:
            lines.append(f"| {label} | {buckets.get(label, 0)} |")
    if stats.get("reason_top"):
        lines += ["", "## 拒绝原因 Top（决策反馈回路）", "", "| 原因 | 次数 |", "|---|---|"]
        for reason, cnt in stats["reason_top"]:
            lines.append(f"| {reason} | {cnt} |")
    runs = stats.get("runs") or {"total": 0, "idle": 0, "errored": 0,
                                 "llm_failed": 0, "productive": 0,
                                 "zero_outcome": 0}
    lines += ["", "## 反思运行记录（窗口内）", "", "| 指标 | 值 |", "|---|---|",
              f"| 运行次数 | {runs['total']} |",
              f"| 空闲（无候选且水位不足） | {runs['idle']} |",
              f"| 异常中断 | {runs['errored']} |",
              f"| LLM 失败（宁 miss 空降级） | {runs['llm_failed']} |",
              f"| 产出提案的运行 | {runs['productive']} |",
              f"| 正常但零产出 | {runs['zero_outcome']} |"]
    lines += ["", "## 待回填结论（对标 dry-run 推荐）", "",
              "- A 水位触发：REFLECT_IMPORTANCE_POOL 是否保持 5.0",
              "- B 自动应用分流：REFLECT_AUTO_APPLY_CONF 是否保持 0.7",
              "- C 落库底线：REFLECT_MIN_CONFIDENCE 是否保持 0.5"]
    return "\n".join(lines) + "\n"


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
