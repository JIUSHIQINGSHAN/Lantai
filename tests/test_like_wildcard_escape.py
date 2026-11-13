r"""票 28：LIKE 通配与裸标点降级臂过匹配收口（三处 LIKE 点统一转义真源）。

病灶（督导现场复核 HEAD `14bb3412`，行号以此为准）：
1. `lantai/retrieval/hybrid.py:1184` `content.like(f"%{t}%")` 无 escape——`_`
   是单字符通配，「查询 foo_bar」把 `fooXbar` 也捞进来；
2. `hybrid.py:1172-1173` 裸字回退段：`clean_q_no_punct` 清洗后为空（纯标点
   查询）时 tokens 退成查询原文逐字符，`%`/`_` 直进 LIKE 模式串 → `LIKE
   '%%%'` 匹配全库；
3. `lantai/cognition/shiyi.py:187` 同病姊妹点（`[\w一-龥]` 保留 `_`，裸 `_`
   查询全表扫描）；
4. `lantai/cognition/chenfeng.py:230` `contains(term_clean)` 底层 LIKE 未转义；
5. `lantai/storage/fts.py:289/:351` 兜底 catch 内联 `import logging` 而非模块
   级 logger（留痕面，顺手统一）。

降级形态复现（镜像票面探针）：embed 臂抛错（外部 LLM 边界替身，纪律允许）
→ hybrid 走 `_keyword_fallback` → LIKE 兜底臂。FTS5 MATCH 面无注入路径
（票面探针实锤），本票不碰 MATCH 净化器与分词器。

拍板：**转义**（督导倾向采纳）——剥 `_` 会废掉「查询 foo_bar 应命中字面
foo_bar」这条验收；三处 .like 点统一 `lantai/core/text.like_escape` 单一
真源 + `escape="\"`，chenfeng 的 contains 用 SQLAlchemy 原生 autoescape。
"""

import json
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cognition.chenfeng import ChenfengEngine
from lantai.cognition.shiyi import ShiyiEngine
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search

ACTIVE_ROWS = [
    ("mem-lit", "任务清单模板 foo_bar 备份"),
    ("mem-x", "任务清单模板 fooXbar 备份"),
    ("mem-pct", "服务器 100% CPU 占用告警"),
    ("mem-cjk", "今天天气不错适合散步"),
    ("mem-other", "随机无关记录 alpha beta gamma"),
]
ARCHIVED_ROWS = [
    ("mem-arch-lit", "归档件 foo_bar 备份"),
    ("mem-arch-x", "归档件 fooXbar 备份"),
    ("mem-arch-other", "归档件无关记录 zeta"),
]


def _result_ids(results):
    """hybrid 结果的 memory id 集（兼容 id/memory 两种承载形状）。"""
    ids = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        mid = r.get("id") or (r.get("memory") or {}).get("id")
        if mid:
            ids.add(mid)
    return ids


@pytest.fixture()
def like_env():
    """真内存 SQLite + 全表 + 真 FTS + 真 hybrid/拾遗/尘封链路。

    embed 臂抛错复现降级形态（外部 LLM 边界替身，纪律允许；降级正是本票
    的被测面）。种子 5 行 active + 3 行 archived：字面 foo_bar / fooXbar /
    100% CPU / 无关行，用于验证「不全库返回」与「字面命中保留」。"""
    import lantai.models.tables  # noqa: F401
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with session_factory() as s:
        for mid, content in ACTIVE_ROWS:
            s.add(MemoryItem(id=mid, content=content, status="active"))
        for mid, content in ARCHIVED_ROWS:
            s.add(MemoryItem(id=mid, content=content, status="archived"))
        s.commit()

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch(
            "lantai.retrieval.hybrid.embed",
            side_effect=RuntimeError("embed down: 票 28 降级形态复现"),
        ),
        patch(
            "lantai.llm.client.embed",
            side_effect=RuntimeError("embed down: 票 28 降级形态复现"),
        ),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
        patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock),
    ):
        yield session_factory, engine


# ── 红测主臂：hybrid 降级 LIKE 臂 ─────────────────────────────────


def test_hybrid_pure_wildcard_percent_not_full_library(like_env):
    """查询 `%`（纯标点，裸字回退路径）：不得返回全库 5 行。

    现状：punct 清洗后 tokens 空 → 逐字符回退 → LIKE '%%%%' → 全库。
    修复后：裸字 token 剥除通配符、剥完为空 → 该臂空手。"""
    session_factory, engine = like_env
    results = hybrid_search(query="%", top_k=10)
    ids = _result_ids(results)
    assert len(ids) < len(ACTIVE_ROWS), (
        f"纯通配查询返回全库 {len(ids)}/{len(ACTIVE_ROWS)} 行（票 28 病灶 2："
        f"裸字回退把 % 直进 LIKE 模式串）——实际命中：{sorted(ids)}"
    )


