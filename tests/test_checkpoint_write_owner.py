"""`checkpoint_write` 在宿主不透传身份时落 NULL 属主行——底本人人可读。

票 `.scratch/mcp-identity-gaps/issues/04-checkpoint-write-null-owner.md`

`write_session_checkpoint:77` 用 `getattr(principal, "user_id", None)` 取
owner，`principal=None` 时落 NULL 属主行；而读侧 `_checkpoint_scope:62`
的口径是 `user_id == viewer OR user_id IS NULL`——**NULL 行人人可读**。
宿主不透传 `user_id` 调 MCP `checkpoint_write`，之后任何用户都能读到
那五段工作现场。

同批探针的四个兄弟写工具（`raw_add` / `add_dialogue` / `scratchpad_write`）
在不透传身份时全部落 `"default"`，**只有这一个落 NULL**。

**测试策略**：真 handler + 真 `write_session_checkpoint` + 内存 SQLite
真建表，不 mock 内部逻辑——要验证的正是 owner 那一行的取值。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.auth import Principal


@pytest.fixture()
def engine():
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", lambda: Session(e))
        yield e


BLOCKS = {
    "cp_active_intent": "正在排查底本归属泄漏",
    "cp_next_action": "跑全量测试确认无回归",
}


def _write(engine, params: dict) -> dict:
    import lantai.cli.mcp as mcp

    return mcp.handle_checkpoint_write(params)


def _rows(engine, session_id: str) -> list[tuple]:
    with Session(engine) as s:
        return s.execute(
            text(
                "select block_key, user_id from session_checkpoint "
                "where session_id = :sid order by block_key"
            ),
            {"sid": session_id},
        ).fetchall()


class TestCheckpointWriteOwner:
    """宿主不透传身份时，底本必须落 `default` 属主而不是 NULL。"""

    def test_no_user_id_stamps_default(self, engine):
        """决定性：不透传 user_id → 落 `default`，不是 NULL。

        修前 `getattr(principal,'user_id',None)` 在 principal=None 时
        给 None，而读侧把 NULL 当"人人可读"——那就是本票的洞。
        """
        resp = _write(engine, {"session_id": "sess-noident", "blocks": BLOCKS})
        assert resp["blocks_written"] == 2, resp

        owners = {r[1] for r in _rows(engine, "sess-noident")}
        assert owners == {"default"}, (
            f"底本落成了 {owners}（该是 {{'default'}}）——"
            "NULL 属主行在读侧口径下人人可读，那是把工作现场敞开"
        )

    def test_explicit_user_id_stamps_that_user(self, engine):
        """透传 user_id → 落该用户（不能修废正常路径）。"""
        resp = _write(
            engine,
            {"session_id": "sess-userA", "blocks": BLOCKS, "user_id": "user-A"},
        )
        assert resp["blocks_written"] == 2, resp

        owners = {r[1] for r in _rows(engine, "sess-userA")}
        assert owners == {"user-A"}, f"透传了 user-A 却落成 {owners}"

    def test_principal_none_stamps_default(self, engine):
        """`principal=None`（内部 worker / 脚本）→ 落 `default`。

        这是修复本身：docstring 原写"内部调用留 NULL"，但全仓**没有任何**
        worker/CLI 调用者（grep 只有 HTTP 路由与 MCP 两个调用方），
        那条设计对应的场景不存在。
        """
        from lantai.services.checkpoint_service import write_session_checkpoint

        write_session_checkpoint("sess-internal", BLOCKS, principal=None)

        owners = {r[1] for r in _rows(engine, "sess-internal")}
        assert owners == {"default"}, f"principal=None 落成了 {owners}（该是 'default'）"

    def test_admin_stamps_default_and_still_readable(self, engine):
        """admin（真形态 `user_id=None`）→ 落 `default`，且自己读得到。

        admin 没有 user_id（auth.py:168），`viewer_of` 收敛到 "default"。
        不能修成"admin 不许写底本"——读侧 `== viewer OR IS NULL` 照样放行。
        """
        from lantai.services.checkpoint_service import (
            get_latest_checkpoint,
            write_session_checkpoint,
        )

        write_session_checkpoint("sess-admin", BLOCKS, principal=Principal(role="admin"))

        owners = {r[1] for r in _rows(engine, "sess-admin")}
        assert owners == {"default"}, f"admin 落的属主是 {owners}（该是 'default'）"

        latest = get_latest_checkpoint(principal=Principal(role="admin"))
        assert latest is not None and latest["blocks"], "admin 写完读不到自己的底本"

    def test_latest_checkpoint_still_reads_own(self, engine):
        """回归护栏：透传 user_id 后 `checkpoint_latest` 仍读得到。"""
        _write(engine, {"session_id": "sess-read", "blocks": BLOCKS, "user_id": "user-A"})

        from lantai.cli.mcp import handle_checkpoint_latest

        out = handle_checkpoint_latest({"user_id": "user-A"})
        assert out and out.get("blocks"), f"写完立刻读不到：{out}"

    def test_null_owner_legacy_rows_still_visible(self, engine):
        """回归护栏：**读侧不收紧**——NULL 属主老行仍可见。

        02 号票的价值观是"读操作不收紧"。本票只改写入时落哪个属主，
        读侧 `user_id == viewer OR user_id IS NULL` 的 NULL 分支必须留着，
        否则单人部署丢掉历史底本。
        """
        with Session(engine) as s:
            s.execute(
                text(
                    "insert into session_checkpoint "
                    "(session_id, block_key, content, created_at, user_id) values "
                    "('sess-legacy', 'cp_active_intent', '老底本', '2026-01-01', NULL)"
                )
            )
            s.commit()

        from lantai.services.checkpoint_service import get_checkpoint

        got = get_checkpoint("sess-legacy", principal=Principal(user_id="user-A"))
        assert got is not None, "NULL 属主老底本对 user-A 不可见了——读侧被收紧了"
