"""票 44：探颐回执分类器二次回溯（ReDoS）——`_HAISHI_KEEP_OLD` 负向前瞻惰性扫全段。

`.scratch/v0.68-next-window-2026-11/issues/44-probe-reply-regex-backtracking.md`

**先说影响**：`_HAISHI_KEEP_OLD = 还是(?![^标点\\s]*?(?:改成|…))` 的否定前瞻
内含**无界惰性** `[^标点\\s]*?`——病理输入（「还是」长流、无标点空白、改动
词压末尾）让每个「还是」起点惰性扫完整段剩余再失败重扫 → O(n²)。探针实测
n 翻倍耗时 ×4.0（40K→7.4s），外推 500KB ≈19 分钟单线程 CPU；REST
`user_reply` 无 `max_length`、MCP `conflict_resolve` 裸取、starlette 无 body
上限——单请求卡死 threadpool 一线程，数个并发即全 API 停摆。

**修法**：前瞻量词有界化（`{0,16}` 窗——「还是」与改动词的 realistic 间距
远小于 16 字，每起点 O(16) → 全局线性）；入口收口 REST `max_length=2000`
（pydantic → 422）+ MCP 显式钳制（ValueError → -32602），不静默截断
（宁 miss 不脏写）；耗时哨兵锁防同族形状回潮。

**测试边界声明**：真 `_matches_any` 直调零 mock（真编译真 search 真计时）；
语义边界臂锁定票 02 正反对照（防去二次化改坏采纳/留旧判定）；入口臂真
TestClient/真 MCP handler（param_env 防真实库写入）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.mcp_full_surface()

# 病理形状：还是 ×50K（100K 字符）+ 改动词压末尾——每个起点前瞻扫到末尾才失败
PATHOLOGICAL = "还是" * 50_000 + "改成"


class TestHaishiSemanticsLocked:
    """语义边界现状锁（红绿两态都必须绿）：票 02 采纳/留旧判定不得被去二次化改坏。"""

    def test_keep_old_still_matches(self):
        from lantai.services.probing_service import _NEGATIVE_PATTERNS, _matches_any

        assert _matches_any(_NEGATIVE_PATTERNS, "还是原来的写法") is True
        assert _matches_any(_NEGATIVE_PATTERNS, "还是用旧密码") is True

    def test_adoption_still_blocked(self):
        from lantai.services.probing_service import _NEGATIVE_PATTERNS, _matches_any

        assert _matches_any(_NEGATIVE_PATTERNS, "还是改成乌龙茶吧") is False
        assert _matches_any(_NEGATIVE_PATTERNS, "还是换成咖啡") is False

    def test_unchanged_face_semantics_locked(self):
        # 分支 B/C 问「这条还对不对」：同形词是**确认**——_HAISHI_KEEP_OLD 在
        # _UNCHANGED_PATTERNS 肯定侧（票面「:378 属 _CLAIM_NEGATIVE_PATTERNS」
        # 为笔误，实测该常量活消费面是 _NEGATIVE_PATTERNS 否定侧 + 本表肯定侧）
        from lantai.services.probing_service import _UNCHANGED_PATTERNS, _matches_any

        assert _matches_any(_UNCHANGED_PATTERNS, "还是喝乌龙茶") is True
        assert _matches_any(_UNCHANGED_PATTERNS, "还是改成咖啡") is False


class TestPathologicalTiming:
    """TDD 1：耗时哨兵——200KB 级病理输入分类必须亚秒（修复前 O(n²) 秒级起步）。"""

    def test_matches_any_bounded_on_pathological_input(self):
        from lantai.services.probing_service import _NEGATIVE_PATTERNS, _matches_any

        start = time.perf_counter()
        _matches_any(_NEGATIVE_PATTERNS, PATHOLOGICAL)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"二次回溯回潮：100K 字符病理输入耗时 {elapsed:.2f}s（ O(n²) 特征）"


class TestRestEntryBound:
    """TDD 2：REST 入口——超长 user_reply 422（pydantic max_length），不进分类器。"""

    def test_resolve_rejects_overlong_reply(self, param_env):
        from lantai.api.app import app

        client = TestClient(app)
        r = client.post(
            "/probing/resolve",
            json={"conflict_id": "conf_nonexistent_44", "user_reply": "还" * 60_000},
            headers={"X-API-Key": "test"},
        )
        assert r.status_code == 422, f"超长 user_reply 修复前放行进分类器：{r.status_code}"


class TestMcpEntryBound:
    """TDD 2 续：MCP conflict_resolve 同步钳制——超长 ValueError（→ -32602）。"""

    def test_conflict_resolve_rejects_overlong_reply(self, param_env):
        from lantai.cli.mcp import handle_probe_resolve

        with pytest.raises(ValueError):
            handle_probe_resolve(
                {"conflict_id": "conf_nonexistent_44", "user_reply": "还" * 60_000}
            )
