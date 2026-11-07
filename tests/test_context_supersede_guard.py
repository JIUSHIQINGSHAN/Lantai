"""认知上下文更正闸（v0.68 票 02）：被取代的旧说不进 build() 产物。

病灶（`.scratch/v0.68-next-window-2026-11/issues/02-supersede-guard-context-mcp.md`，
探针 `.scratch/_v068_probe_ticket02_supersede.py` 实锤）：更正闸只挂在 shell_hook
注入面（`scripts/shell_hook.py` `_apply_supersede_guard`），`CognitiveContextBuilder
._owned_memory_stmt` 只过滤 `status == "active"`——而更正设计里旧说**有意保留**
status=active，仅标 `lifecycle_status="superseded"` + `superseded_by`（tables.py:179
注释：superseded「退出检索」）。于是 REST `/cognitive/context`、`/context/prompt`
与 MCP 心斋经 `build()` 把「住武汉」这类已被取代的旧值当当前事实注入 Agent 提示词
（P0-2 论文实证的答旧值病灶）。

红→绿语义：本文件在修复前必须红在断点形态（旧值 id/正文出现在产物切面里），
修复后转绿。不 mock 被测链路：真内存库（param_env）+ 真
`apply_supersede_correction` 更正链 + 真 `build()` 直调；对照臂（active 行不受
影响、admin 分支同口径）防修复把正常面改坏。

全局状态：无新增污染面——param_env 自管 `db.get_session`/向量桩，无其他绑定
patch（票 01 评审教训：不在 param_env 之上二次 patch 受护栏绑定）。
"""

import pytest

from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

OLD_TEXT = "用户的地址是武汉，在光谷上班"
NEW_TEXT = "用户的地址已改为荆州，现在住在荆州"
TEA_TEXT = "用户通勤路上喜欢喝乌龙茶"

_SECTIONS = ("facts", "experience", "beliefs", "rules", "principles")


def _all_records(ctx) -> list[dict]:
    """收集 build() 产物全部记忆切面的条目（failures 形状不同，单独断言）。"""
    out: list[dict] = []
    for name in _SECTIONS:
        out.extend(getattr(ctx, name))
    return out


@pytest.fixture()
def corrected_env(param_env):
    """真库种子 + 真实更正链：mem_old 被 mem_new 取代（status 仍 active）。

    真入口 `apply_supersede_correction` 全程真实执行（supersedes 边、
    lifecycle 翻转、valid_to 锚定、checkpoint、ConflictEvent 同事务落库），
    落库态由首条用例断言钉住——防种子写错让后面的断言空转。
    """
    from datetime import timedelta

    from lantai.cognition.late_correction import apply_supersede_correction

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
        s.add(
            MemoryItem(
                id="mem_tea",
                memory_type="semantic",
                key="饮品",
                content=TEA_TEXT,
                event_time=now - timedelta(days=5),
            )
        )
        s.commit()
        res = apply_supersede_correction(
            s.get(MemoryItem, "mem_old"), s.get(MemoryItem, "mem_new"), session=s, reason="搬家"
        )
        s.commit()
    assert res.get("ok") is True, res
    with session_factory() as s:
        old = s.get(MemoryItem, "mem_old")
        assert old.status == "active", "更正不删历史：旧说必须仍是 active（否则本文件测错了东西）"
        assert old.lifecycle_status == "superseded"
        assert old.superseded_by == "mem_new"
    return param_env


