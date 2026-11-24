"""边界锁：MCP 面「先验后用」——批量写周期的 limit 不得越界。

`handle_kaogong_eval` 与 `handle_zhiming_lifecycle` 此前都是裸
`int(params.get("limit", 100))`：MCP 分发层**不做 inputSchema 校验**，负值
原样进 `q.limit(limit)`，而 SQLite 的 `LIMIT -1` 是无上限——一次调用就把
全库记忆过一遍并写回 tier/importance（生命周期审计还会翻状态）。

本文件锁住：越界值在入口即被拒（ValueError → 分发层 -32602），合法值照常
传下去（反向保险，防「干脆全拒」的假修）。不 mock 产品逻辑。
"""

import pytest

from lantai.cli import mcp as mcp_mod


@pytest.mark.parametrize("bad", [-1, 0, 1001, True])
def test_kaogong_eval_rejects_bad_limit(bad):
    with pytest.raises(ValueError, match="limit"):
        mcp_mod.handle_kaogong_eval({"limit": bad})


@pytest.mark.parametrize("bad", [-1, 0, 1001])
def test_zhiming_lifecycle_rejects_bad_limit(bad):
    with pytest.raises(ValueError, match="limit"):
        mcp_mod.handle_zhiming_lifecycle({"action": "audit", "limit": bad})


def test_valid_limit_reaches_service(monkeypatch):
    """反向保险：合法值必须真的传到 service 层（不是入口全拒的假修）。"""
    seen: dict = {}

    def fake_cycle(*, principal=None, limit=100):
        seen["limit"] = limit
        return {"ok": True}

    import lantai.services.kaogong_service as kaogong_service

    monkeypatch.setattr(kaogong_service, "run_kaogong_cycle", fake_cycle)
    mcp_mod.handle_kaogong_eval({"limit": 25})
    assert seen["limit"] == 25


def test_default_limit_preserved():
    """缺省仍是 100（不因加边界而改默认值）。"""
    seen: dict = {}

    def fake_cycle(*, principal=None, limit=100):
        seen["limit"] = limit
        return {"ok": True}

    import lantai.services.kaogong_service as kaogong_service

    mcp_mod_kaogong = kaogong_service
    original = mcp_mod_kaogong.run_kaogong_cycle
    mcp_mod_kaogong.run_kaogong_cycle = fake_cycle
    try:
        mcp_mod.handle_kaogong_eval({})
    finally:
        mcp_mod_kaogong.run_kaogong_cycle = original
    assert seen["limit"] == 100
