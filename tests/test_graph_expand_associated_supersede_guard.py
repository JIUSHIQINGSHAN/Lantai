"""贯珠联想面更正闸（巡检 2026-11-07 22:00）：旧值不再沿 supersedes 边回到 associated_memories。

病灶：v0.68 票 13（bc0afb5c）只给 graph_augmented_search 的 primary_results 套了更正闸。
`late_correction.apply_supersede_correction` 会写一条 new → old 的 `supersedes` 边，
旧值 status 仍是 active，而贯珠邻居判定只看 status——新值进了种子、旧值没进
初筛（top_k 小，或问句只命中新值）时，旧值以 hop=1 回到 associated_memories，
正文裸返、不带任何标记。

复用 `tests/test_rest_search_supersede_guard.py` 的 corrected_env（真库 + 真更正链），
直调 REST 路由与 MCP handler，不 mock 被测链路。
"""

from datetime import UTC, datetime

from lantai.core.auth import Principal
from tests import test_rest_search_supersede_guard as _rest

# fixture 复用：同一真库 + 真更正链种子（mem_old 被 mem_new 取代，带 supersedes 边）
corrected_env = _rest.corrected_env
OLD_TEXT = _rest.OLD_TEXT

Q_CURRENT = "用户的地址是哪里"
Q_NEW_ONLY = "现在住在荆州"
Q_RETRO_NEW = "以前的地址改过吗，现在住在荆州"


def _graph(query: str, top_k: int = 5) -> dict:
    from lantai.api.routes_search import search_graph_expand

    req = type(
        "G",
        (),
        {"query": query, "top_k": top_k, "max_hops": 2, "min_edge_conf": 0.5, "domain": None},
    )()
    return search_graph_expand(req, ctx=Principal(user_id="default"))


def _primary_ids(ret) -> list[str]:
    return [r.get("memory", {}).get("id") for r in ret.get("primary_results", [])]


def _assoc(ret, mid):
    return [a for a in ret.get("associated_memories", []) if a.get("memory_id") == mid]


class TestAssociatedSupersedeGuard:
    def test_current_query_top1_old_not_in_associated(self, corrected_env):
        ret = _graph(Q_CURRENT, top_k=1)
        assert _primary_ids(ret) == ["mem_new"], f"前提塌了：{_primary_ids(ret)}"
        # 红点：修复前 mem_old 以 relation=supersedes、hop=1 裸正文回到联想面
        assert _assoc(ret, "mem_old") == [], ret["associated_memories"]

    def test_new_only_query_old_not_in_associated(self, corrected_env):
        ret = _graph(Q_NEW_ONLY, top_k=5)
        assert "mem_new" in _primary_ids(ret), f"前提塌了：{_primary_ids(ret)}"
        assert "mem_old" not in _primary_ids(ret)
        assert _assoc(ret, "mem_old") == [], ret["associated_memories"]

    def test_no_raw_old_text_anywhere_in_response(self, corrected_env):
        ret = _graph(Q_NEW_ONLY, top_k=5)
        for a in ret.get("associated_memories", []):
            assert a.get("content") != OLD_TEXT, a

    def test_retrospective_query_keeps_old_with_marker(self, corrected_env):
        ret = _graph(Q_RETRO_NEW, top_k=1)
        if "mem_new" not in _primary_ids(ret):
            # 回看问句下初筛可能先拿到旧值（primary 闸带标记保留），联想面就轮不到它
            ret = _graph(Q_NEW_ONLY.replace("现在", "以前和现在"), top_k=1)
        assert _primary_ids(ret) == ["mem_new"], f"前提塌了：{_primary_ids(ret)}"
        hits = _assoc(ret, "mem_old")
        assert len(hits) == 1, ret["associated_memories"]
        assert hits[0]["content"].startswith("[已更正："), hits[0]
        assert hits[0]["content"].endswith(OLD_TEXT)

    def test_guard_disabled_returns_verbatim(self, corrected_env, monkeypatch):
        from lantai.core.settings import settings

        monkeypatch.setattr(settings, "SHELL_HOOK_SUPERSEDED_GUARD", False)
        ret = _graph(Q_CURRENT, top_k=1)
        hits = _assoc(ret, "mem_old")
        assert len(hits) == 1 and hits[0]["content"] == OLD_TEXT, ret["associated_memories"]

    def test_mcp_handler_same_contract(self, corrected_env):
        from lantai.cli.mcp import handle_graph_expand_search

        ret = handle_graph_expand_search({"query": Q_CURRENT, "top_k": 1})
        assert _primary_ids(ret) == ["mem_new"], f"前提塌了：{_primary_ids(ret)}"
        assert _assoc(ret, "mem_old") == [], ret["associated_memories"]


class TestGuardHelperUnit:
    """直调 helper 的分支锁（真库行，不 mock 查询）。"""

    def _call(self, associated, primary, query):
        from lantai.retrieval.graph_retriever import _guard_associated_superseded

        return _guard_associated_superseded(associated, primary, query, None)

    def _old_entry(self):
        return {
            "memory_id": "mem_old",
            "hop": 2,
            "via_memory_id": "mem_x",
            "relation": "links",
            "edge_confidence": 0.8,
            "content": OLD_TEXT,
            "lane": "general",
            "domain": "user",
        }

    def test_successor_absent_keeps_old_with_dated_marker(self, corrected_env):
        out = self._call([self._old_entry()], [], Q_CURRENT)
        assert len(out) == 1
        content = out[0]["content"]
        assert content.startswith("[已更正：") and content.endswith(OLD_TEXT), content
        # 带日期（valid_to 已由更正链回写）
        assert "起不再成立" in content, content

    def test_successor_in_associated_drops_old(self, corrected_env):
        new_entry = dict(self._old_entry(), memory_id="mem_new", content="新值")
        out = self._call([self._old_entry(), new_entry], [], Q_CURRENT)
        assert [a["memory_id"] for a in out] == ["mem_new"]

    def test_input_not_mutated(self, corrected_env):
        entry = self._old_entry()
        self._call([entry], [], Q_CURRENT)
        assert entry["content"] == OLD_TEXT

    def test_non_superseded_and_fact_entries_untouched(self, corrected_env):
        fact = {
            "memory_id": "mem_old",
            "hop": 1,
            "via_memory_id": "fact_atomic",
            "relation": "wanbi:住址",
            "edge_confidence": 0.9,
            "content": "用户 住在 武汉",
            "lane": "fact",
            "domain": "entity_network",
        }
        plain = dict(self._old_entry(), memory_id="mem_new", content="新值")
        out = self._call([fact, plain], [], Q_CURRENT)
        assert out == [fact, plain]

    def test_empty_and_unknown_ids_passthrough(self, corrected_env):
        assert self._call([], [], Q_CURRENT) == []
        ghost = dict(self._old_entry(), memory_id="mem_ghost")
        assert self._call([ghost], [], Q_CURRENT) == [ghost]


def test_marker_date_uses_valid_to_day():
    """标记日期口径与 supersede._superseded_marker 一致（valid_to 优先）。"""
    from lantai.retrieval.supersede import _superseded_marker

    meta = {"valid_to": datetime(2026, 10, 26, 4, 0, tzinfo=UTC), "superseded_at": None}
    assert _superseded_marker(meta).startswith("[已更正：2026-10-26")
