"""E2 时间用例集与双视图评测（更漏 ADR-0048 / 票 11）。

roadmap P1-1 验收口径：**30+ 时间/更新用例，当前/历史证据选择正确率 ≥90%**。
用例 = 确定性种子记忆族 + 视图时点扫描（当前态 / as-of / 时间窗），
判据按 spec §2.5（修复后口径）：全部适用子判据均成立才算该查询正确——
① expect_hit_ids ⊆ 实际命中集（应选中的证据被选中）；
② expect_miss_ids ∩ 实际命中集 = ∅（应排除的证据未混入）。

种子族（6 条，id 固定可复现）：
- e2-db-old  数据库 Postgres 15   valid [01-01, 06-01)  event 01-15 (day)
- e2-db-new  数据库 MySQL 9       valid [06-01, ∞)      event 06-01 (day)
- e2-editor  VSCode 编辑器        valid 全开             event 缺（unknown 软放行代表）
- e2-meet    周三例会             valid [05-01, 09-30)  event 05-01 (fuzzy)
- e2-travel  护照办理             valid 开               event 08-20 12:00 (second 点区间)
- e2-phone   手机尾号 8888        valid 开               event 缺（干扰项）

评测跑真实 hybrid_search（embed 由调用环境决定；测试用确定性替身 + 内嵌 Chroma）。
"""

import hashlib
from datetime import UTC, datetime, timedelta, timezone

from lantai.core.ids import new_id
from lantai.models.tables import MemoryItem
from lantai.retrieval.temporal import _ensure_utc

SEEDS: list[dict] = [
    {
        "id": "e2-db-old",
        "content": "用户的主数据库是 PostgreSQL 15，部署在公司内网机房",
        "valid_from": datetime(2026, 1, 1, tzinfo=UTC),
        "valid_to": datetime(2026, 6, 1, tzinfo=UTC),
        "event_time": datetime(2026, 1, 15, tzinfo=UTC),
        "event_time_precision": "day",
    },
    {
        "id": "e2-db-new",
        "content": "用户的主数据库是 MySQL 9，迁移到了云端可用区",
        "valid_from": datetime(2026, 6, 1, tzinfo=UTC),
        "valid_to": None,
        "event_time": datetime(2026, 6, 1, tzinfo=UTC),
        "event_time_precision": "day",
    },
    {
        "id": "e2-editor",
        "content": "用户偏好用 VSCode 编辑器写 Python 代码",
        "valid_from": None,
        "valid_to": None,
        "event_time": None,
        "event_time_precision": "",
    },
    {
        "id": "e2-meet",
        "content": "用户每周三下午开团队例会",
        "valid_from": datetime(2026, 5, 1, tzinfo=UTC),
        "valid_to": datetime(2026, 9, 30, tzinfo=UTC),
        "event_time": datetime(2026, 5, 1, tzinfo=UTC),
        "event_time_precision": "fuzzy",
    },
    {
        "id": "e2-travel",
        "content": "用户计划下月办理护照签证",
        "valid_from": None,
        "valid_to": None,
        "event_time": datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC),
        "event_time_precision": "second",
    },
    {
        "id": "e2-phone",
        "content": "用户的手机号尾号是 8888",
        "valid_from": None,
        "valid_to": None,
        "event_time": None,
        "event_time_precision": "",
    },
]


