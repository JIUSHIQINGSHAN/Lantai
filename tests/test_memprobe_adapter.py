"""MemProbe adapter 纯函数冒烟测试（票 03，不 mock：真实构造输入直调）。

覆盖映射主路径：session→JSONL 行（前缀/标签/时间戳/空内容跳过）、payload
序列化（U+2028 行界防拆——票 01 同源教训）、/search 响应→reader 上下文
（gate 拦截与正常路径）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from memprobe_lantai_adapter import build_session_lines, format_retrieval, payload_of

U2028 = chr(0x2028)  # LINE SEPARATOR：服务端 splitlines 会当行界（票 01 教训）


def _session():
    return {
        "session_idx": 2,
        "session_type": "perturbation",
        "dialogue": [
            {"role": "user", "content": "I switched my gym membership to CrossFit Central."},
            {"role": "assistant", "content": "CrossFit Central is a solid choice."},
            {"role": "tool_output", "content": "$ kubectl get deploy\nNAME  READY"},
            {"role": "user", "content": "   "},
        ],
    }


def test_build_session_lines_prefixes_and_tags():
    lines = build_session_lines("EP-X", _session(), n_sessions=21)
    assert len(lines) == 3  # 空白 user 行跳过
    assert lines[0]["content"].startswith("[Session 3 of 21 | perturbation] User: I switched")
    assert lines[1]["content"].startswith("[Session 3 of 21 | perturbation] Assistant: CrossFit")
    assert lines[2]["content"].startswith("[Session 3 of 21 | perturbation] [tool] $ kubectl")
    for l in lines:
        assert l["tags"][:4] == ["MP", "EP-X", "sess2", "perturbation"]
        assert l["tags"][4] in ("user", "assistant", "tool_output")
        assert l["lane"] == "general"


def test_build_session_lines_timestamps_monotonic():
    lines = build_session_lines("EP-X", _session(), n_sessions=21)
    ts = [l["created_at"] for l in lines]
    assert ts == sorted(ts) and len(set(ts)) == 3
    assert ts[0] == "2026-01-01T02:00:00Z"  # session_idx=2 → +2h


def test_payload_ensure_ascii_blocks_u2028_line_split():
    s = _session()
    s["dialogue"][0]["content"] = "line one" + U2028 + "line two"
    lines = build_session_lines("EP-U", s, 1)
    payload = payload_of(lines)
    assert U2028 not in payload  # ensure_ascii=True 转义后不再以字面出现
    parsed = [json.loads(x) for x in payload.split("\n")]
    assert len(parsed) == len(lines)  # 行界唯一 = \n


def test_format_retrieval_normal_path():
    sj = {
        "gate": {"needs_memory": True, "reason": "self_reference"},
        "results": [
            {"memory": {"content": "[Session 3 of 21 | perturbation] User: I switched gyms."}},
            {"memory": {"content": "[Session 3 of 21 | perturbation] Assistant: Great."}},
            {"memory": {"content": ""}},  # 空内容不进上下文
        ],
    }
    ctx, meta = format_retrieval(sj, top_k=15)
    assert ctx.count("\n- ") == 1 and ctx.startswith("- [Session 3 of 21")
    assert meta["gate_blocked"] is False and meta["n_results"] == 3


def test_format_retrieval_gate_blocked_returns_empty():
    sj = {"gate": {"needs_memory": False, "reason": "chitchat"}, "results": []}
    ctx, meta = format_retrieval(sj, top_k=15)
    assert ctx == ""
    assert meta["gate_blocked"] is True and meta["gate_reason"] == "chitchat"


def test_format_retrieval_top_k_truncates():
    sj = {
        "gate": {"needs_memory": True},
        "results": [{"memory": {"content": f"item {i}"}} for i in range(20)],
    }
    ctx, meta = format_retrieval(sj, top_k=15)
    assert meta["n_results"] == 20 and len(ctx.splitlines()) == 15
