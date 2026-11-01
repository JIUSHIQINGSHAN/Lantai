"""D-03 置信度分路阈值回归测试（票 .scratch/v0.65-next-window-2026-11/03）。

验证：
1. 当 score_mode == 'rrf' 时，分数在 [0.15, 0.5) 区间的第 2/3 名候选能够正常通过过滤，不再被 0.5 一刀切；
2. 当 score_mode == 'reranker' 时，依然严格遵循 0.5 置信度下限过滤。
"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from lantai.core.settings import settings
from scripts.shell_hook import build_context


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr(settings, "DATA_FENCE_ENABLED", True)
    monkeypatch.setattr(settings, "SHELL_HOOK_SCORE_THRESHOLD", 0.5)
    monkeypatch.setattr(settings, "SHELL_HOOK_SCORE_THRESHOLD_RRF", 0.15)


def test_rrf_dimension_retains_second_and_third_candidates(clean_env):
    """RRF 降级路径下，排第 2、第 3 名的候选（如分数为 0.38、0.25）应正常保留。"""
    mock_candidates = [
        {"score": 0.58, "score_mode": "rrf", "memory": {"id": "m1", "content": "第一名重要偏好"}},
        {"score": 0.38, "score_mode": "rrf", "memory": {"id": "m2", "content": "第二名相关技术栈"}},
        {"score": 0.22, "score_mode": "rrf", "memory": {"id": "m3", "content": "第三名背景信息"}},
        {"score": 0.08, "score_mode": "rrf", "memory": {"id": "m4", "content": "第四名噪音弱相关"}},
    ]

    with (
        patch("scripts.shell_hook.relevance_check", return_value={"needs_memory": True}),
        patch("scripts.shell_hook.hybrid_search", return_value=mock_candidates),
    ):
        ctx = build_context("测试查询一个重要的技术栈问题")

    assert "context" in ctx
    c_text = ctx["context"]
    assert "第一名重要偏好" in c_text
    assert "第二名相关技术栈" in c_text
    assert "第三名背景信息" in c_text
    assert "第四名噪音弱相关" not in c_text


def test_reranker_dimension_strictly_enforces_half_threshold(clean_env):
    """Reranker 模式下，0.5 语义门禁依然严格生效，低于 0.5 的条目坚决剔除。"""
    mock_candidates = [
        {"score": 0.85, "score_mode": "reranker", "memory": {"id": "m1", "content": "强相关证据"}},
        {
            "score": 0.45,
            "score_mode": "reranker",
            "memory": {"id": "m2", "content": "弱相关证据（应被剔除）"},
        },
    ]

    with (
        patch("scripts.shell_hook.relevance_check", return_value={"needs_memory": True}),
        patch("scripts.shell_hook.hybrid_search", return_value=mock_candidates),
    ):
        ctx = build_context("测试重排门禁")

    assert "context" in ctx
    c_text = ctx["context"]
    assert "强相关证据" in c_text
    assert "弱相关证据（应被剔除）" not in c_text
