"""放大面锁：批量采纳 AI 预审的单批条数上限。

`/candidates/batch_apply_triage` 的请求体是裸 `dict`，`actions` 没有上限：
每条 approve 至少一次写事务、并可能触发一次外部 LLM（propose）。一个请求
因此可放大成上万次写 + 上万次 LLM 调用。

本锁验三层：service 拒绝超限、REST 译成 422、恰在上限的批次照常处理
（反向保险，防「干脆全拒」的假修）。不 mock 产品逻辑。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.services.auto_triage_service import _BATCH_ACTION_MAX, apply_ai_triage_batch


def _actions(n: int) -> list[dict]:
    return [{"id": f"cand-{i}", "action": "reject", "reason": "批量"} for i in range(n)]


def test_service_rejects_over_cap():
    with pytest.raises(ValueError, match="最多"):
        apply_ai_triage_batch(_actions(_BATCH_ACTION_MAX + 1), principal=None)


def test_service_accepts_exactly_at_cap(monkeypatch):
    """反向保险：恰好达到上限必须放行（不是 `>=` 的越界拒绝）。"""
    calls: list = []

    def fake_review(cid, *, approve, reason="", principal=None):
        calls.append(cid)
        return {"ok": True}

    import lantai.services.auto_triage_service as svc

    monkeypatch.setattr(svc, "review_candidate", fake_review)
    applied = apply_ai_triage_batch(_actions(_BATCH_ACTION_MAX), principal=None)
    assert applied["rejected"] == _BATCH_ACTION_MAX
    assert len(calls) == _BATCH_ACTION_MAX


def test_rest_route_translates_to_422(monkeypatch):
    """REST 面把 service 的 ValueError 译成 422（不是 500）。"""
    from lantai.api.app import app

    big = {"actions": _actions(_BATCH_ACTION_MAX + 1)}
    with TestClient(app) as client:
        resp = client.post("/candidates/batch_apply_triage", json=big)
    assert resp.status_code == 422, f"超限批次未被拦：{resp.status_code} {resp.text[:200]}"
