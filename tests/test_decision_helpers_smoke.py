"""决策辅助函数直调冒烟（v0.68 测试诚实性审计补强）。

审计结论：以下决策/安全函数此前只有「经调用栈间接真实执行」的测试，
没有任何直调用例（纪律字面缺口）：
- `gate.scorer.novelty_score`（新颖度打分，检索决策输入）
- `security.secret_guard.detect_secret_shapes` / `route_secret_hits`
  （密钥熔断检测与路由——「宁 miss 不脏写」待审转接）
- `core.acl.viewer_of` / `is_system_viewer`（读侧身份收敛单一真源）
- `ingestion.dialogue._is_chitchat`（闲聊沙汰判定；既有测试把它整个
  patch 掉来驱动 ingest_dialogue 的闲聊分支，函数自身无直调）

本文件真实构造最小输入直调，mock 仅 settings 与 conftest 既定外部替身
（embed）；`route_secret_hits` 的 DB 面走 param_env 真库。
"""

import pytest


def test_novelty_score_branches(param_env):
    from lantai.gate.scorer import novelty_score

    assert novelty_score("全新问题", []) == 1.0  # 无对照 → 完全新颖
    # conftest embed 替身是常向量：与非空对照的最大相似度 = 1 → 新颖度趋 0
    score = novelty_score("同一问题", ["已有记忆甲", "已有记忆乙"])
    assert 0.0 <= score <= 1e-6


def test_detect_secret_shapes_modes(monkeypatch):
    from lantai.core.settings import settings
    from lantai.security.secret_guard import detect_secret_shapes

    # 假密钥形状走拼接组装（运行时才成完整形状），绕过提交闸明文扫描——
    # 同 tests/test_key_gate_and_shiyi_perf.py:27 惯例。
    secret = "记住：我的 key 是 " + "sk-" + "F" * 24
    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "off")
    assert detect_secret_shapes(secret) == []  # off 直通不检测

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    assert detect_secret_shapes(secret) == ["openai_key"]
    assert detect_secret_shapes("普通内容没有密钥") == []


def test_route_secret_hits_pending_review(param_env, monkeypatch):
    from sqlmodel import select

    from lantai.core.settings import settings
    from lantai.models.tables import MemoryCandidate
    from lantai.security.secret_guard import route_secret_hits

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id="cand-secret-smoke",
                document_id="doc-secret-smoke",
                user_id="secret-smoke",
                status="new",
            )
        )
        s.commit()

    result = route_secret_hits(
        {"ok": True, "candidate_id": "cand-secret-smoke", "status": "new"},
        ["openai_key"],
    )
    assert result["status"] == "pending_review"
    with session_factory() as s:
        assert s.get(MemoryCandidate, "cand-secret-smoke").status == "pending_review"


def test_route_secret_hits_passthrough_branches(param_env, monkeypatch):
    from lantai.core.settings import settings
    from lantai.security.secret_guard import route_secret_hits

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    original = {"ok": True, "candidate_id": "cand-x"}
    assert route_secret_hits(dict(original), ["openai_key"]) == original  # 非 enforce 原样

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    no_candidate = {"ok": True}  # dedup merge 等无新建候选分支：原样
    assert route_secret_hits(no_candidate, ["openai_key"]) is no_candidate
    assert route_secret_hits({"ok": True, "candidate_id": "cand-y"}, []) == {
        "ok": True,
        "candidate_id": "cand-y",
    }  # 无命中原样


def test_is_chitchat_branches(monkeypatch):
    from lantai.core.settings import settings
    from lantai.ingestion.dialogue import _is_chitchat

    monkeypatch.setattr(settings, "DIALOGUE_MIN_CHARS", 4)
    assert _is_chitchat("嗯") is True  # 过短
    assert _is_chitchat("谢谢再见！") is True  # 纯社交结束语（NO_MEMORY_PATTERNS 整段匹配）
    assert _is_chitchat("部署用蓝绿发布，先切流量再摘旧节点") is False  # 真内容


def test_viewer_of_and_system_viewer():
    from lantai.core.acl import SYSTEM_VIEWER, Principal, is_system_viewer, viewer_of

    assert viewer_of(None) == "default"
    assert viewer_of(Principal(user_id="u1")) == "u1"
    assert viewer_of(Principal(user_id=None)) == "default"
    assert viewer_of(Principal()) == "default"

    assert is_system_viewer(Principal(user_id=SYSTEM_VIEWER)) is True
    # 判据只有 user_id==SYSTEM_VIEWER 一条：role=system 但 user_id 缺省不算
    # 显式系统身份（viewer_of 收敛到 "default"）——这正是票 mcp-identity-gaps/06
    # 要拆开的「None 两种解释」之一。
    assert is_system_viewer(Principal(role="system")) is False
    assert is_system_viewer(Principal(user_id="u1")) is False
    assert is_system_viewer(None) is False
