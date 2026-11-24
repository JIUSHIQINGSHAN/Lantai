"""Step 8 验证回流——信号可靠性统计与只降权。

设计（方向五，已钉死）：
- 人工验证结果回流：pass/fail 记录到 SignalReliabilityStat
- **只降不升**：penalty 只降权，无任何回升路径
- penalty 叠加进 resolve_gating：某类信号反复验证失败 → tier_weight 打折
- 阈值用已有 settings：PENALTY_FAIL_STREAK / PENALTY_FAIL_RATE /
  PENALTY_MIN_SAMPLES / PENALTY_TTL_DAYS（零硬编码）
"""

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.parameters.trust_models import SignalReliabilityStat
from lantai.storage import db


def record_verification_result(
    venue_class: str, *, passed: bool, note: str = ""
) -> SignalReliabilityStat:
    """记录一次人工验证结果，更新可靠性统计。

    passed=True  → pass_count+1，streak 清零
    passed=False → fail_count+1，fail_streak+1
    """
    venue_class = ((venue_class or "").strip() or "unknown").lower()
    with db.get_session() as s:
        stat = s.exec(
            select(SignalReliabilityStat).where(SignalReliabilityStat.venue_class == venue_class)
        ).first()
        if stat is None:
            stat = SignalReliabilityStat(id=new_id("srs"), venue_class=venue_class)
            s.add(stat)
            s.flush()

        if passed:
            stat.pass_count += 1
            stat.fail_streak = 0
        else:
            stat.fail_count += 1
            stat.fail_streak += 1
        stat.last_verified_at = utcnow()
        s.add(stat)
        s.commit()
        s.refresh(stat)
        logger.info(
            "verification recorded: venue=%s passed=%s streak=%d fail=%d",
            venue_class,
            passed,
            stat.fail_streak,
            stat.fail_count,
        )
        return stat


def penalty_from_stat(stat) -> float:
    """按**已取到的**统计行计算降权系数（纯函数，不查库）。

    `reliability_penalty` 的判定体（阈值、TTL、只降不升）原样收在这里，
    区别只在于统计行由调用方给——列表页需要逐行算，逐行查库就是 N+1
    （`/verification/stats` 原先每行一次 `get_session()`）。
    `stat is None`（该类还没有统计行）→ 1.0。
    """
    if stat is None:
        return 1.0

    # TTL 过期 → 恢复 1.0（降权不永久，防历史偏见）
    if stat.last_verified_at is not None:
        from datetime import timedelta

        from lantai.core.time import utcnow as _now

        now = _now()
        last = stat.last_verified_at
        if last.tzinfo is None and now.tzinfo is not None:
            last = last.replace(tzinfo=now.tzinfo)
        if now - last > timedelta(days=settings.PENALTY_TTL_DAYS):
            return 1.0

    total = stat.pass_count + stat.fail_count
    if total < settings.PENALTY_MIN_SAMPLES:
        return 1.0

    fail_rate = stat.fail_count / total
    if stat.fail_streak >= settings.PENALTY_FAIL_STREAK or fail_rate >= settings.PENALTY_FAIL_RATE:
        # 只降不升：降权系数 = 1 - fail_rate（下限 0.5 保护，防归零）
        return round(max(1.0 - fail_rate, 0.5), 4)
    return 1.0


def reliability_penalty(venue_class: str) -> float:
    """计算某信号类别的降权系数（1.0=无降权，<1.0=降权）。

    规则（阈值来自 settings，只降不升）：
    - 样本不足（pass+fail < PENALTY_MIN_SAMPLES）→ 1.0（不动手）
    - fail_streak >= PENALTY_FAIL_STREAK 或 fail_rate >= PENALTY_FAIL_RATE → 降权
    - TTL：last_verified_at 超过 PENALTY_TTL_DAYS 未更新 → 降权失效恢复 1.0
    - 降权幅度 = fail_rate（有上限 0.5 保护，见 apply_penalty_to_weight）

    判定体在 `penalty_from_stat`（纯函数）；本函数只负责按类别取一次统计行。
    批量场景（列表页逐行算 penalty）请直接用 `penalty_from_stat(行)`，
    否则就是逐行查库的 N+1。
    """
    venue_class = ((venue_class or "").strip() or "unknown").lower()
    with db.get_session() as s:
        stat = s.exec(
            select(SignalReliabilityStat).where(SignalReliabilityStat.venue_class == venue_class)
        ).first()
    return penalty_from_stat(stat)


def apply_penalty_to_weight(base_weight: float, venue_class: str) -> float:
    """门控接入：tier_weight × reliability_penalty（只降不升）。"""
    return round(base_weight * reliability_penalty(venue_class), 4)
