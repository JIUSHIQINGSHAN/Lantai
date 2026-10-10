"""密钥熔断接入对话入口(票 K 第一道闸)。

真实内存库 + 真实 ingest_dialogue;只 mock 外部 LLM。覆盖三模式:
off 不检测、shadow 只记录不转待审、enforce 命中转待审。
"""

from sqlmodel import Session

from lantai.models.tables import MemoryCandidate

SECRET_TEXT = "记住:我的key是 sk-abcdefghijklmnopqrstuvwx 请保存"


def _status(session_factory, candidate_id):
    with session_factory() as s:
        return s.get(MemoryCandidate, candidate_id).status


def test_off_mode_does_not_route_secret(param_env, monkeypatch):
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.ingestion.dialogue import ingest_dialogue

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "off")
    result = ingest_dialogue(SECRET_TEXT)
    assert _status(session_factory, result["candidate_id"]) == "fastpath"


def test_shadow_mode_logs_but_keeps_status(param_env, monkeypatch, caplog):
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.ingestion.dialogue import ingest_dialogue

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    result = ingest_dialogue(SECRET_TEXT)
    assert _status(session_factory, result["candidate_id"]) == "fastpath"
    assert "密钥熔断命中" in caplog.text


def test_enforce_mode_routes_secret_to_pending_review(param_env, monkeypatch):
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.ingestion.dialogue import ingest_dialogue

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    result = ingest_dialogue(SECRET_TEXT)
    assert result["status"] == "pending_review"
    assert _status(session_factory, result["candidate_id"]) == "pending_review"