class TestSupersededExcludedFromContext:
    def test_superseded_old_value_absent_from_all_sections(self, corrected_env):
        """主红点：build() 产物各切面不得包含被取代旧值的 id 与正文；新值与无关行照常在。

        build() 无问句语境、无「新值在场」可判——直接不进产物是最小正确语义
        （票面修复目标 2）；历史回看场景不经此入口（MCP search 带 as_of 的
        时效视图才是历史查询面）。
        """
        from lantai.cognition.context import CognitiveContextBuilder

        session_factory, _engine = corrected_env
        with session_factory() as s:
            ctx = CognitiveContextBuilder(s, principal=None).build("用户的地址相关任务安排")

        records = _all_records(ctx)
        ids = [r.get("id") for r in records]
        # 红点（修复前 mem_old 在 facts 里，探针实锤）：旧值 id 与正文都不得出现
        assert "mem_old" not in ids, f"被取代旧值仍进认知上下文切面：{ids}"
        assert all("武汉" not in (r.get("content") or "") for r in records), (
            f"旧值正文仍注入：{[r.get('content') for r in records]}"
        )
        # 对照臂：新值照常在，无关 active 行不受影响（防修复把正常面修瞎）
        assert "mem_new" in ids, f"新值被误伤：{ids}"
        assert "mem_tea" in ids, f"无关 active 行被误伤：{ids}"

    def test_prompt_rendering_also_clean(self, corrected_env):
        """/context/prompt 出口（to_prompt）同样不得出现旧值正文。"""
        from lantai.cognition.context import CognitiveContextBuilder

        session_factory, _engine = corrected_env
        with session_factory() as s:
            ctx = CognitiveContextBuilder(s, principal=None).build("用户的地址相关任务安排")
        prompt = ctx.to_prompt()
        assert "武汉" not in prompt
        assert "荆州" in prompt


class TestAdminBranchUnconditional:
    def test_admin_branch_also_excludes_superseded(self, corrected_env):
        """admin 全权分支同样排除——lifecycle 过滤必须进 `_owned_memory_stmt` 的
        基础 stmt（归属判定之前），与 status 过滤同口径（review-fixes-2026-10/06
        的既有决定：撤回对 admin 也是「停止使用」）。"""
        from lantai.cognition.context import CognitiveContextBuilder
        from lantai.core.acl import Principal

        admin = Principal(
            user_id="admin-1", tenant_id="t1", allowed_lanes=["general", "fact"], role="admin"
        )
        session_factory, _engine = corrected_env
        with session_factory() as s:
            ctx = CognitiveContextBuilder(s, principal=admin).build("用户的地址相关任务安排")
        ids = [r.get("id") for r in _all_records(ctx)]
        assert "mem_old" not in ids, f"admin 分支漏进被取代旧值：{ids}"
        assert "mem_new" in ids


class TestOrphanSupersededRowExcluded:
    def test_superseded_without_pointer_also_excluded(self, param_env):
        """异常行对照臂的翻面回填（票 02 绿半步 005cf702→context 补闸的有意决策）。

        红测阶段本臂钉的是「修复前异常行进产物」的现状（防修复顺手扩大口径）；
        票面修复目标 2 落地为 lifecycle 单条件排除（`!= SUPERSEDED`，无
        superseded_by 门槛、无 or_ 分支）后，自称已被取代的异常行一并退出产物。
        这是排查轮 Answer 预案过的决策，不是静默翻面：context 无标记机制，
        对「自称已被取代」的行宁 miss 不脏写。与注入面 `_apply_supersede_guard`
        的口径分歧是刻意的、不统一——闸能标记，所以只认显式更正链（orphan
        带标记留下）；build() 不能标记，只能整体排除。"""
        from lantai.cognition.context import CognitiveContextBuilder

        session_factory, _engine = param_env
        with session_factory() as s:
            s.add(
                MemoryItem(
                    id="mem_orphan",
                    memory_type="semantic",
                    key="旧档",
                    content="用户的地址是成都",
                    lifecycle_status="superseded",
                    superseded_by=None,
                )
            )
            s.commit()
            ctx = CognitiveContextBuilder(s, principal=None).build("用户的地址相关任务安排")
        ids = [r.get("id") for r in _all_records(ctx)]
        assert "mem_orphan" not in ids, f"自称已被取代的异常行仍进产物：{ids}"
