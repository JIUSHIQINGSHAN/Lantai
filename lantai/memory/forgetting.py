import math

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage import db


def _lane_strength(importance: float, use_count: int, lane: str) -> float:
    """按 lane profile 计算记忆保持强度 S"""
    profile = settings.LANE_DECAY_PROFILES.get(lane, settings.LANE_DECAY_PROFILES["general"])
    base_s = profile["base_s"]
    boost = profile["importance_boost"]
    return base_s + boost * importance + 2 * math.log1p(use_count)


# ---- 票 03（v0.66 P0-4）：豁免分两档，两条归档路各自明确认哪些标记 ----
# 病灶：`is_exempt` 此前只挡 `ttl_expired`，`decay_score < ARCHIVE_DECAY_THRESHOLD`
# 那条路照样把高重要度/语义记忆归档——TTL 豁免被衰减路开了后门（实测 65 天即误杀
# importance=0.95 的 chat 泳道记忆、200 天即误杀 semantic 记忆）。
#
#   · `_decay_exempt`：**结构性豁免标记**（高重要度、语义/程序性衰减类）——声明
#     「本条不受衰减归档」，衰减路与 TTL 路都必须尊重；
#   · `_ttl_exempt`：60 天未使用突击的豁免集，口径与改动前的 `is_exempt` 逐字一致
#     （再加偏好/画像泳道与「用过」：`use_count + injection_count > 0`）。
#
# `use_count > 0` 与偏好泳道**不进** `_decay_exempt`，是刻意的：把「用过一次」做成
# 永久豁免等于让衰减归档整体空转（工单红线），而仓内已有冻结契约——
# `lantai/eval/chinese_memory_cases.py` 的 stale 用例（偏好泳道 + importance 0.1 +
# 200 天零使用必须归档）与 `test_forgetting_quality.py` 的 `stale_hit_rate == 0.0`。
# 使用保活走另一条路：注入回执回写 `last_used_at` / `injection_count`（修复目标 2），
# 它直接改变衰减公式的输入，不需要免疫标记，也就不会把语义做成「永生」。
#
# 巡检 2026-11-03 P0（改法①）：注入次数从 `use_count` 拆到 `injection_count`，
# 因为考功/三省把 `use_count` 当「显式反馈次数」用，混进注入会把天天在用的记忆
# 判成高频低效。遗忘这边两本账都算使用（`_usage_signal`），保活强度与 v0.66 等价。
_DECAY_EXEMPT_CLASSES = ("semantic", "procedural")
_TTL_EXEMPT_LANES = ("preference", "profile")


def _usage_signal(m) -> int:
    """遗忘眼里的「用过几次」：显式反馈次数 + 宿主注入回执次数。"""
    return int(getattr(m, "use_count", 0) or 0) + int(getattr(m, "injection_count", 0) or 0)


def _decay_exempt(m) -> bool:
    """结构性豁免标记：高重要度或语义/程序性衰减类——两条归档路都不得动它们。

    下限走 `settings.FORGET_IMPORTANCE_EXEMPTION_FLOOR`（P0-B「阈值可配置」），
    不再写死在模块常量：调高下限必须能真实放宽归档、调低必须能真实收紧，
    否则「可配置」只是文档上的承诺（回归见
    `tests/test_forgetting_exempt_archive.py::TestImportanceFloorIsConfigurable`）。
    """
    if getattr(m, "decay_class", None) in _DECAY_EXEMPT_CLASSES:
        return True
    return m.importance is not None and (m.importance >= settings.FORGET_IMPORTANCE_EXEMPTION_FLOOR)


def _ttl_exempt(m) -> bool:
    """working TTL 突击的豁免集（与改动前 `is_exempt` 逐字同口径，只是拆成命名判据）。"""
    return _decay_exempt(m) or (m.lane in _TTL_EXEMPT_LANES) or (_usage_signal(m) > 0)


def _forgetting_scope(principal):
    """遗忘候选集的归属条件（票 .scratch/readside-gaps/12）。

    admin / `principal=None` → None（不过滤）；否则
    `user_id == viewer OR IS NULL`。NULL 口径同票 03/04/06/09/10：
    真实库 615 行 NULL 属主 memoryitem，判不可见会让单人部署下的
    衰减与自动归档整体空转。

    `principal=None`（定时任务）保持全表——衰减是系统行为，
    收窄成空转会让 archive 门槛永不触发。口径刻意如此，见票据口径 4。
    """
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))


