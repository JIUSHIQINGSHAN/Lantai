"""REST `/search` 更正闸（v0.68 票 13）：routes_search 不再把被取代旧说当当前事实返回。

病灶（票 13）：票 02 更正闸消费方普查的第三缺口——shell_hook 注入面（PR #30）、
MCP `handle_search`（票 02 mcp 半步）、context.py SQL 过滤（票 02 context 半步）
都已挂闸，REST `/search` 全程无闸，新旧两条裸正文并列返回（探针
`.scratch/_v068_probe_ticket13_rest_supersede.py` 实锤）。本测试镜像
`tests/test_mcp_search_supersede_guard.py`（corrected_env 双面镜像先例），
直调路由函数 `routes_search.search(...)`，真库 + 真更正链，不 mock 被测链路。

红→绿语义：主臂在修复前必须红在断点形态（旧值在 results 且无标记）；
as_of 臂与开关关闭臂是现状锁（现在就绿，修复后必须保持绿——钉「时效视图
不收闸」「配置开关沿用」两条既有契约）。全局状态仅 `SHELL_HOOK_SUPERSEDED_GUARD`
走 monkeypatch；param_env 自管 db.get_session/向量桩。
"""

import pytest

from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.schemas import SearchReq
from lantai.models.tables import MemoryItem

OLD_TEXT = "用户的地址是武汉，在光谷上班"
NEW_TEXT = "用户的地址已改为荆州，现在住在荆州"

Q_CURRENT = "用户的地址是哪里"
Q_RETRO = "我以前的地址是什么？"
Q_OLD_ONLY = "武汉光谷上班的地方是哪？"


def _ids(ret) -> list[str]:
    return [r.get("memory", {}).get("id") for r in ret.get("results", [])]


def _memory_of(ret, mid) -> dict:
    for r in ret.get("results", []):
        m = r.get("memory") or {}
        if m.get("id") == mid:
            return m
    raise AssertionError(f"{mid} 不在 results 里（前提塌了，先修召回再谈语义）：{_ids(ret)}")


@pytest.fixture()
def corrected_env(param_env):
    """真库种子（FTS 同步）+ 真实更正链；落库态钉住防种子写错让断言空转。"""
    from datetime import timedelta

    from lantai.cognition.late_correction import apply_supersede_correction
    from lantai.storage.fts import sync_fts

    session_factory, _engine = param_env
    now = utcnow()
    with session_factory() as s:
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
        s.commit()
        for mid in ("mem_old", "mem_new"):
            row = s.get(MemoryItem, mid)
            sync_fts(s, row.id, row.content)
        s.commit()
        res = apply_supersede_correction(
            s.get(MemoryItem, "mem_old"), s.get(MemoryItem, "mem_new"), session=s, reason="搬家"
        )
        s.commit()
    assert res.get("ok") is True, res
    with session_factory() as s:
        old = s.get(MemoryItem, "mem_old")
        assert old.status == "active" and old.lifecycle_status == "superseded"
        assert old.superseded_by == "mem_new"
    return param_env


def _search(query: str, **req_kwargs):
    from lantai.api.routes_search import search as rest_search

    return rest_search(
        SearchReq(query=query, **req_kwargs),
        trace=False,
        explain=False,
        ctx=Principal(user_id="default"),
    )


