"""MCP 工具循环守卫（票据 .scratch/mcp-loop-guard/issues/01）。

不 mock 冒烟：真 handle() 直调 + 真 handler 抛异常 + 真 LoopGuard 状态机。
上游 f0.3++ LoopGuard 教训镜像：宿主 LLM 陷「调工具→失败→再调」死循环，
每轮烧 token；熔断给宿主明确的 retry_after 信号。
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

MCP_PATH = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(mod, name, args):
    return mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )


def _boom_handler(msg):
    def handler(params):
        raise RuntimeError(msg)

    return handler


def _ok_handler():
    def handler(params):
        return {"ok": True}

    return handler


# ---------------------------------------------------------------- 熔断与恢复


def test_five_failures_then_sixth_rejected():
    """5 次失败/窗口内 → 第 6 次被拒：error 帧 + retry_after，handler 不再执行。"""
    mod = _load_mcp()
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise RuntimeError("boom")

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"boom": handler}),
    ):
        for i in range(5):
            resp = _call(mod, "boom", {})
            assert resp["error"]["code"] == -32603, f"第{i + 1}次应正常隔离异常"
        assert calls["n"] == 5

        rejected = _call(mod, "boom", {})
        assert rejected["error"]["code"] == -32005, f"第 6 次应被熔断: {rejected}"
        assert "retry_after" in rejected["error"]["message"]
        assert calls["n"] == 5, "熔断后 handler 不应再执行"


def test_success_resets_failure_count():
    """一次成功清零全部失败计数——Success calls are unlimited（上游原义）。"""
    mod = _load_mcp()
    state = {"fail": True}
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return {"ok": True}

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "flip": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"flip": handler}),
    ):
        for _ in range(4):
            _call(mod, "flip", {})  # 4 次失败（不足阈值）
        state["fail"] = False
        resp = _call(mod, "flip", {})  # 成功 → 清零
        assert "result" in resp

        state["fail"] = True
        for i in range(5):
            resp = _call(mod, "flip", {})
            assert resp["error"]["code"] == -32603, f"清零后第{i + 1}次失败不应熔断"
        # 前 5 次失败已使计数达标（达标的那次调用本身放行），下一次 begin 应拒
        resp = _call(mod, "flip", {})
        assert resp["error"]["code"] == -32005


def test_cooldown_expiry_single_probe():
    """冷却结束放单探针：探针失败续断；探针成功恢复。"""
    mod = _load_mcp()
    clock = {"t": 1000.0}
    guard = mod.LoopGuard(clock=lambda: clock["t"])
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise RuntimeError("boom")

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"boom": handler}),
        patch.object(mod, "_get_guard", lambda: guard),
    ):
        for _ in range(5):
            _call(mod, "boom", {})
        resp = _call(mod, "boom", {})
        assert resp["error"]["code"] == -32005

        clock["t"] += 31  # 冷却 30s 过去
        resp = _call(mod, "boom", {})  # 单探针放行 → handler 执行 → 失败 → 续断
        assert resp["error"]["code"] == -32603, "探针应放行并照常报错"
        assert calls["n"] == 6
        resp = _call(mod, "boom", {})  # 探针失败 → 重新熔断
        assert resp["error"]["code"] == -32005
        assert calls["n"] == 6

        clock["t"] += 31

        def ok_handler(params):
            calls["n"] += 1
            return {"ok": True}

        # 换成功 handler 模拟服务恢复
        with patch.object(mod, "TOOL_HANDLERS", {"boom": ok_handler}):
            resp = _call(mod, "boom", {})  # 第二轮探针 → 成功 → 恢复
            assert "result" in resp, f"探针成功应恢复: {resp}"
            resp = _call(mod, "boom", {})  # 恢复后正常调用
            assert "result" in resp


# ---------------------------------------------------------------- 指纹隔离


def test_different_args_count_separately():
    """同工具不同参数 = 不同指纹，各自计数。"""
    mod = _load_mcp()
    calls = {"a": 0, "b": 0}

    def handler(params):
        calls["a" if params.get("which") == "a" else "b"] += 1
        raise RuntimeError("boom")

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"boom": handler}),
    ):
        for _ in range(5):
            _call(mod, "boom", {"which": "a"})
        assert _call(mod, "boom", {"which": "b"})["error"]["code"] == -32603, (
            "b 参数不应被 a 的熔断连坐"
        )


def test_volatile_keys_do_not_split_fingerprint():
    """request_id/trace_id 等易变顶层键不新开指纹（同一业务操作重试照常熔断）。"""
    mod = _load_mcp()
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise RuntimeError("boom")

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"boom": handler}),
    ):
        for i in range(5):
            _call(mod, "boom", {"which": "a", "request_id": f"r{i}"})
        resp = _call(mod, "boom", {"which": "a", "request_id": "r99"})
        assert resp["error"]["code"] == -32005, "换 request_id 不应逃过熔断"


# ---------------------------------------------------------------- 配置与健壮性


def test_guard_disabled_passes_through():
    """ENABLED=false：零拦截，逐次调用 handler。"""
    mod = _load_mcp()
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise RuntimeError("boom")

    from lantai.core.settings import settings as real_settings

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "boom": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"boom": handler}),
        patch.object(real_settings, "MCP_LOOP_GUARD_ENABLED", False),
    ):
        for _ in range(10):
            resp = _call(mod, "boom", {})
            assert resp["error"]["code"] == -32603
        assert calls["n"] == 10


def test_state_capacity_eviction_does_not_crash():
    """指纹数超容量：最旧状态被淘汰，不崩不挂。"""
    mod = _load_mcp()
    guard = mod.LoopGuard(capacity=8)
    for i in range(50):
        tok, rej = guard.begin(f"key_{i}")
        guard.finish(tok, failed=True)
    assert len(guard._states) <= 8, f"容量未收敛: {len(guard._states)}"


def test_window_expires_old_failures():
    """60s 窗口外的失败不累计（滑动窗口）。"""
    mod = _load_mcp()
    clock = {"t": 1000.0}
    guard = mod.LoopGuard(clock=lambda: clock["t"])
    for i in range(4):
        tok, rej = guard.begin("k")
        clock["t"] += 20  # 每次间隔 20s：第 1 次失败在第 4 次时已出窗
        guard.finish(tok, failed=True)
    tok, rej = guard.begin("k")
    assert rej is None, "窗口外失败不应累计到阈值"
    guard.finish(tok, failed=False)


def test_validation_error_also_counts():
    """ValueError（-32602 业务校验失败）同样计入失败——宿主参数写错也是撞墙。"""
    mod = _load_mcp()
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise ValueError("bad input")

    with (
        patch.object(mod, "TOOLS", {**mod.TOOLS, "bad": {}}),
        patch.object(mod, "TOOL_HANDLERS", {"bad": handler}),
    ):
        for _ in range(5):
            resp = _call(mod, "bad", {})
            assert resp["error"]["code"] == -32602
        resp = _call(mod, "bad", {})
        assert resp["error"]["code"] == -32005, f"校验失败也应熔断: {resp}"


def test_concurrent_calls_bounded_and_isolated():
    """并发 finish 不互相踩：状态边界保持 threshold 上限，无异常冒泡。"""
    import threading

    mod = _load_mcp()
    guard = mod.LoopGuard(threshold=3, capacity=16)
    tokens = [guard.begin(f"k{i}")[0] for i in range(10)]
    errs = []

    def finish_all():
        try:
            for t in tokens:
                if t:
                    guard.finish(t, failed=True)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=finish_all) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs, f"并发 finish 抛异常: {errs}"
