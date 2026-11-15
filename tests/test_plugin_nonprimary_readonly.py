"""Hermes 的 cron 和子智能体会话只读兰台、不写兰台（周报 2026-11-08 改进 1）。

Hermes 的 pre_llm_call / on_session_end 都带 `platform`：cron 调度跑的 agent 是
"cron"（cron/scheduler.py），委派出去的子智能体是 "subagent"（tools/delegate_tool.py），
用户自己的会话是 "cli"/"desktop"/各消息平台等。Hermes 的 MemoryProvider 接口文档写明
非主会话跳过写入。修复前 lantai-hook 不看 platform：cron 的任务提示词进会话缓冲，
on_session_end 把它写成底本「上次在做」、送进对话写通道，还给注入的记忆回执（保活）。

前半段 mock 掉 serve 子进程的全部出站调用，只断言插件调了哪些；最后一组把底本
读写接到真实 checkpoint_service + 内存 SQLite，断言用户下一场会话拿到的底本。
"""

import importlib.util
import time
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"
CRON_PROMPT = "每天早上读取 deploy.log，总结过去 24 小时的失败部署并推送到 Telegram"
SUBAGENT_PROMPT = "你是一个代码审查子智能体，审查 lantai/retrieval/hybrid.py 的改动并列出风险"
USER_MSG = "在写兰台 v0.68 的发布说明，下一步补 CHANGELOG"
HOOK_RESP = {
    "context": "[兰台记忆] 用户对花生过敏",
    "event_id": "evt_1",
    "evidence": [{"id": "m1"}, {"id": "m2"}],
}


