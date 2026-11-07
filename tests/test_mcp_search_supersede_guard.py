"""MCP search 更正闸（v0.68 票 02）：handle_search 不再把被取代旧说当当前事实返回。

病灶（`.scratch/v0.68-next-window-2026-11/issues/02-supersede-guard-context-mcp.md`）：
检索层 `_apply_supersedes_order` 只把旧值压到新值之下（不丢不标）；shell_hook 注入面
在其上加了 `_apply_supersede_guard`（丢/标记），MCP `handle_search` 直返 hybrid 结果
没有等价处理——宿主拿到的新旧两条都是裸正文，模型无从分辨（P0-2 答旧值病灶）。
`as_of` 时效视图是历史查询面（周报 P0-2 验收口径），保持现状不加闸。

红→绿语义：丢弃臂/标记臂在修复前必须红在断点形态（旧值在 results 且无标记）；
as_of 臂与开关关闭臂是现状锁（现在就绿，修复后必须保持绿——它们钉的是
「时效视图不收闸」「配置开关沿用」两条既有契约，前提断言在前防空转假绿）。

不 mock 被测链路：真内存库（param_env）+ 真 `apply_supersede_correction` +
真 `handle_search`（真实 relevance gate 自指/疑问词自然过闸，不 mock、不 force）；
种子文案新旧两行共享短语「用户的地址」，BM25 trigram OR 通道可稳定召回
（探针 `.scratch/_v068_probe_ticket02_supersede.py` 实测各臂召回集）。
全局状态：仅 `SHELL_HOOK_SUPERSEDED_GUARD` 走 monkeypatch（自动恢复）；
param_env 自管 `db.get_session`/向量桩，无其他绑定 patch。
"""

import pytest

from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

OLD_TEXT = "用户的地址是武汉，在光谷上班"
NEW_TEXT = "用户的地址已改为荆州，现在住在荆州"

Q_CURRENT = "用户的地址是哪里"  # 当前态：自然过 gate（self_reference「用户」）
Q_RETRO = "我以前的地址是什么？"  # 回看问句：旧值保留 + 标记
Q_OLD_ONLY = "武汉光谷上班的地方是哪？"  # 只含旧值词：新值不在场（探针实测只召回 mem_old）


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


class TestMCPSearchSupersedeGuard:
    def test_current_query_drops_old_when_successor_present(self, corrected_env):
        """主红点：当前态问句 + 新值在场 → 旧值不进 results。

        前提断言（新值必须在场）在前：丢弃语义依赖「取代它的新记忆也在本次
        结果里」，前提塌了断言就空转——假绿防护。
        """
        from lantai.cli.mcp import handle_search

        ret = handle_search({"query": Q_CURRENT})
        ids = _ids(ret)
        assert "mem_new" in ids, f"前提塌了：新值未被召回，丢弃语义无从谈起：{ids}"
        # 红点（修复前 mem_old 与新值并列返回，探针实锤）
        assert "mem_old" not in ids, f"被取代旧值仍随 MCP search 返回：{ids}"

    def test_retrospective_query_keeps_old_with_marker(self, corrected_env):
        """回看问句：旧值保留，memory.content 带「已更正」标记；新值不受影响。

        content 经 wrap_as_data 樊篱包裹，标记在围栏内（数据注记），用子串断言。
        """
        from lantai.cli.mcp import handle_search

        ret = handle_search({"query": Q_RETRO})
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"前提塌了：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        assert "武汉" in (old_mem.get("content") or ""), old_mem
        # 红点（修复前旧值裸正文，无任何标记）
        assert "已更正" in (old_mem.get("content") or ""), old_mem
        new_mem = _memory_of(ret, "mem_new")
        assert "已更正" not in (new_mem.get("content") or ""), new_mem

    def test_successor_absent_keeps_old_with_marker(self, corrected_env):
        """新值没被召回：旧值不丢（丢了模型两条都看不到），但必须带标记。

        前提断言双钉：旧值在场**且**新值不在场——否则本臂退化成丢弃臂的
        重复（新值在场时旧行为是被丢），标记语义无从验证。
        """
        from lantai.cli.mcp import handle_search

        ret = handle_search({"query": Q_OLD_ONLY})
        ids = _ids(ret)
        assert "mem_old" in ids, f"前提塌了：旧值未被召回：{ids}"
        assert "mem_new" not in ids, f"前提塌了：新值不应被本问句召回：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        # 红点（修复前裸正文无标记）
        assert "已更正" in (old_mem.get("content") or ""), old_mem

    def test_as_of_view_unchanged(self, corrected_env):
        """现状锁：带 as_of 的时效视图不收更正闸（历史查询面，周报 P0-2 验收口径）。

        前提断言（两行都在场）在前——若召回为空，本臂对「旧行为不变」的
        断言就是空转假绿。绿半步实现时 `temporal_kwargs` 在场必须跳过闸。
        """
        from lantai.cli.mcp import handle_search

        now_iso = utcnow().isoformat()
        ret = handle_search({"query": Q_CURRENT, "as_of": now_iso})
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"前提塌了：时效视图召回缺失：{ids}"
        for r in ret.get("results", []):
            assert "已更正" not in (r.get("memory", {}).get("content") or ""), r

    def test_guard_disabled_returns_verbatim(self, corrected_env, monkeypatch):
        """现状锁 + 开关契约：SHELL_HOOK_SUPERSEDED_GUARD=false 时全量原样返回
        （新旧并列、无标记）——配置开关语义从注入面沿用到 MCP 面。"""
        from lantai.cli.mcp import handle_search
        from lantai.core.settings import settings

        monkeypatch.setattr(settings, "SHELL_HOOK_SUPERSEDED_GUARD", False)
        ret = handle_search({"query": Q_CURRENT})
        ids = _ids(ret)
        assert "mem_old" in ids and "mem_new" in ids, f"开关关闭仍动了结果：{ids}"
        for r in ret.get("results", []):
            assert "已更正" not in (r.get("memory", {}).get("content") or ""), r

    def test_real_db_results_carry_lifecycle_fields(self, corrected_env):
        """假绿硬防线：真库种子的 results[i]["memory"] 必须真带闸依赖的三个字段。

        闸能否生效取决于 hybrid 的 `model_dump(mode="json")` 把 lifecycle_status /
        superseded_by / valid_to 全量序列化进结果字典——桩数据里字段永远在，
        真库拿不到闸就空转。用回看问句（Q_RETRO）取旧值：闸生效前后它都保留
        在 results 里（前者无标记、后者带标记），本断言两侧都真。"""
        from lantai.cli.mcp import handle_search

        ret = handle_search({"query": Q_RETRO})
        ids = _ids(ret)
        assert "mem_old" in ids, f"前提塌了：旧值未被召回：{ids}"
        old_mem = _memory_of(ret, "mem_old")
        assert old_mem.get("lifecycle_status") == "superseded", old_mem.get("lifecycle_status")
        assert old_mem.get("superseded_by") == "mem_new", old_mem.get("superseded_by")
        assert old_mem.get("valid_to"), old_mem.get("valid_to")  # 更正锚点回写（ISO 串）
