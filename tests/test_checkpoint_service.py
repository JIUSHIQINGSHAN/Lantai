"""底本（session checkpoint，ADR-0021）测试：五段会话快照服务。

测试纪律（AGENTS.md）：核心逻辑不 mock——纯函数直调；DB 操作用内存 SQLite
真实建表（patch db.engine/get_session，仅隔离存储）。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import SessionCheckpoint
from lantai.services.checkpoint_service import (
    BLOCK_LABELS,
    validate_blocks,
    write_session_checkpoint,
    get_checkpoint,
    get_latest_checkpoint,
    cleanup_old_checkpoints,
    inject_checkpoint_context,
)

_BLOCKS = {
    "cp_active_intent": "兰台记忆项目白皮书审阅",
    "cp_next_action": "实现底本五段会话快照",
    "cp_current_work": "checkpoint_service 开发中",
    "cp_key_decisions": "按 ADR-0021 移植 aiduMEM checkpoint.py 窄版",
    "cp_open_notes": "集成测试后更新白皮书",
}


@pytest.fixture()
def ckpt_env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory


def test_validate_blocks_pure():
    """纯函数：五段键白名单、过短内容丢弃、超长截断（宁 miss 不脏写）。"""
    ok = validate_blocks({**_BLOCKS, "evil_key": "x" * 100})
    keys = [k for k, _ in ok]
    assert keys == list(BLOCK_LABELS.keys())  # 白名单且有序
    assert "evil_key" not in keys
    short = validate_blocks({"cp_open_notes": "短"})
    assert short == []  # 过短不落
    long = validate_blocks({"cp_current_work": "长" * (settings.CHECKPOINT_MAX_CONTENT + 50)})
    assert len(long[0][1]) == settings.CHECKPOINT_MAX_CONTENT
    assert validate_blocks(None) == []


def test_write_get_roundtrip_upsert(ckpt_env):
    """写入 → 读取；同 session 重写 = 替换（upsert），不叠加。"""
    r = write_session_checkpoint("sess-01", _BLOCKS)
    assert r["blocks_written"] == 5
    cp = get_checkpoint("sess-01")
    assert cp["blocks"]["cp_active_intent"] == _BLOCKS["cp_active_intent"]
    # 重写只带 3 块 → 旧的 5 块被整体替换
    write_session_checkpoint("sess-01", {"cp_active_intent": "新意图",
                                         "cp_next_action": "新下一步",
                                         "cp_open_notes": "新待办"})
    cp2 = get_checkpoint("sess-01")
    assert len(cp2["blocks"]) == 3
    assert cp2["blocks"]["cp_active_intent"] == "新意图"
    assert "cp_current_work" not in cp2["blocks"]


def test_write_validation(ckpt_env):
    with pytest.raises(ValueError):
        write_session_checkpoint("ab", _BLOCKS)  # session_id < 3
    with pytest.raises(ValueError):
        write_session_checkpoint("   ", _BLOCKS)


def test_latest_is_newest_session(ckpt_env):
    write_session_checkpoint("sess-old", _BLOCKS)
    write_session_checkpoint("sess-new", {"cp_active_intent": "最新会话在做",
                                          "cp_next_action": "收尾",
                                          "cp_current_work": "x" * 5,
                                          "cp_key_decisions": "y" * 5,
                                          "cp_open_notes": "z" * 5})
    latest = get_latest_checkpoint()
    assert latest["session_id"] == "sess-new"
    assert latest["blocks"]["cp_active_intent"] == "最新会话在做"


def test_cleanup_keeps_n_sessions(ckpt_env):
    for i in range(8):
        write_session_checkpoint(f"sess-{i:02d}", {"cp_active_intent": f"会话 {i}"})
    r = cleanup_old_checkpoints(max_sessions=5)
    assert r["deleted"] == 3
    assert r["kept"] == 5
    assert get_checkpoint("sess-00") is None
    assert get_checkpoint("sess-07") is not None
    with pytest.raises(ValueError):
        cleanup_old_checkpoints(0)


def test_inject_format_and_staleness(ckpt_env, monkeypatch):
    write_session_checkpoint("sess-01", _BLOCKS)
    now = datetime.now(timezone.utc)
    text = inject_checkpoint_context(now=now)
    assert text.startswith("[Checkpoint · 上次会话]")
    assert "在做: 兰台记忆项目白皮书审阅" in text
    assert "待办: 集成测试后更新白皮书" in text
    # 陈旧标注：把 created_at 拨老 31 天
    with ckpt_env() as s:
        rows = s.exec(select(SessionCheckpoint)).all()
        for row in rows:
            row.created_at = now - timedelta(days=31)
        s.commit()
    text2 = inject_checkpoint_context(now=now)
    assert "30天+前" in text2
    # 清空后无合法块 → 空串（零侵入降级）
    write_session_checkpoint("sess-01", {"cp_active_intent": "清"})  # 过短不落
    assert inject_checkpoint_context(now=now) == ""
