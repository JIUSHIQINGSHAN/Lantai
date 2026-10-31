"""R-01 否定子串误判修复（票 `.scratch/v0.64-next-window-2026-11/09`）。

病灶：`probing_service._NEGATIVE_PATTERNS` 含英文裸子串（`no`/`false` 等），
匹配用 `re.search` 无边界——`'noted, 已记录'` 被 `no` 子串误判为否定 →
分支 B 直接 `status="archived"` + `confidence=0.1`。用户随口回一句
「已记录」，记忆就被归档。

修法：否定判定与肯定侧 `_is_affirmative_reply` 同构——**英文否定词整词
匹配（\\b 边界）**；CJK 否定词（不是/不对/没有…）语义不动（CJK 词间无
子串误伤面，「没错」不含「没有」）。三处分支（conflict/memory/fact）同源。

不 mock 冒烟：真实 MemoryItem/ConflictEvent + 真实 SQLite，直调
`resolve_probe_response` 公开接口。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.models.tables import ConflictEvent, MemoryItem


@pytest.fixture
def probe_env():
    """真实 SQLite + FTS（resolve 内部 sync_fts）。"""
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    yield engine
    engine.dispose()


def _seed_mem(engine, mid="mem-r01", content="用户偏好乌龙茶", user_id=None):
    with Session(engine) as s:
        s.add(MemoryItem(id=mid, content=content, status="active", user_id=user_id))
        s.commit()


class TestEnglishNegativeWordsMatchWholeWordOnly:
    """英文否定词整词匹配：子串误伤清零（修复前红）。"""

    def test_noted_reply_does_not_archive_memory(self):
        """核心复现：'noted, 已记录' 不得触发否定分支归档（修复前红：直接 archived）。"""
        from lantai.services.probing_service import resolve_probe_response

        engine = probe_env_fresh()
        _seed_mem(engine, "mem-r01a", "用户偏好乌龙茶")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-r01a",
                    memory_id="mem-r01a",
                    incoming_ref="用户偏好改成龙井",
                    rule_name="x",
                    status="open",
                )
            )
            s.commit()
            res = resolve_probe_response("conf-r01a", "noted, 已记录", session=s)
            # 修复前：neg 子串误判 → action=dismissed（冲突废弃）；此处记忆不该被动
            s.refresh(s.get(MemoryItem, "mem-r01a"))
            m = s.get(MemoryItem, "mem-r01a")
            assert m.status == "active", f"noted 被误判否定，记忆被动: {res}"
            # 无明确表态 → deferred（保持待审），不是 dismissed
            assert res["action"] in ("pending", "deferred"), res

    def test_known_word_not_negative(self):
        """'known' 含 'no' 子串——整词匹配后不再误判。"""
        from lantai.services.probing_service import resolve_probe_response

        engine = probe_env_fresh()
        _seed_mem(engine, "mem-r01b", "服务部署在内网")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-r01b",
                    memory_id="mem-r01b",
                    incoming_ref="服务部署在云端",
                    rule_name="x",
                    status="open",
                )
            )
            s.commit()
            res = resolve_probe_response("conf-r01b", "known issue, 记录一下", session=s)
            m = s.get(MemoryItem, "mem-r01b")
            assert m.status == "active", res
            assert res["action"] in ("pending", "deferred"), res

    def test_bare_no_still_negative(self):
        """整词 'no' 仍是否定（既有语义不回归）。"""
        from lantai.services.probing_service import resolve_probe_response

        engine = probe_env_fresh()
        _seed_mem(engine, "mem-r01c", "端口是 8080")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-r01c",
                    memory_id="mem-r01c",
                    incoming_ref="端口改成 9090",
                    rule_name="x",
                    status="open",
                )
            )
            s.commit()
            res = resolve_probe_response("conf-r01c", "no", session=s)
            assert res["status"] == "resolved" and res["action"] == "dismissed", res

    def test_cjk_negatives_unaffected(self):
        """CJK 否定词（不是/不对/没有/还是）语义不动（既有用例口径）。"""
        from lantai.services.probing_service import resolve_probe_response

        engine = probe_env_fresh()
        _seed_mem(engine, "mem-r01d", "测试环境端口是 8080")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-r01d",
                    memory_id="mem-r01d",
                    incoming_ref="端口变更为 9090",
                    rule_name="x",
                    status="open",
                )
            )
            s.commit()
            res = resolve_probe_response("conf-r01d", "不是的，端口没有变，依然是 8080", session=s)
            assert res["status"] == "resolved" and res["action"] == "dismissed", res

    def test_mem_branch_word_boundary(self):
        """分支 B（probe_mem）：'nothing changed' 整词 no 仍归档、'noted' 不归档。"""
        from lantai.services.probing_service import resolve_probe_response

        engine = probe_env_fresh()
        # 整词否定：归档分支语义保持
        _seed_mem(engine, "mem-r01e", "旧手机号 13800000000")
        res = (
            resolve_probe_response("probe_mem-r01e", "no, 这个早就不用了", session=None)
            if False
            else None
        )
        # 直接走 session 形态（内部 db.get_session 已被我们接管不了——用 engine 会话直调）
        with Session(engine) as s:
            import lantai.services.probing_service as ps

            orig = ps.db.get_session
            ps.db.get_session = lambda: s
            try:
                _seed_mem(engine, "mem-r01f", "旧手机号 13800000000")
                res = resolve_probe_response("probe_mem-r01f", "no, 早就不用了")
                assert res["action"] == "archived", res
                # 子串误伤：'noted' 不归档
                _seed_mem(engine, "mem-r01g", "在用的手机号 13900000000")
                res2 = resolve_probe_response("probe_mem-r01g", "noted, 还在用")
                m = s.get(MemoryItem, "mem-r01g")
                assert m.status == "active", res2
                assert res2["action"] in ("pending", "deferred"), res2
            finally:
                ps.db.get_session = orig


def probe_env_fresh():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return engine
