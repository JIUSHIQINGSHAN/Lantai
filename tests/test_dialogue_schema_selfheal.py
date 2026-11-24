"""对话通道缺表自愈（票 90）不 mock 冒烟。

真机验收实证（2026-11-24）：真实库 `user_version=44`，代码到 v47，缺
`ingest_event` / `conversation_message` / `conversation_job` 三表。钩子独立
进程不跑 `init_db`，首次 `dialogue` 提交即 `no such table: ingest_event`，
而 `_handle_dialogue` 的 except 把它吞成 `{}`——宿主看到的与「通道没开」同形。

本测试真实执行：造一个**只有空文件、无任何表**的库，调 `_handle_dialogue`，
验证 (1) 能自愈建表并产出候选，(2) 补建后仍失败时返回 ok=False 而非静默。
外部网络（LLM 提取）被 mock 成固定结构——它是外部面，不是被测逻辑。
"""

import importlib.util
import os

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


def _load_hook():
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def empty_db(monkeypatch, tmp_path):
    """空库（无任何表）+ get_session 指向它，模拟真实库滞后 schema。"""
    import lantai.storage.db as db_module

    db_path = tmp_path / "remembrance.db"
    db_path.write_bytes(b"")
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "get_session", lambda: __import__("sqlmodel").Session(engine))
    monkeypatch.setattr(db_module, "init_db", lambda: SQLModel.metadata.create_all(engine))
    return db_path, engine


def test_dialogue_selfheals_missing_tables(monkeypatch, empty_db):
    """缺表 → 补建 → 重试成功，且返回 ok=True 与候选 id。"""
    import lantai.models.tables  # noqa: F401  注册 metadata
    import lantai.parsing.extractor as extractor

    monkeypatch.setattr(
        extractor,
        "extract_candidate",
        lambda title, text: {
            "topic": ["兰台", "预算"],
            "summary": "检索预算由 2 秒改为 8 秒",
            "claims": ["SHELL_HOOK_TIMEOUT 调整为 8 秒"],
            "methods": [],
            "constraints": [],
            "actions": [],
            "contradictions": [],
            "entities": [],
            "extractor_confidence": 0.9,
        },
    )

    mod = _load_hook()
    out = mod._handle_dialogue(
        "大哥确认：兰台的检索预算参数 SHELL_HOOK_TIMEOUT 从 2 秒改成 8 秒，Windows 冷启动实测较慢。",
        session_id="selfheal-test",
        turn=1,
    )

    assert out.get("ok") is True, f"缺表自愈后应成功，实得 {out!r}"
    assert out.get("candidate_id"), "应产出候选 id"
    assert out.get("ingest_event_id"), "应产出 ingest_event id"

    # 表真的建出来了
    db_path, engine = empty_db
    with engine.connect() as conn:
        tables = {
            r[0] for r in conn.exec_driver_sql("select name from sqlite_master where type='table'")
        }
    assert "ingest_event" in tables, f"自愈后应存在 ingest_event，实有 {sorted(tables)[:8]}"
    assert "memorycandidate" in tables


def test_dialogue_retry_failure_is_reported_not_swallowed(monkeypatch, empty_db):
    """补建后仍失败 → 返回 ok=False + error（不静默吞成 {}）。"""
    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_module, "init_db", boom)

    mod = _load_hook()
    out = mod._handle_dialogue("任意足够长的对话内容用于触发提取流程。", session_id="x", turn=1)

    assert out.get("ok") is False, f"重试失败必须如实上报，实得 {out!r}"
    assert "error" in out, "应带 error 说明"


def test_non_schema_error_still_zero_intrusion(monkeypatch, empty_db):
    """非缺表异常保持零侵入（回 {}），不把钩子拖垮。"""
    import lantai.ingestion.dialogue as dialogue_mod
    import lantai.models.tables  # noqa: F401

    def boom(*a, **kw):
        raise ValueError("some other failure")

    monkeypatch.setattr(dialogue_mod, "ingest_dialogue", boom)

    mod = _load_hook()
    out = mod._handle_dialogue("任意足够长的对话内容用于触发提取流程。", session_id="x", turn=1)
    assert out == {}, f"非 schema 异常应零侵入回 {{}}，实得 {out!r}"