class TestRestSearchSupersedeGuard:
    def test_current_query_drops_old_when_successor_present(self, corrected_env):
        """主红点：当前态问句 + 新值在场 → 旧值不进 results（前提断言在前防假绿）。"""
        ret = _search(Q_CURRENT)
        ids = _ids(ret)
        assert "mem_new" in ids, f"前提塌了：新值未被召回，丢弃语义无从谈起：{ids}"
        # 红点（修复前 mem_old 与新值并列返回，探针实锤）
        assert "mem_old" not in ids, f"被取代旧值仍随 REST /search 返回：{ids}"

    def test_retrospective_query_keeps_old_with_marker(self, corrected_env):
        """回看问句：旧值保留带「已更正」标记；新值不受影响。"""
        ret = _search(Q_RETRO)
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"前提塌了：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        assert "武汉" in (old_mem.get("content") or ""), old_mem
        # 红点（修复前旧值裸正文，无任何标记）
        assert "已更正" in (old_mem.get("content") or ""), old_mem
        new_mem = _memory_of(ret, "mem_new")
        assert "已更正" not in (new_mem.get("content") or ""), new_mem

    def test_successor_absent_keeps_old_with_marker(self, corrected_env):
        """新值没被召回：旧值不丢但必须带标记（前提断言双钉防退化为丢弃臂）。"""
        ret = _search(Q_OLD_ONLY)
        ids = _ids(ret)
        assert "mem_old" in ids, f"前提塌了：旧值未被召回：{ids}"
        assert "mem_new" not in ids, f"前提塌了：新值不应被本问句召回：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        # 红点（修复前裸正文无标记）
        assert "已更正" in (old_mem.get("content") or ""), old_mem

    def test_as_of_view_unchanged(self, corrected_env):
        """现状锁：带 as_of 的时效视图不收更正闸（历史查询面，与 MCP 面口径一致）。"""
        now_iso = utcnow().isoformat()
        ret = _search(Q_CURRENT, as_of=now_iso)
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"前提塌了：时效视图召回缺失：{ids}"
        for r in ret.get("results", []):
            assert "已更正" not in (r.get("memory", {}).get("content") or ""), r

    def test_guard_disabled_returns_verbatim(self, corrected_env, monkeypatch):
        """现状锁 + 开关契约：SHELL_HOOK_SUPERSEDED_GUARD=false 时全量原样返回。"""
        from lantai.core.settings import settings

        monkeypatch.setattr(settings, "SHELL_HOOK_SUPERSEDED_GUARD", False)
        ret = _search(Q_CURRENT)
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"开关关闭仍动了结果：{ids}"
        for r in ret.get("results", []):
            assert "已更正" not in (r.get("memory", {}).get("content") or ""), r

    def test_real_db_results_carry_lifecycle_fields(self, corrected_env):
        """假绿硬防线：真库种子的 results[i]["memory"] 必须真带闸依赖的字段。"""
        ret = _search(Q_RETRO)
        ids = _ids(ret)
        assert "mem_old" in ids, f"前提塌了：旧值未被召回：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        assert old_mem.get("lifecycle_status") == "superseded", old_mem.get("lifecycle_status")
        assert old_mem.get("superseded_by") == "mem_new", old_mem.get("superseded_by")
        assert old_mem.get("valid_to"), old_mem.get("valid_to")


class TestGraphExpandSupersedeGuard:
    """病灶 5（票面判据：实测复现→顺手覆盖）。

    立案探针曾判「graph_expand 零展开未复现」——复检发现探针读了不存在的
    `results` 键（graph_augmented_search 返回 primary_results/associated_memories）；
    本轮探针 `.scratch/_t13_probe_graph_expand_supersede.py` 实测
    `primary ids: ['mem_new', 'mem_old']`，primary_results 同形漏旧值，实锤。
    """

    def test_graph_expand_primary_drops_old_when_successor_present(self, corrected_env):
        from lantai.api.routes_search import search_graph_expand

        ret = search_graph_expand(
            type(
                "G",
                (),
                {
                    "query": Q_CURRENT,
                    "top_k": 5,
                    "max_hops": 2,
                    "min_edge_conf": 0.5,
                    "domain": None,
                },
            )(),
            ctx=Principal(user_id="default"),
        )
        prim_ids = [r.get("memory", {}).get("id") for r in ret.get("primary_results", [])]
        assert "mem_new" in prim_ids, f"前提塌了：新值未被召回：{prim_ids}"
        # 红点（修复前 primary_results 新旧并列裸返）
        assert "mem_old" not in prim_ids, f"graph_expand primary_results 仍漏被取代旧值：{prim_ids}"