def apply_forgetting(principal=None, *, page_size: int = 500):
    """衰减 + 自动归档。

    - 计算每条记忆的 decay_score（指数衰减）
    - 跳过 |Δdecay| < 0.001 的更新，减少数据库无谓写入 (Ticket 2.4 [DD-06])
    - 分批 commit 减少 WAL 锁竞争 (Ticket 2.4 [DD-06])
    - decay 低于 ARCHIVE_DECAY_THRESHOLD 时自动转 archived
      （高重要度 / 语义 / 程序性等结构性豁免标记不受此路归档，票 03）
    - working memory 超过 TTL 且无帮助时转 archived
    - archived 记忆不参与检索（WHERE status='active'），但物理不删

    分页游标（票 .scratch/review-fixes-2026-10/09，审查 M9-4）：候选集按 id 序
    keyset 分批加载（每批 `page_size` 条，默认 500），不再 `all()` 一次性全表——
    内存与单查询耗时随批大小而非库容增长。判定逻辑、衰减公式、归档阈值与
    全量加载口径逐字一致；id 是带前缀 ULID（new_id），字典序即稳定全序，
    游标比较全部在库内完成。批间 commit 延续既有「每 100 条变更一 commit」
    语义（batch_size），页尾把不足 100 的余量落库并释放本页对象。

    归属（票 .scratch/readside-gaps/12）：此前候选集是全表，一个身份都不取——
    任何持 key 者都能把**别人**的记忆改成 archived，而 archived 没有 undo
    入口。宁 miss 不脏写：收窄后没有可处理的记忆就空跑，不动别人的。
    """
    if page_size < 1:
        raise ValueError(f"page_size 必须 ≥ 1，得到 {page_size}")
    now = utcnow()
    batch_size = 100
    with db.get_session() as s:
        batch_count = 0
        base_q = select(MemoryItem).where(MemoryItem.status == "active")
        scope = _forgetting_scope(principal)
        if scope is not None:
            base_q = base_q.where(scope)
        last_id: str | None = None
        while True:
            q = base_q
            if last_id is not None:
                q = q.where(MemoryItem.id > last_id)
            page = s.exec(q.order_by(MemoryItem.id).limit(page_size)).all()
            if not page:
                break
            # 游标在任何 commit 之前捕获：commit 会 expire 页内对象
            last_id = page[-1].id
            for m in page:
                changed = False
                # procedural 永不衰减：跳过衰减与归档判定，铁律天然浮顶
                if m.decay_class == "procedural":
                    if m.decay_score != 1.0:
                        m.decay_score = 1.0
                        changed = True
                else:
                    last = m.last_used_at or m.created_at
                    if last.tzinfo is not None and now.tzinfo is None:
                        last = last.replace(tzinfo=None)
                    elif last.tzinfo is None and now.tzinfo is not None:
                        last = last.replace(tzinfo=now.tzinfo)
                    days = max(0.0, (now - last).total_seconds() / 86400.0)
                    strength = _lane_strength(m.importance, _usage_signal(m), m.lane)
                    new_decay = math.exp(-days / strength)

                    # Ticket 2.4 [DD-06]: 跳过极微小更新
                    if abs(m.decay_score - new_decay) >= 0.001:
                        m.decay_score = new_decay
                        changed = True

                    # 自动归档：decay 跌破阈值 或 working memory 超 TTL 且零帮助。
                    # 票 03：两条路分别取自己的豁免集——衰减路也严格认豁免标记。
                    is_exempt = _ttl_exempt(m)
                    ttl_expired = (
                        m.tier == "working"
                        and days > settings.WORKING_MEMORY_TTL_DAYS
                        and m.helpful_count == 0
                        and not is_exempt
                    )
                    decay_breached = (
                        m.decay_score < settings.ARCHIVE_DECAY_THRESHOLD and not _decay_exempt(m)
                    )
                    if (decay_breached or ttl_expired) and m.status != "archived":
                        m.status = "archived"
                        changed = True

                if changed:
                    s.add(m)
                    batch_count += 1
                    if batch_count >= batch_size:
                        s.commit()
                        batch_count = 0

            # 批间 commit：页尾把不足 100 的余量落库（延续既有分批语义）
            if batch_count > 0:
                s.commit()
                batch_count = 0
            if len(page) < page_size:
                break
            # 释放已处理页的对象：会话不随周期累积全表引用，内存有界
            s.expunge_all()
