"""钩子对话写入与钩子检索同一属主（巡检 2026-11-25，票 90 延伸）。

病灶：票 90 让 `build_context` 按 `LANTAI_HOOK_USER_ID` / `LANTAI_HOOK_ROLE` 检索，
但 `_handle_dialogue` 仍调 `ingest_dialogue(text, session_id=, turn=)`，候选落
`user_id="default"`，promoter 照抄到 MemoryItem。非 admin 读侧只看
`user_id == 配置值 OR IS NULL`，于是配了 user 档属主后，钩子从对话里学到的
记忆钩子自己读不到。

这里不 mock 写入链：真 `_handle_one` → 真 `ingest_dialogue`（fastpath，不走 LLM）
→ 真 proposer/promoter → 真 `hybrid_search`（只替换 embed 与向量库，走 FTS）。
"""

import importlib.util
import os
from unittest.mock import Mock, patch

from sqlmodel import select

from lantai.models.tables import MemoryCandidate, MemoryItem

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


def _load_hook(monkeypatch, user_id: str = "", role: str = "user"):
    spec = importlib.util.spec_from_file_location("shell_hook_owner", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_USER_ID", user_id)
    monkeypatch.setattr(mod.settings, "LANTAI_HOOK_ROLE", role)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
    return mod


def _dialogue(mod, text: str) -> dict:
    import json

    with patch(
        "lantai.parsing.extractor.chat_json",
        side_effect=AssertionError("fastpath 不应触发 LLM 提取"),
    ):
        return mod._handle_one(json.dumps({"type": "dialogue", "text": text}, ensure_ascii=False))


def _promote(cand_id: str, key: str, content: str):
    with (
        patch(
            "lantai.evolution.proposer.chat_json",
            return_value={
                "proposal_type": "add",
                "target_key": key,
                "new_content": content,
                "memory_type": "semantic",
                "reason": "r",
                "confidence": 0.9,
            },
        ),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.evolution.promoter.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store", return_value=Mock(add=Mock(), delete=Mock())
        ),
    ):
        from lantai.evolution.promoter import apply_proposal
        from lantai.evolution.proposer import propose_from_candidate

        prop = propose_from_candidate(cand_id, {"decision": "promote_semantic"})
        assert apply_proposal(prop.id)["ok"] is True


def test_configured_owner_reaches_candidate(param_env, monkeypatch):
    """配了属主 → 对话候选落在这个属主下（改前是 default）。"""
    session_factory, _ = param_env
    mod = _load_hook(monkeypatch, user_id="alice", role="user")
    out = _dialogue(mod, "记住：我对花生过敏")
    assert out["ok"] is True and out["fastpath"] is True
    with session_factory() as s:
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.user_id == "alice"


def test_whitespace_owner_keeps_default(param_env, monkeypatch):
    """纯空白等于未配置：仍落 default，与读侧 `_hook_principal` 同口径。"""
    session_factory, _ = param_env
    mod = _load_hook(monkeypatch, user_id="   ", role="user")
    out = _dialogue(mod, "记住：我对花生过敏")
    with session_factory() as s:
        assert s.get(MemoryCandidate, out["candidate_id"]).user_id == "default"


def test_unconfigured_call_shape_unchanged(monkeypatch):
    """未配置时调用形状逐字不变（不多传 user_id）。"""
    mod = _load_hook(monkeypatch, user_id="", role="user")
    with patch(
        "lantai.ingestion.dialogue.ingest_dialogue",
        return_value={"ingested": True, "candidate_id": "c"},
    ) as m:
        mod._handle_one('{"type":"dialogue","text":"记住：明天开会","session_id":"s","turn":2}')
    m.assert_called_once_with("记住：明天开会", session_id="s", turn=2)


def test_dialogue_memory_visible_to_hook_read(param_env, monkeypatch):
    """全链：钩子写进来的记忆，钩子自己按同一属主检索得到。

    改前：mem.user_id == "default"，`hybrid_search(principal=alice)` 零命中。
    """
    session_factory, _ = param_env
    mod = _load_hook(monkeypatch, user_id="alice", role="user")
    out = _dialogue(mod, "记住：我对花生过敏")
    _promote(out["candidate_id"], "花生过敏", "用户对花生过敏")
    with session_factory() as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == "花生过敏")).one()
        assert mem.user_id == "alice"

    from lantai.retrieval.hybrid import hybrid_search

    with (
        patch("lantai.retrieval.hybrid.embed", side_effect=RuntimeError("no embed")),
        patch("lantai.retrieval.hybrid.classify_intent", return_value={"candidate_n": 20}),
    ):
        hits = hybrid_search("花生", top_k=5, principal=mod._hook_principal())
    ids = [(h.get("memory") or {}).get("id") for h in hits]
    assert mem.id in ids, f"钩子读不到自己写的记忆：{hits!r}"