def _load(monkeypatch, env_value=None):
    if env_value is None:
        monkeypatch.delenv("LANTAI_HOOK_READONLY_PLATFORMS", raising=False)
    else:
        monkeypatch.setenv("LANTAI_HOOK_READONLY_PLATFORMS", env_value)
    spec = importlib.util.spec_from_file_location("lantai_hook_readonly", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


@pytest.fixture()
def mod(monkeypatch):
    return _load(monkeypatch)


def _run_turn(mod, text, sid, platform, hook_resp=HOOK_RESP, ck="【上次在做】某事"):
    """跑一整轮（pre_llm_call + on_session_end），返回注入结果和各写出口的 mock。"""
    with (
        patch.object(mod, "_call_hook", return_value=dict(hook_resp) if hook_resp else None),
        patch.object(mod, "_call_checkpoint", return_value=ck),
        patch.object(mod, "_call_backfill") as bf,
        patch.object(mod, "_call_dialogue") as dlg,
        patch.object(mod, "_call_checkpoint_write") as cw,
        patch.object(mod, "_call_veto") as veto,
    ):
        injected = mod._on_pre_llm_call(
            user_message=text, session_id=sid, turn_id="t1", platform=platform
        )
        mod._on_session_end(
            session_id=sid,
            turn_id="t1",
            completed=True,
            turn_exit_reason="text_response(stop)",
            platform=platform,
        )
    return injected, {"backfill": bf, "dialogue": dlg, "checkpoint_write": cw, "veto": veto}


@pytest.mark.parametrize(
    ("platform", "text", "sid"),
    [
        ("cron", CRON_PROMPT, "cron_job42_20261108_080000"),
        ("subagent", SUBAGENT_PROMPT, "sub_7f3a"),
        ("CRON", CRON_PROMPT, "cron_upper"),  # 大小写不敏感
    ],
)
def test_nonprimary_turn_writes_nothing(mod, platform, text, sid):
    _, calls = _run_turn(mod, text, sid, platform)
    calls["dialogue"].assert_not_called()
    calls["checkpoint_write"].assert_not_called()
    calls["backfill"].assert_not_called()
    calls["veto"].assert_not_called()
    assert sid not in mod._session_buffers
    assert sid not in mod._session_turns


def test_nonprimary_turn_still_gets_injection(mod):
    injected, _ = _run_turn(mod, CRON_PROMPT, "cron_job42", "cron")
    assert injected is not None
    assert "【上次在做】某事" in injected["context"]
    assert "用户对花生过敏" in injected["context"]


def test_nonprimary_without_session_does_not_backfill_immediately(mod):
    # 无 session 的轮次原本立即回执；非主会话同样不该回执。
    injected, calls = _run_turn(mod, CRON_PROMPT, "", "cron")
    assert injected and "用户对花生过敏" in injected["context"]
    calls["backfill"].assert_not_called()


@pytest.mark.parametrize("platform", ["cli", "desktop", "telegram", "", None])
def test_primary_turn_unchanged(mod, platform):
    _, calls = _run_turn(mod, USER_MSG, "user_s1", platform)
    assert [c.args[0] for c in calls["dialogue"].call_args_list] == [USER_MSG]
    calls["checkpoint_write"].assert_called_once()
    calls["backfill"].assert_called_once_with("evt_1", ["m1", "m2"])


def test_missing_platform_kwarg_treated_as_primary(mod):
    # 旧版 Hermes 不传 platform：行为与修复前一致。
    with (
        patch.object(mod, "_call_hook", return_value=dict(HOOK_RESP)),
        patch.object(mod, "_call_checkpoint", return_value=None),
        patch.object(mod, "_call_backfill") as bf,
        patch.object(mod, "_call_dialogue") as dlg,
        patch.object(mod, "_call_checkpoint_write") as cw,
    ):
        mod._on_pre_llm_call(user_message=USER_MSG, session_id="old_hermes")
        mod._on_session_end(session_id="old_hermes", completed=True)
    dlg.assert_called_once()
    cw.assert_called_once()
    bf.assert_called_once()


def test_env_empty_disables_isolation(monkeypatch):
    m = _load(monkeypatch, env_value="")
    _, calls = _run_turn(m, CRON_PROMPT, "cron_job42", "cron")
    calls["dialogue"].assert_called_once()
    calls["checkpoint_write"].assert_called_once()


def test_env_custom_list(monkeypatch):
    m = _load(monkeypatch, env_value="cron, batch")
    _, batch_calls = _run_turn(m, "批处理：重跑 1-10 号数据", "b1", "batch")
    batch_calls["dialogue"].assert_not_called()
    _, sub_calls = _run_turn(m, SUBAGENT_PROMPT, "sub_1", "subagent")
    sub_calls["dialogue"].assert_called_once()  # subagent 不在自定义列表里 → 照写


def test_dotenv_value_is_read(monkeypatch, tmp_path):
    m = _load(monkeypatch)
    (tmp_path / "scripts").mkdir()
    hook = tmp_path / "scripts" / "shell_hook.py"
    hook.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LANTAI_HOOK_READONLY_PLATFORMS=cron\n", encoding="utf-8")
    assert m._readonly_platforms(str(hook)) == frozenset({"cron"})
    (tmp_path / ".env").write_text("OTHER=1\n", encoding="utf-8")
    assert m._readonly_platforms(str(hook)) == m._READONLY_PLATFORMS_DEFAULT


def test_interleaved_user_and_cron_sessions_keep_user_buffer(mod):
    """用户会话进行中插进一轮 cron：用户缓冲不受影响，cron 什么也不留。"""
    with (
        patch.object(mod, "_call_hook", return_value=None),
        patch.object(mod, "_call_checkpoint", return_value=None),
    ):
        mod._on_pre_llm_call(user_message=USER_MSG, session_id="user_s1", platform="cli")
        mod._on_pre_llm_call(user_message=CRON_PROMPT, session_id="cron_j", platform="cron")
    assert [e["text"] for e in mod._session_buffers["user_s1"]] == [USER_MSG]
    assert "cron_j" not in mod._session_buffers


# ── 集成：真实 checkpoint_service（内存 SQLite）───────────────────────────────────────


@pytest.fixture()
def real_checkpoint_db():
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", lambda: Session(e))
        yield e


def _wire_real_checkpoint(mod):
    from lantai.services.checkpoint_service import (
        inject_checkpoint_context,
        write_session_checkpoint,
    )

    return (
        patch.object(
            mod,
            "_call_checkpoint_write",
            side_effect=lambda sid, b: write_session_checkpoint(sid, b),
        ),
        patch.object(
            mod, "_call_checkpoint", side_effect=lambda: inject_checkpoint_context() or None
        ),
    )


@pytest.mark.parametrize(
    ("platform", "prompt"), [("cron", CRON_PROMPT), ("subagent", SUBAGENT_PROMPT)]
)
def test_next_user_session_checkpoint_not_overwritten(mod, real_checkpoint_db, platform, prompt):
    write_patch, read_patch = _wire_real_checkpoint(mod)
    with (
        write_patch,
        read_patch,
        patch.object(mod, "_call_hook", return_value=None),
        patch.object(mod, "_call_dialogue"),
        patch.object(mod, "_call_backfill"),
    ):
        # 用户自己的会话先写底本
        mod._on_pre_llm_call(user_message=USER_MSG, session_id="user_s1", platform="cli")
        mod._on_session_end(session_id="user_s1", completed=True, platform="cli")
        time.sleep(0.02)  # created_at 错开：修复前 cron 那条会是全库 newest
        # 然后跑一轮 cron / 子智能体
        mod._on_pre_llm_call(user_message=prompt, session_id=f"{platform}_run", platform=platform)
        mod._on_session_end(session_id=f"{platform}_run", completed=True, platform=platform)
        # 用户新开一场会话，首轮拿底本
        injected = mod._on_pre_llm_call(user_message="继续", session_id="user_s2", platform="cli")
    ctx = (injected or {}).get("context") or ""
    assert "CHANGELOG" in ctx
    assert prompt not in ctx