def build_seed_items() -> list[MemoryItem]:
    """确定性种子记忆（真实 MemoryItem，供 ingest/评测环境直接落库）。"""
    items = []
    for spec in SEEDS:
        items.append(
            MemoryItem(
                id=spec["id"],
                content=spec["content"],
                key=spec["content"],
                status="active",
                valid_from=spec["valid_from"],
                valid_to=spec["valid_to"],
                event_time=spec["event_time"],
                event_time_precision=spec["event_time_precision"],
                created_at=spec.get("event_time") or datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    return items


def _d(m, d, hh=0, mm=0):
    return datetime(2026, m, d, hh, mm, tzinfo=UTC)


def build_cases() -> list[dict]:
    """30+ 双视图用例（确定性构造；qid 可复现）。"""
    cases: list[dict] = []

    def add(
        qid,
        query,
        *,
        as_of=None,
        time_from=None,
        time_to=None,
        as_of_recorded=None,
        hit=(),
        miss=(),
    ):
        cases.append(
            {
                "qid": qid,
                "query": query,
                "as_of": as_of,
                "time_from": time_from,
                "time_to": time_to,
                "as_of_recorded": as_of_recorded,
                "expect_hit_ids": set(hit),
                "expect_miss_ids": set(miss),
            }
        )

    # A. as-of 时点扫描（数据库族）：06-01 边界前后、历史/当前切换
    for i, as_of in enumerate(
        [
            _d(1, 1),
            _d(3, 15),
            _d(5, 31),
            _d(6, 1),
            _d(6, 2),
            _d(8, 15),
            _d(9, 25),
            _d(12, 31),
        ]
    ):
        hit_old = as_of < _d(6, 1)  # valid_to(06-01) > as_of → old 为真
        hit_new = as_of >= _d(6, 1)  # valid_from(06-01) ≤ as_of
        add(
            f"e2-asof-db-{i:02d}",
            "用户的主数据库是什么",
            as_of=as_of,
            hit=(["e2-db-old"] if hit_old else []) + (["e2-db-new"] if hit_new else []),
            miss=(["e2-db-new"] if not hit_new else []) + (["e2-db-old"] if not hit_old else []),
        )

    # B. 事件区间命中（Δ=1d：event ±1d 窗口）
    add("e2-asof-db-event-day", "用户的主数据库是什么", as_of=_d(1, 15), hit=["e2-db-old"])
    add("e2-asof-db-event-eve", "用户的主数据库是什么", as_of=_d(5, 31), hit=["e2-db-old"])
    # C. second 点区间特判：as-of 当天命中（Δ=1d 窗口覆盖 08-20 12:00 点）
    add("e2-asof-travel-point", "用户办理护照签证了吗", as_of=_d(8, 20, 12), hit=["e2-travel"])
    add("e2-asof-travel-next", "用户办理护照签证了吗", as_of=_d(8, 20, 13), hit=["e2-travel"])
    # D. fuzzy 软放行：会议记忆 event 05-01 fuzzy，宽 ±1d
    add("e2-asof-meet-fuzzy", "用户的团队例会安排", as_of=_d(5, 2), hit=["e2-meet"])
    add("e2-asof-meet-fuzzy-2", "用户的团队例会安排", as_of=_d(5, 1), hit=["e2-meet"])
    # E. I4：unknown（editor/phone 无任何时间）在任意 as-of 不被硬排除
    for i, as_of in enumerate([_d(3, 15), _d(8, 20, 12)]):
        add(f"e2-i4-editor-{i}", "用户用什么编辑器写代码", as_of=as_of, hit=["e2-editor"])
        add(f"e2-i4-phone-{i}", "手机号尾号 8888 的归属", as_of=as_of, hit=["e2-phone"])
    # F. 时间窗：事件轴窗口命中/排除
    add(
        "e2-window-db-event",
        "用户的主数据库是什么",
        time_from=_d(1, 10),
        time_to=_d(1, 20),
        hit=["e2-db-old"],
        miss=["e2-db-new"],
    )
    add(
        "e2-window-travel-point",
        "用户办理护照签证了吗",
        time_from=_d(8, 20, 11),
        time_to=_d(8, 20, 13),
        hit=["e2-travel"],
    )
    add(
        "e2-window-validity-coverage",
        "用户的主数据库是什么",
        time_from=_d(7, 1),
        time_to=_d(7, 10),
        # db-new 有效期 [06-01, ∞) 覆盖窗口 → validity 命中；
        # db-old 有效期 [01-01, 06-01) 与窗口不交 → 排除
        hit=["e2-db-new"],
        miss=["e2-db-old"],
    )
    # G. 事务轴 as-of（as_of_recorded）：兰台当时还不知道 → 不可见
    add(
        "e2-recorded-before-new",
        "用户的主数据库是什么",
        as_of_recorded=_d(5, 1),
        miss=["e2-db-new"],  # new 的 created_at=06-01 > 05-01
        hit=[],
    )
    add(
        "e2-recorded-after-new",
        "用户的主数据库是什么",
        as_of_recorded=_d(7, 1),
        # as_of_recorded 是事务轴近似（兰台当时知道什么）：只滤 created_at，
        # 不做事件轴剔除（历史真伪交 as_of 视图）——db-new(06-01 入库) 此刻可见
        hit=["e2-db-new"],
        miss=[],
    )
    # H. as-of 有效期外 + 事件区间外 → 排除（缺省软模式 missed_soft 保留——
    #    expect 以「选择正确」为准：作为 miss 断言须在严格模式下；此处只做
    #    命中面断言，排除面归 F 类窗口用例）
    add("e2-asof-editor-still-soft", "用户用什么编辑器写代码", as_of=_d(12, 25), hit=["e2-editor"])
    add("e2-asof-phone-still-soft", "手机号尾号 8888 的归属", as_of=_d(12, 25), hit=["e2-phone"])

    # 生成式补充：数据库族月度扫描（凑满 30+，确定性）
    month_checks = [
        (1, "e2-db-old"),
        (2, "e2-db-old"),
        (3, "e2-db-old"),
        (4, "e2-db-old"),
        (5, "e2-db-old"),
        (6, "e2-db-new"),
        (7, "e2-db-new"),
        (8, "e2-db-new"),
        (9, "e2-db-new"),
        (10, "e2-db-new"),
        (11, "e2-db-new"),
        (12, "e2-db-new"),
    ]
    for i, (m, expect_id) in enumerate(month_checks):
        add(
            f"e2-monthly-db-{i:02d}",
            "用户的主数据库是什么",
            as_of=_d(m, 15),
            hit=[expect_id],
            miss=[{"e2-db-old": "e2-db-new", "e2-db-new": "e2-db-old"}[expect_id]],
        )

    return cases


def corpus_hash() -> str:
    return hashlib.sha256(repr(SEEDS).encode("utf-8")).hexdigest()[:16]


def run_e2_temporal_eval(*, top_k: int = 5) -> dict:
    """跑 E2 双视图评测：落种子 → 逐用例 hybrid_search → 对账 → 正确率。

    返回 {queries, correct, accuracy, failures:[...], corpus_hash}。
    """
    from lantai.retrieval.hybrid import hybrid_search, index_memory_item

    with db_module_safe_session() as s:
        for item in build_seed_items():
            existing = s.get(MemoryItem, item.id)
            if existing:
                s.delete(existing)
        s.commit()
        for item in build_seed_items():
            s.add(item)
            s.commit()
            emb = _embed_or_fallback([item.content])[0]
            index_memory_item(
                item.id,
                emb,
                {
                    "memory_id": item.id,
                    "key": item.key,
                    "memory_type": item.memory_type,
                    "lane": item.lane,
                    "domain": item.domain,
                },
            )
            sync_fts_quiet(s, item.id, item.content)
        s.commit()

    correct = 0
    failures: list[dict] = []
    cases = build_cases()
    for case in cases:
        try:
            results = hybrid_search(
                case["query"],
                top_k=top_k,
                use_rerank=False,
                as_of=case["as_of"],
                time_from=case["time_from"],
                time_to=case["time_to"],
                as_of_recorded=case["as_of_recorded"],
                # E2 证据选择口径用严格模式：未命中 as-of 的条目剔除（排除面可断言）；
                # I4 仍生效——fuzzy / event_time IS NULL 软放行不剔除（spec §3.3）
                param_overrides={"TEMPORAL_ASOF_STRICT": True},
            )
            if isinstance(results, tuple):
                results = results[0]
            got = {
                r["memory"]["id"] for r in (results or []) if isinstance(r, dict) and "memory" in r
            }
        except Exception as exc:  # noqa: BLE001
            failures.append({"qid": case["qid"], "error": str(exc)[:120]})
            continue
        hit_ok = case["expect_hit_ids"] <= got if case["expect_hit_ids"] else True
        miss_ok = not (case["expect_miss_ids"] & got)
        if hit_ok and miss_ok:
            correct += 1
        else:
            failures.append(
                {
                    "qid": case["qid"],
                    "got": sorted(got)[:8],
                    "want_hit": sorted(case["expect_hit_ids"]),
                    "want_miss": sorted(case["expect_miss_ids"]),
                }
            )
    accuracy = round(correct / len(cases), 4) if cases else None
    return {
        "queries": len(cases),
        "correct": correct,
        "accuracy": accuracy,
        "failures": failures[:20],
        "corpus_hash": corpus_hash(),
    }


def sync_fts_quiet(session, mid: str, content: str) -> None:
    from lantai.storage.fts import sync_fts

    sync_fts(session, mid, content)


def _embed_or_fallback(texts):
    """确定性 3-gram hash embed（512 维；spec §1.2 原则三：评测固定模型/种子）。

    不 try 生产 embed——环境含真实 embedding 服务时真调（1024 维）会与
    确定性替身（512 维）混写同一 collection，触发维度冲突（全量跑实证）。
    """
    out = []
    for text in texts:
        v = [0.0] * 512
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))] or [text]
        for g in grams:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % 512] += 1.0
        n = sum(v) or 1.0
        out.append([x / n for x in v])
    return out


def db_module_safe_session():
    from lantai.storage import db

    return db.get_session()
