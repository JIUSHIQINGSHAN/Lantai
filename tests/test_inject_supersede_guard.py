"""注入前更正闸（记忆研究周报 2026-11-03 P0-2）。

问题：`apply_supersede_correction` 更正后旧记忆仍是 status=active（有意保留历史），
检索层只把它压到新值之下；注入帧把「住武汉」和「搬到荆州」并列、旧值不带任何
标记。本文件钉住：当前态问句不再看到旧值；回看问句、或新值不在结果里时，旧值
留下但带「已更正」标记；开关关闭时逐字旧行为。

集成用例不 mock 更正链：真 MemoryItem + 真 apply_supersede_correction + 真 build_context
（内存 SQLite，向量库用假库按顺序返回 id）。
"""

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)

CURRENT_Q = "我现在住在哪个城市，通勤怎么安排比较好？"
RETRO_Q = "我以前住在哪个城市？"
OLD_TEXT = "用户住在武汉，在光谷上班"
NEW_TEXT = "用户已经搬到荆州，现在住在荆州"


def _load_hook():
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 纯函数


def _res(mid, content, score, **mem):
    m = {"id": mid, "content": content, "lifecycle_status": "active", "superseded_by": None}
    m.update(mem)
    return {"id": mid, "score": score, "memory": m}


def _old(**extra):
    base = {
        "lifecycle_status": "superseded",
        "superseded_by": "mem_new",
        "valid_to": "2026-10-25T20:00:00+00:00",
    }
    base.update(extra)
    return _res("mem_old", OLD_TEXT, 0.61, **base)


def _new():
    return _res("mem_new", NEW_TEXT, 0.62)


def test_guard_drops_superseded_when_successor_present():
    """当前态问句 + 新值在结果里 → 旧值不注入。"""
    mod = _load_hook()
    out = mod._apply_supersede_guard([_new(), _old()], CURRENT_Q)
    assert [r["id"] for r in out] == ["mem_new"]


def test_guard_keeps_marked_old_for_retrospective_query():
    """回看问句（「以前」）：旧值保留，正文前带失效日标记，新值不动。"""
    mod = _load_hook()
    out = mod._apply_supersede_guard([_new(), _old()], RETRO_Q)
    assert [r["id"] for r in out] == ["mem_new", "mem_old"]
    assert out[0]["memory"]["content"] == NEW_TEXT
    old_content = out[1]["memory"]["content"]
    assert old_content.startswith("[已更正：")
    assert old_content.endswith(OLD_TEXT)


def test_guard_keeps_marked_old_when_successor_absent():
    """新值没被召回：旧值不丢（否则模型两条都看不到），但必须带标记。"""
    mod = _load_hook()
    out = mod._apply_supersede_guard([_old()], CURRENT_Q)
    assert len(out) == 1
    assert "仅作历史" in out[0]["memory"]["content"]


def test_marker_date_uses_display_timezone():
    """失效日按注入显示时区渲染：UTC 10-25 20:00 = 北京时间 10-26。"""
    mod = _load_hook()
    with patch.object(mod.settings, "SHELL_HOOK_INJECT_TZ", "Asia/Shanghai"):
        out = mod._apply_supersede_guard([_old()], CURRENT_Q)
    assert out[0]["memory"]["content"].startswith("[已更正：2026-10-26 起不再成立")
    with patch.object(mod.settings, "SHELL_HOOK_INJECT_TZ", "UTC"):
        out = mod._apply_supersede_guard([_old()], CURRENT_Q)
    assert out[0]["memory"]["content"].startswith("[已更正：2026-10-25 起不再成立")


def test_marker_falls_back_to_superseded_at_then_no_date():
    """valid_to 缺失回落 superseded_at；两者都缺或是脏串则不带日期，不硬造、不抛错。"""
    mod = _load_hook()
    with patch.object(mod.settings, "SHELL_HOOK_INJECT_TZ", "UTC"):
        out = mod._apply_supersede_guard(
            [_old(valid_to=None, superseded_at="2026-10-30T01:00:00+00:00")], CURRENT_Q
        )
        assert "2026-10-30" in out[0]["memory"]["content"]
        out = mod._apply_supersede_guard([_old(valid_to="not-a-date")], CURRENT_Q)
        assert out[0]["memory"]["content"].startswith("[已更正：不再成立，仅作历史] ")


def test_guard_ignores_superseded_without_pointer_and_active_items():
    """第一版只认显式更正链：没有 superseded_by 的 superseded、普通 active 条目逐字不动。"""
    mod = _load_hook()
    orphan = _old(superseded_by=None)
    plain = _res("mem_x", "用户喜欢喝乌龙茶", 0.7)
    items = [plain, orphan]
    out = mod._apply_supersede_guard(items, CURRENT_Q)
    assert out == items
    assert out[1]["memory"]["content"] == OLD_TEXT


def test_guard_does_not_mutate_input():
    """不改入参：检索结果还会给检索日志等下游用，标记只进注入副本。"""
    mod = _load_hook()
    old = _old()
    old["document"] = OLD_TEXT
    mod._apply_supersede_guard([old], CURRENT_Q)
    assert old["memory"]["content"] == OLD_TEXT
    assert old["document"] == OLD_TEXT


