r"""票 44（v0.68-next-window-2026-11）：探颐回执分类器正则二次回溯（ReDoS）回归哨兵。

病灶：`_HAISHI_KEEP_OLD` 的负向前瞻内含惰性 `[^标点\\s]*?` 一路扫到改动词——
「还是」长流（无标点/空白，全落负类）+ 改动词压在末尾时，每个「还是」起点的前瞻
都要惰性扫完整段剩余文本、撞上动词后前瞻失败，再从下一起点重扫，O(n²)。
本机实测（HEAD `c25d4cee`）：5K→0.108s，20K→2.083s，40K→15.986s——n 翻倍耗时
×4~×8，与立案探针一致；外推 500KB 体量输入 ≈ 单线程分钟级到十分钟级。

修复面（票 44 修复目标）：负向前瞻**窗口化**——「还是」与改动词的现实间距远小于
窗口，窗口外的改动词不再豁免「留旧」（票面已接受的语义收窄）；入口面 `user_reply`
加 2000 上界（REST 422 / MCP ValueError），**不静默截断**（宁 miss 不脏写口径）。

五臂：
1. 复杂度锁（哨兵）：5 万字符病理输入直调 `_matches_any`，坏态 ~25s，修后毫秒级；
2. 语义回归：「还是改成 X」仍不判留旧、「还是原来的」仍判留旧（防去二次化改坏票 02
   边界守卫语义——采纳新说法绝不能被读成留旧）；
3. 窗口边界：间距 2 的改动词仍豁免（语义保持），间距 40 不再豁免（新语义钉死）；
4. 入口臂：REST 422 / MCP ValueError，恰好上界不误伤（钳制面不漂移的双端同步锁）；
5. 分发臂：真 JSON-RPC `handle()` 全链路，超长回执收 -32602 错误帧（票 59 先例同款）。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.services.probing_service import (
    PROBE_REPLY_MAX_LENGTH,
    _NEGATIVE_PATTERNS,
    _UNCHANGED_PATTERNS,
    _classify_claim_reply,
    _matches_any,
)

# 病理输入：「还是」×25000 + 改动词压尾。5 万字符，本机坏态 ~25s；修后线性 ~毫秒级。
# 阈值 1.0s：绿态余量 >100×，坏态即使快 25 倍的机器也仍 >1s——两端都不落机器速度灰区。
_PATHOLOGICAL = "还是" * 25000 + "改成"


# ── 臂 1：复杂度锁（哨兵）──────────────────────────────────────────
@pytest.mark.parametrize(
    "mount",
    [_NEGATIVE_PATTERNS, _UNCHANGED_PATTERNS],
    ids=["branch_a_negative", "branch_bc_unchanged"],
)
def test_haishi_no_quadratic_backtracking(mount):
    """`_HAISHI_KEEP_OLD` 两处挂载面对 5 万字符病理输入必须线性（<1s）。"""
    t0 = time.perf_counter()
    _matches_any(mount, _PATHOLOGICAL)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, (
        f"二次回溯回归：5 万字符病理输入耗时 {elapsed:.2f}s（>1s），"
        "_HAISHI_KEEP_OLD 负向前瞻疑似回潮为无界惰性扫描（票 44）"
    )


def test_control_without_haishi_flow_is_linear():
    """对照臂：无「还是」的等长流不触发病理形状——排除「_matches_any 整体慢」的解释。"""
    text = "茶" * 50000
    t0 = time.perf_counter()
    assert _matches_any(_NEGATIVE_PATTERNS, text) is False
    assert time.perf_counter() - t0 < 0.5


# ── 臂 2：语义回归（防去二次化改坏票 02 边界守卫）──────────────────
def test_adopt_new_reply_not_keep_old():
    """「还是改成乌龙茶」= 采纳新说法，不得判留旧；分类不得落 negative。"""
    assert _matches_any(_NEGATIVE_PATTERNS, "还是改成乌龙茶") is False
    assert _classify_claim_reply("还是改成乌龙茶") != "negative"


def test_keep_old_reply_still_matches():
    """「还是原来的」= 留旧：分支 A 判否定（废弃冲突），分支 B/C 判确认（不动记忆）。"""
    assert _matches_any(_NEGATIVE_PATTERNS, "还是原来的") is True
    assert _matches_any(_UNCHANGED_PATTERNS, "还是原来的") is True


# ── 臂 3：窗口边界（钉死票 44 已接受的语义收窄）────────────────────
def test_change_verb_within_window_still_exempt():
    """改动词紧邻（间距 2，现实形态）→ 仍豁免「留旧」，语义与修复前一致。"""
    assert _matches_any(_NEGATIVE_PATTERNS, "还是" + "茶" * 2 + "改成") is False


def test_change_verb_beyond_window_no_longer_exempt():
    """改动词在 40 字之外（远超现实间距）→ 窗口化后不再豁免，判留旧。"""
    assert _matches_any(_NEGATIVE_PATTERNS, "还是" + "茶" * 40 + "改成") is True


# ── 臂 4：入口臂（REST 422 / MCP ValueError，双端同一常量同步锁）──────
def test_reply_max_length_is_spec_value():
    """钳制常量必须钉在票面建议值 2000：单一真源可改，spec 值改动必须过人眼。"""
    assert PROBE_REPLY_MAX_LENGTH == 2000


def test_rest_probe_resolve_rejects_oversize_reply(param_env):
    """REST user_reply 超上界 → 422（坏态：无 max_length，长答复直通服务层）。"""
    client = TestClient(app)
    resp = client.post(
        "/probing/resolve",
        json={"conflict_id": "c1", "user_reply": "茶" * (PROBE_REPLY_MAX_LENGTH + 1)},
    )
    assert resp.status_code == 422


def test_rest_probe_resolve_allows_reply_at_bound(param_env):
    """恰好上界的合法长度不得误伤（坏态亦 200——此臂锁「不因修复收窄而拒合法输入」）。"""
    client = TestClient(app)
    resp = client.post(
        "/probing/resolve", json={"conflict_id": "c1", "user_reply": "茶" * PROBE_REPLY_MAX_LENGTH}
    )
    assert resp.status_code == 200


def test_mcp_probe_resolve_rejects_oversize_reply(param_env):
    """MCP conflict_resolve 超上界 → ValueError（分发器既定映射 -32602 invalid params）。"""
    from lantai.cli.mcp import handle_probe_resolve

    with pytest.raises(ValueError):
        handle_probe_resolve(
            {"conflict_id": "c1", "user_reply": "茶" * (PROBE_REPLY_MAX_LENGTH + 1)}
        )


def test_mcp_probe_resolve_allows_reply_at_bound(param_env):
    """MCP 恰好上界 → 越过钳制面，到达 conflict_id 校验（返回参数缺失错误字典）。"""
    from lantai.cli.mcp import handle_probe_resolve

    res = handle_probe_resolve({"conflict_id": "", "user_reply": "茶" * PROBE_REPLY_MAX_LENGTH})
    assert res["status"] == "error"


# ── 臂 5：分发臂（真 JSON-RPC 全链路，锁 ValueError→-32602 映射）─────
def test_mcp_dispatch_oversize_reply_maps_to_invalid_params(param_env):
    """票 59 先例同款：超长回执必须收 -32602 错误帧，而非 -32603 internal error 或成功帧。"""
    from lantai.cli.mcp import handle

    resp = handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "probe_resolve",
                "arguments": {
                    "conflict_id": "c1",
                    "user_reply": "茶" * (PROBE_REPLY_MAX_LENGTH + 1),
                },
            },
        }
    )
    assert resp["error"]["code"] == -32602