def test_hybrid_pure_wildcard_underscore_not_full_library(like_env):
    """查询 `_`（裸字回退 + LIKE 单字符通配）：不得返回全库 5 行。"""
    session_factory, engine = like_env
    results = hybrid_search(query="_", top_k=10)
    ids = _result_ids(results)
    assert len(ids) < len(ACTIVE_ROWS), (
        f"下划线查询返回全库 {len(ids)}/{len(ACTIVE_ROWS)} 行（票 28 病灶 1/2）"
        f"——实际命中：{sorted(ids)}"
    )


def test_hybrid_all_punct_mix_not_full_library(like_env):
    """查询 `%%_%_`（混合通配裸字回退）：不得返回全库 5 行。"""
    session_factory, engine = like_env
    results = hybrid_search(query="%%_%_", top_k=10)
    ids = _result_ids(results)
    assert len(ids) < len(ACTIVE_ROWS), (
        f"混合通配查询返回全库 {len(ids)}/{len(ACTIVE_ROWS)} 行（票 28 病灶 2）"
    )


def test_hybrid_foo_bar_underscore_is_literal(like_env):
    """查询 `foo_bar`：字面命中 foo_bar 行、不得把 fooXbar 当通配捞进来
    （督导拍板：转义而非剥离——剥离会废掉字面命中验收）。"""
    session_factory, engine = like_env
    results = hybrid_search(query="foo_bar", top_k=10)
    ids = _result_ids(results)
    assert "mem-lit" in ids, f"字面 foo_bar 行必须命中（票 28）：{sorted(ids)}"
    assert "mem-x" not in ids, f"fooXbar 被下划线通配捞进（票 28 病灶 1）：{sorted(ids)}"


def test_hybrid_100_percent_query_hits_literal_row(like_env):
    """查询 `100%`：字面命中 100% CPU 行（转义后 % 作为普通字符参与匹配；
    修复前经 punct 清洗走 %100%，行为碰巧一致——本臂为绿通道锁，如实申报）。"""
    session_factory, engine = like_env
    results = hybrid_search(query="100%", top_k=10)
    ids = _result_ids(results)
    assert "mem-pct" in ids, f"100% CPU 行必须字面命中：{sorted(ids)}"
    assert len(ids) < len(ACTIVE_ROWS)


# ── 红测姊妹臂：shiyi 拾遗扫描（/shiyi/remedy 直连入口）──────────────


def test_shiyi_wildcard_scan_not_full_library(like_env):
    """查询 `___`（拾遗 token 来源 `re.findall(r"[\\w一-龥]")` 保留 `_`）：
    SQL 预筛 LIKE 臂不得扫全库——scanned_count 是扫描侧过匹配的直接量。"""
    session_factory, engine = like_env
    eng = ShiyiEngine(session=session_factory(), principal=None)
    res = eng.remedy_recall(query="___", limit=10)
    assert res["scanned_count"] < len(ACTIVE_ROWS), (
        f"拾遗扫描扫了全库 {res['scanned_count']}/{len(ACTIVE_ROWS)} 行"
        "（票 28 病灶 3：裸 _ 直进 LIKE 模式串）"
    )


def test_shiyi_percent_scan_not_full_library(like_env):
    """查询 `%%`（拾遗 raw_chars 剥 % 后若成空 token、LIKE 兜底行为）：
    扫描面不得全库（转义后纯通配 token 不再命中任何行）。"""
    session_factory, engine = like_env
    eng = ShiyiEngine(session=session_factory(), principal=None)
    res = eng.remedy_recall(query="%%", limit=10)
    assert res["scanned_count"] < len(ACTIVE_ROWS), (
        f"拾遗扫描扫了全库 {res['scanned_count']}/{len(ACTIVE_ROWS)} 行（票 28 病灶 3）"
    )


# ── 红测姊妹臂：chenfeng 尘封检索（列表检索 API）────────────────────


def test_chenfeng_underscore_is_literal(like_env):
    """尘封检索查询 `foo_bar`：字面命中归档 foo_bar 行、不得通配捞进
    fooXbar 归档行（contains 底层 LIKE 未转义，票 28 病灶 4）。"""
    session_factory, engine = like_env
    eng = ChenfengEngine(session=session_factory(), principal=None)
    res = eng.search_archived(query="foo_bar")
    ids = {item["id"] for item in res["items"]}
    assert res["total"] < len(ARCHIVED_ROWS), (
        f"尘封检索返回全库 {res['total']}/{len(ARCHIVED_ROWS)} 行（票 28 病灶 4）"
    )
    assert "mem-arch-lit" in ids, f"字面 foo_bar 归档行必须命中：{sorted(ids)}"
    assert "mem-arch-x" not in ids, f"fooXbar 归档行被下划线通配捞进（票 28 病灶 4）：{sorted(ids)}"
