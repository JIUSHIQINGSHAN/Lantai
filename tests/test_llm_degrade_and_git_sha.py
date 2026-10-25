"""票 J（.scratch/v0.24-next-window-2026-10/07）：司天 LLM 降级率 + /health git_sha。

不 mock 冒烟纪律：指标三态用内存 SQLite 真实建表真实插行直调
`collect_llm_degrade` / `evaluate_alerts`，不 mock 被测内部计算；
client 包装器的结局记录用 monkeypatch 替换内部 retry 函数（外部网络面）；
git_sha 的子进程调用属外部副作用，按纪律允许 mock。
"""

from datetime import timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.llm.client as client_module
from lantai.core.time import utcnow
from lantai.llm.client import record_llm_outcome
from lantai.models.tables import LLMCallEvent
from lantai.ops.monitor import collect_llm_degrade, evaluate_alerts


@pytest.fixture()
def db_env(monkeypatch, tmp_path):
    """内存 SQLite 真实建表 + client 遥测直写本库（不 mock 内部计算）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory, engine


def _seed(session_factory, *, ok: int, error: int, age_hours: float = 1.0):
    now = utcnow()
    stamp = f"{int(now.timestamp() * 1000)}_{ok}_{error}_{age_hours}"
    with session_factory() as s:
        for i in range(ok):
            s.add(
                LLMCallEvent(
                    id=f"ok_{stamp}_{i}",
                    channel="chat",
                    outcome="ok",
                    created_at=now - timedelta(hours=age_hours),
                )
            )
        for i in range(error):
            s.add(
                LLMCallEvent(
                    id=f"err_{stamp}_{i}",
                    channel="embed",
                    outcome="error",
                    created_at=now - timedelta(hours=age_hours),
                )
            )
        s.commit()


def test_degrade_metric_three_states(db_env):
    session_factory, _ = db_env
    # ① 零调用 = 无判据：不判红不判绿
    with session_factory() as s:
        m = collect_llm_degrade(s)
    assert m["judgment"] == "no_evidence" and m["degraded_rate_24h"] is None

    # ② 有调用有失败 = degraded，率值如实
    _seed(session_factory, ok=8, error=2)
    with session_factory() as s:
        m = collect_llm_degrade(s)
    assert m["judgment"] == "degraded"
    assert m["calls_total_24h"] == 10 and m["calls_failed_24h"] == 2
    assert m["degraded_rate_24h"] == pytest.approx(0.2)

    # ③ 窗口外的事件不计入（24h 滚动窗口语义）
    _seed(session_factory, ok=0, error=5, age_hours=30.0)
    with session_factory() as s:
        m = collect_llm_degrade(s)
    assert m["calls_total_24h"] == 10  # 30h 前的 5 条不计


def test_degrade_metric_all_ok(db_env):
    session_factory, _ = db_env
    _seed(session_factory, ok=6, error=0)
    with session_factory() as s:
        m = collect_llm_degrade(s)
    assert m["judgment"] == "ok" and m["degraded_rate_24h"] == 0.0


def test_alert_fires_only_at_high_rate():
    """≥10 次且失败率 ≥50% 才告 high；低失败率或样本不足不告。"""
    base = {
        "llm_degrade": {
            "judgment": "degraded",
            "calls_total_24h": 20,
            "calls_failed_24h": 12,
            "degraded_rate_24h": 0.6,
        }
    }
    ids = [a["id"] for a in evaluate_alerts(base)]
    assert "llm_degrade_high" in ids

    mild = {
        "llm_degrade": {
            "judgment": "degraded",
            "calls_total_24h": 100,
            "calls_failed_24h": 10,
            "degraded_rate_24h": 0.1,
        }
    }
    assert "llm_degrade_high" not in [a["id"] for a in evaluate_alerts(mild)]

    thin = {
        "llm_degrade": {
            "judgment": "degraded",
            "calls_total_24h": 5,
            "calls_failed_24h": 5,
            "degraded_rate_24h": 1.0,
        }
    }
    assert "llm_degrade_high" not in [a["id"] for a in evaluate_alerts(thin)]


def test_client_records_ok_and_error(db_env):
    """包装器直写结局：成功 ok、异常 error（_timed 面为被测对象，内部 fn 为外部网络替身）。"""
    import lantai.storage.db as db_module

    @client_module._timed("chat")
    def fake_ok(system, user):
        return {"ok": True}

    @client_module._timed("chat")
    def boom(system, user):
        raise RuntimeError("provider down")

    assert fake_ok("s", "u") == {"ok": True}
    with pytest.raises(RuntimeError):
        boom("s", "u")

    with db_module.get_session() as s:
        rows = s.query(LLMCallEvent).all()
    outcomes = sorted(r.outcome for r in rows)
    assert outcomes == ["error", "ok"]
    assert all(r.channel == "chat" for r in rows)


def test_record_llm_outcome_never_raises(monkeypatch):
    """遥测自身失败必须静默（无库/表不存在），不得反噬外呼。"""

    def no_session():
        raise RuntimeError("db unavailable")

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", no_session)
    record_llm_outcome("chat", "ok", 5)  # 不抛即过


# ── /health git_sha ──────────────────────────────────────────────────────


def _reset_sha_cache():
    import lantai.api.routes_health as rh

    rh._git_sha_cache = None
    return rh


def test_health_git_sha_from_subprocess(monkeypatch):
    rh = _reset_sha_cache()
    import subprocess

    class FakeCompleted:
        returncode = 0
        stdout = "6a58faa4deadbeef000000000000000000000000\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted())
    monkeypatch.setattr(rh, "_find_git", lambda: r"C:\fake\git.exe")
    assert rh._current_git_sha() == "6a58faa4deadbeef000000000000000000000000"


def test_health_git_sha_missing_git(monkeypatch):
    rh = _reset_sha_cache()
    monkeypatch.setattr(rh, "_find_git", lambda: None)
    assert rh._current_git_sha() is None  # 取不到返回 None，不抛


def test_health_returns_sha_key(monkeypatch):
    rh = _reset_sha_cache()
    monkeypatch.setattr(rh, "_current_git_sha", lambda: "abc123")
    assert rh.health() == {"ok": True, "git_sha": "abc123"}