def test_guard_prefixes_document_field():
    """无 MemoryItem 对应时 build_context 走 document 字段，标记也要跟到那里。"""
    mod = _load_hook()
    old = _old()
    old["document"] = OLD_TEXT
    out = mod._apply_supersede_guard([old], CURRENT_Q)
    assert out[0]["document"].startswith("[已更正：")


def test_guard_disabled_returns_input_unchanged():
    """SHELL_HOOK_SUPERSEDED_GUARD=false：原样返回（旧行为）。"""
    mod = _load_hook()
    items = [_new(), _old()]
    with patch.object(mod.settings, "SHELL_HOOK_SUPERSEDED_GUARD", False):
        assert mod._apply_supersede_guard(items, CURRENT_Q) is items


@pytest.mark.parametrize(
    "query",
    [
        "我之前在哪上班",
        "当时住哪来着",
        "我去年住哪",
        "我住址有过哪些变化",
        "where did I live before",
    ],
)
def test_retrospective_words(query):
    mod = _load_hook()
    assert mod._RETROSPECTIVE_RE.search(query)


@pytest.mark.parametrize("query", [CURRENT_Q, "我住哪", "帮我规划一下周末通勤"])
def test_current_queries_not_retrospective(query):
    mod = _load_hook()
    assert not mod._RETROSPECTIVE_RE.search(query)


# ---------------------------------------------------------------- 真更正链 + 真 build_context


@pytest.fixture
def corrected_engine():
    """存「住武汉」「搬到荆州」两条，再用真的 apply_supersede_correction 更正。"""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from lantai.cognition.late_correction import apply_supersede_correction
    from lantai.models.tables import MemoryItem

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="mem_old",
                memory_type="semantic",
                key="住址",
                content=OLD_TEXT,
                event_time=now - timedelta(days=200),
            )
        )
        s.add(
            MemoryItem(
                id="mem_new",
                memory_type="semantic",
                key="住址",
                content=NEW_TEXT,
                event_time=now - timedelta(days=10),
            )
        )
        s.add(
            MemoryItem(
                id="mem_tea",
                memory_type="semantic",
                key="饮品",
                content="用户通勤路上喜欢喝乌龙茶",
                event_time=now - timedelta(days=5),
            )
        )
        s.commit()
    with Session(engine) as s:
        old = s.get(MemoryItem, "mem_old")
        new = s.get(MemoryItem, "mem_new")
        res = apply_supersede_correction(old, new, session=s, reason="搬家")
        s.commit()
    assert res["ok"]
    with Session(engine) as s:
        assert s.get(MemoryItem, "mem_old").lifecycle_status == "superseded"
        assert s.get(MemoryItem, "mem_old").status == "active"  # 更正不删历史
    return engine


def _fake_store(ids):
    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": i, "distance": 0.1 + 0.02 * n} for n, i in enumerate(ids)]

    return _FakeStore()


def _run(engine, query, ids, guard=True):
    from sqlmodel import Session

    import lantai.storage.db as db_module

    mod = _load_hook()
    with (
        patch.object(db_module, "get_session", lambda: Session(engine)),
        patch("lantai.retrieval.hybrid.get_vector_store", lambda: _fake_store(ids)),
        patch.object(mod.settings, "SHELL_HOOK_SUPERSEDED_GUARD", guard),
    ):
        return mod.build_context(query)


def _memory_lines(ctx: str, needle: str) -> list[str]:
    return [ln for ln in ctx.splitlines() if needle in ln]


def test_build_context_current_query_injects_only_new_value(corrected_engine):
    """周报复现的场景：当前态问句的注入文本里不再有「武汉」，只有「荆州」。"""
    out = _run(corrected_engine, CURRENT_Q, ["mem_old", "mem_new", "mem_tea"])
    ctx = out.get("context", "")
    assert "荆州" in ctx, ctx[-800:]
    assert "武汉" not in ctx, ctx[-800:]
    assert "乌龙茶" in ctx  # 无关记忆不受影响
    ids = [e["id"] for e in out.get("evidence", [])]
    assert "mem_old" not in ids and "mem_new" in ids


def test_build_context_retrospective_query_keeps_marked_old(corrected_engine):
    """「我以前住在哪」：武汉留下且带「已更正」标记，荆州也在。"""
    out = _run(corrected_engine, RETRO_Q, ["mem_old", "mem_new"])
    ctx = out.get("context", "")
    assert "荆州" in ctx
    wuhan = _memory_lines(ctx, "武汉")
    assert wuhan, ctx[-800:]
    assert all("已更正" in ln for ln in wuhan), wuhan
    ev = {e["id"]: e["content"] for e in out.get("evidence", [])}
    assert ev["mem_old"].startswith("[已更正：")  # 回执/审计看到的与注入一致


def test_build_context_successor_not_retrieved_keeps_marked_old(corrected_engine):
    """只召回到旧值：不丢（丢了模型两条都看不到），但带标记。"""
    out = _run(corrected_engine, CURRENT_Q, ["mem_old"])
    wuhan = _memory_lines(out.get("context", ""), "武汉")
    assert wuhan and all("已更正" in ln for ln in wuhan), wuhan


def test_build_context_guard_off_is_old_behavior(corrected_engine):
    """开关关闭：新旧并列、旧值无标记（即修复前的注入帧）。"""
    out = _run(corrected_engine, CURRENT_Q, ["mem_old", "mem_new"], guard=False)
    ctx = out.get("context", "")
    assert "荆州" in ctx and "武汉" in ctx
    assert "已更正" not in ctx
