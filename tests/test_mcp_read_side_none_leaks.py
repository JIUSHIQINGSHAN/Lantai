"""MCP 读侧两个工具在无身份时敞开：`checkpoint_latest` 拿 B 的 newest 底本、
`scratchpad_get` 按 id 拿 B 的札记。

票 `.scratch/mcp-identity-gaps/issues/05-mcp-read-side-none-leaks.md`

宿主不透传 `user_id` 时（MCP 没有 HTTP 鉴权层，这是常见形态），两个工具的
收窄都被"`principal is None`"整条跳过：
  - `get_latest_checkpoint`：`_checkpoint_scope(None)` 返回 None → 不加 where
    → 返回**全库 newest** 的完整五段快照，且**根本不看 session_id 参数**。
  - `get_scratchpad`：`if principal is not None` 把归属判断整块跳过，
    而上一行 `_owner_of(None)` 已经算出了 viewer=`"default"`——算好了不用。

修法：两个函数都收敛到 `viewer_of(None) == "default"` + `OR IS NULL`。
**04 号票保证了这个修法安全**：无身份写入现在落 `"default"`，
与无身份读取收敛到的 `"default"` 正好配对，不会"自己写了读不到"。

**测试策略**：真 handler + 真 service + 内存 SQLite 真建表，不 mock 内部
逻辑——要验证的正是那两处 `principal is None` 的形状。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.auth import Principal

BLOCKS = {
    "cp_active_intent": "正在核对对家报价",
    "cp_next_action": "约见供货商",
}


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


def _seed_checkpoints(s: Session) -> None:
    from datetime import UTC, datetime, timedelta

    from lantai.models.tables import SessionCheckpoint

    now = datetime.now(UTC)
    # 时间必须错开：`get_latest_checkpoint` 按 created_at desc 挑 newest，
    # 同一时刻会退到 id desc，测试就分不清"挑对了吗"。
    # user-A 最早、user-B 最新（B 的是全库 newest）、NULL 老底本居中。
    for i, (sid, owner, age) in enumerate(
        [
            ("sess-A", "user-A", timedelta(hours=3)),
            ("sess-legacy", None, timedelta(hours=2)),
            ("sess-B", "user-B", timedelta(hours=1)),
        ]
    ):
        for key, content in BLOCKS.items():
            s.add(
                SessionCheckpoint(
                    session_id=sid,
                    block_key=key,
                    content=content,
                    user_id=owner,
                    created_at=now - age,
                )
            )
    s.commit()


def _seed_scratchpads(s: Session) -> None:
    from datetime import UTC, datetime

    from lantai.models.tables import SessionScratchpad

    now = datetime.now(UTC)
    for sid, owner, content in [
        ("sp-A", "user-A", "A 的私有札记"),
        ("sp-B", "user-B", "B 的私有札记"),
        ("sp-default", "default", "default 的札记"),
    ]:
        s.add(
            SessionScratchpad(
                session_id=sid,
                content=content,
                user_id=owner,
                created_at=now,
                updated_at=now,
            )
        )
    s.commit()


class TestCheckpointLatestNoIdentity:
    """`checkpoint_latest` 在宿主不透传身份时不能拿 B 的 newest 底本。"""

    def test_no_identity_does_not_return_others(self, engine):
        """决定性：不透传 user_id → 不返回 user-B 的底本。"""
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_checkpoints(s)

        out = mcp.handle_checkpoint_latest({})
        # 修前：返回 sess-B（全库 newest，B 的）
        assert out is None or out.get("session_id") != "sess-B", (
            f"无身份时拿到了 B 的底本：{out}——那是 Agent 的当前工作现场"
        )

    def test_no_identity_still_sees_null_owner_legacy(self, engine):
        """NULL 属主老底本必须仍可见（真实库 82 行全是 NULL）。"""
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_checkpoints(s)

        out = mcp.handle_checkpoint_latest({})
        assert out is not None, "NULL 属主老底本对无身份调用不可见了——单人部署丢失工作现场"
        assert out.get("session_id") == "sess-legacy", (
            f"该回退到 NULL 属主老底本，实际 {out.get('session_id')}"
        )

    def test_explicit_owner_sees_own(self, engine):
        """透传 user-A → 看得见自己的底本（不能修废）。

        注意 newest 可见的是 NULL 老底本（2h）而非 sess-A（3h）——**这正是
        预期**：NULL 老行对人人可见（真实库 82 行全靠这条），所以 user-A 的
        "最近一次快照"自然包含它。本条钉的是"自己没被挡在门外"。
        """
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_checkpoints(s)

        out = mcp.handle_checkpoint_latest({"user_id": "user-A"})
        assert out is not None and out.get("session_id") == "sess-legacy", (
            f"user-A 该看到自己的 + NULL 老行（newest 是 sess-legacy），实际 {out}"
        )

        # 只有自己的库时， newest 就是自己的
        with Session(engine) as s:
            s.execute(text("delete from session_checkpoint where session_id != 'sess-A'"))
            s.commit()
        out2 = mcp.handle_checkpoint_latest({"user_id": "user-A"})
        assert out2 is not None and out2.get("session_id") == "sess-A", (
            f"user-A 读不到自己的底本：{out2}"
        )

    def test_admin_still_full(self, engine):
        """admin 仍全量（看得见 B 的）。"""
        from lantai.services.checkpoint_service import get_latest_checkpoint

        with Session(engine) as s:
            _seed_checkpoints(s)

        out = get_latest_checkpoint(principal=Principal(role="admin"))
        assert out is not None and out.get("session_id") == "sess-B", f"admin 看不全了：{out}"

    def test_inject_path_still_works_without_principal(self, engine):
        """内部 worker：`inject_checkpoint_context(principal=None)` 不能断。

        04 号票让无身份写落 `default`，所以这条路径下"写 default、读 default"
        正好配对——这正是本票敢收紧的前提。
        """
        from lantai.services.checkpoint_service import (
            inject_checkpoint_context,
            write_session_checkpoint,
        )

        write_session_checkpoint("sess-internal", BLOCKS)  # 无身份 → 落 default

        ctx = inject_checkpoint_context(session_id="sess-internal")
        assert "正在核对对家报价" in ctx, f"内部注入路径断了：{ctx!r}"


class TestScratchpadGetNoIdentity:
    """`scratchpad_get` 在无身份时不能按 id 拿 B 的札记。"""

    def test_no_identity_does_not_return_others(self, engine):
        """决定性：不透传 user_id 拿 B 的 session_id → 空串。"""
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_scratchpads(s)

        out = mcp.handle_scratchpad_get({"session_id": "sp-B"})
        assert out.get("content") == "", f"无身份时拿到了 B 的札记：{out!r}——札记直接进 LLM 提示"

    def test_no_identity_sees_default(self, engine):
        """不透传 user_id 拿 default 的札记 → 正常返回（单人部署不能修废）。"""
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_scratchpads(s)

        out = mcp.handle_scratchpad_get({"session_id": "sp-default"})
        assert out.get("content") == "default 的札记", f"default 的札记读不到了：{out!r}"

    def test_explicit_owner_sees_own(self, engine):
        """透传 user-A → 拿到自己的（不能修废）。"""
        import lantai.cli.mcp as mcp

        with Session(engine) as s:
            _seed_scratchpads(s)

        out = mcp.handle_scratchpad_get({"session_id": "sp-A", "user_id": "user-A"})
        assert out.get("content") == "A 的私有札记", f"user-A 读不到自己的：{out!r}"

    def test_admin_still_full(self, engine):
        """admin 仍能读 B 的札记。"""
        from lantai.services.scratchpad_service import get_scratchpad

        with Session(engine) as s:
            _seed_scratchpads(s)

        assert get_scratchpad("sp-B", principal=Principal(role="admin")) == "B 的私有札记"
