"""票 17 子项 4/5：dialogue 摄取两处 stray 的回归锁。

子项 4（X-06 单一真源）：`dialogue.py:143` 的
`if secret_hits and settings.SECRET_GUARD_MODE == "enforce"` 是对
`secret_guard.route_secret_hits` 内部判据的调用方复制（secret_guard 模块
docstring 明文「调用方只做挂载，不各自复制 mode 分支」；off 由
`detect_secret_shapes` 内部处理，shadow/enforce 由 `route_secret_hits`
内部处理——三态行为等价，纯结构违例）。v0.63 `48e12749` 下沉时摘了
fastpath 臂，extract 臂漏网。

spy 锁的语义：`route_secret_hits` 是 X-06 单一真源，spy 仅记录调用并
**委托真实现**（不替换行为）——断言的是「调用方必须咨询真源」这一结构
契约，不是 mock 被测函数内部逻辑。

子项 5（裸 except）：`dialogue.py:211` 的 `except Exception: pass`——
事件时间提取异常零日志零标记；`event_time_extract_failed` 标记只在
et=None 臂，异常臂漏（票面：异常类型无界、无日志留痕）。
"""

import logging
from unittest.mock import patch

import pytest

import lantai.security.secret_guard as secret_guard_mod
from lantai.core.settings import settings
from lantai.ingestion.dialogue import _is_chitchat
from lantai.parsing.fastpath import fastpath_check

# 运行时拼装假密钥（git 安全钩子拦字面量，同 test_secret_guard_ingest 口径）
_FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
_SECRET_TEXT = (
    f"上周在客户机房部署时把 {_FAKE_AWS_KEY} 写进了部署脚本，回来我整理了一份轮换与吊销的记录文档"
)

_EXTRACT_STUB = {
    "summary": "部署脚本密钥轮换记录",
    "claims": [],
    "methods": [],
    "constraints": [],
    "actions": [],
    "topic": ["部署"],
    "extractor_confidence": 0.8,
}


def _assert_extract_path(text: str) -> None:
    """测试文本必须真走 extract 臂（否则 spy/矩阵断言无效）。"""
    assert not fastpath_check(text), "文本命中 fastpath——不是 extract 臂"
    assert not _is_chitchat(text), "文本被判闲聊——不是 extract 臂"


def test_extract_path_consults_single_source_router(monkeypatch, param_env):
    """红测：shadow 态 extract 臂必须咨询单一真源 route_secret_hits。

    复制分支在位时（mode != enforce 短路），route 全程 0 调用——真源被绕过。
    """
    _assert_extract_path(_SECRET_TEXT)
    session_factory, _ = param_env
    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    assert secret_guard_mod.detect_secret_shapes(_SECRET_TEXT), "文本必须命中密钥形状"

    calls = []
    real = secret_guard_mod.route_secret_hits

    def spy(result, hits):
        calls.append(tuple(hits))
        return real(result, hits)

    monkeypatch.setattr(secret_guard_mod, "route_secret_hits", spy)

    from lantai.ingestion.dialogue import ingest_dialogue

    with patch("lantai.parsing.extractor.chat_json", return_value=dict(_EXTRACT_STUB)):
        result = ingest_dialogue(_SECRET_TEXT)

    assert calls, "extract 臂未咨询 route_secret_hits——调用方复制 mode 分支（X-06 违例）"
    assert result["status"] == "new"  # shadow 检测+记录不阻断，行为保持
    with session_factory() as s:
        from lantai.models.tables import MemoryCandidate

        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == "new"


@pytest.mark.parametrize(
    "mode,expected_status",
    [("off", "new"), ("shadow", "new"), ("enforce", "pending_review")],
)
def test_extract_path_mode_matrix_unchanged(monkeypatch, param_env, mode, expected_status):
    """三态矩阵现状锁：摘除复制分支前后，extract 臂终态必须逐模态一致
    （off 不检测 / shadow 检测+记录不阻断 / enforce 命中转待审）。"""
    _assert_extract_path(_SECRET_TEXT)
    session_factory, _ = param_env
    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", mode)

    from lantai.ingestion.dialogue import ingest_dialogue

    with patch("lantai.parsing.extractor.chat_json", return_value=dict(_EXTRACT_STUB)):
        result = ingest_dialogue(_SECRET_TEXT)

    assert result["status"] == expected_status
    with session_factory() as s:
        from lantai.models.tables import MemoryCandidate

        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == expected_status


def test_fastpath_path_consults_router(monkeypatch, param_env):
    """现状锁：fastpath 臂本就无条件走真源（v0.63 已摘）——修复不得弄坏。"""
    text = "记住：生产数据库的维护窗口改到周六凌晨两点"
    assert fastpath_check(text), "文本必须命中 fastpath"
    session_factory, _ = param_env
    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")

    calls = []
    real = secret_guard_mod.route_secret_hits

    def spy(result, hits):
        calls.append(tuple(hits))
        return real(result, hits)

    monkeypatch.setattr(secret_guard_mod, "route_secret_hits", spy)

    from lantai.ingestion.dialogue import ingest_dialogue

    result = ingest_dialogue(text)
    assert calls, "fastpath 臂未咨询 route_secret_hits——v0.63 既有行为被破坏"
    assert result["status"] == "fastpath"


# 子项 5（裸 except 留痕）的回归锁在本文件收口子项 4 后单独追加（红→绿分笔提交）。
