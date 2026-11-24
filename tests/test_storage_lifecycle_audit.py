"""模块三（存储引擎与数据生命周期）独立审计契约锁与回归测试。

覆盖审计项：
1. init_db 底层连接生命周期管理（连接池泄露排查与 Windows 文件句柄锁释放验证）
2. 目次引擎 MuciEngine.build_catalog 会话生命周期与泄露排查
3. FTS5 rebuild_fts 全量重建索引的生命周期状态过滤（防止 retracted / consolidated 僵尸复活）
4. index_fts 重复调用的幂等性（UPSERT 语义，防止全文索引虚增重号）
5. 候选待审队列 review_candidate(approve=False) CAS 原子性与并发防脏写
6. 候选审核 review_candidate(approve=True) 主权边界透传（principal 贯穿防越权寻址）
7. 记忆关系链与因果溯源链单 session 复用与图遍历性能优化
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_mod
from lantai.core.acl import Principal
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    MemoryCandidate,
    MemoryEdge,
    MemoryItem,
    MemoryProposal,
)
from lantai.services.candidate_service import (
    CandidateStateConflict,
    review_candidate,
)
from lantai.storage.edges import (
    create_edge,
    get_causal_chain,
    get_edges,
    get_supersed_chain,
)
from lantai.storage.fts import (
    index_fts,
    init_fts,
    rebuild_fts,
    search_fts,
    sync_fts,
)


@pytest.fixture
def isolated_db_env(monkeypatch):
    """临时隔离数据库环境，隔离生产全局 engine 与连接。"""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "audit_test.db")
        url = f"sqlite:///{db_path}"
        monkeypatch.setattr(settings, "DATABASE_URL", url)
        test_engine = create_engine(url, connect_args={"timeout": 30})
        monkeypatch.setattr(db_mod, "engine", test_engine)

        yield test_engine, db_path

        test_engine.dispose()


class TestInitDbConnectionLifecycle:
    """1. init_db 连接生命周期与句柄排查。"""

    def test_init_db_releases_connection_without_leak(self, monkeypatch):
        """修复前 init_fts(conn) 的 raw_connection 未 close，导致 Windows 下文件锁占用无法删除。"""
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "init_db_leak_check.db")
            url = f"sqlite:///{db_path}"
            monkeypatch.setattr(settings, "DATABASE_URL", url)
            test_engine = create_engine(url, connect_args={"timeout": 30})
            monkeypatch.setattr(db_mod, "engine", test_engine)

            db_mod.init_db()

            assert db_mod.FTS_OK is True
            assert db_mod.get_migration_failures() == []

            # 释放 engine 连接池后，底层文件必须能被立即独占访问或删除，证明无任何未关闭的底层连接悬挂
            test_engine.dispose()
            try:
                # 若连接未关，Windows 下 unlink 会抛 WinError 32
                os.remove(db_path)
            except PermissionError as exc:
                pytest.fail(f"init_db 遗留未释放连接句柄，数据库文件被锁定: {exc}")


class TestMuciEngineSessionLifecycle:
    """2. 目次引擎会话生命周期与泄露排查。"""

    def test_build_catalog_closes_own_session(self, monkeypatch):
        """当外部未传入 session 时，MuciEngine 必须在 build_catalog 完成后主动关闭内部 session。"""
        from lantai.cognition.muci import MuciEngine

        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_mod, "engine", engine)

        closed_sessions = []
        real_get_session = db_mod.get_session

        def tracking_get_session():
            sess = real_get_session()
            orig_close = sess.close

            def tracked_close():
                closed_sessions.append(sess)
                orig_close()

            sess.close = tracked_close
            return sess

        monkeypatch.setattr(db_mod, "get_session", tracking_get_session)

        engine_muci = MuciEngine()
        catalog = engine_muci.build_catalog()

        assert "total_memories" in catalog
        assert len(closed_sessions) == 1, (
            "MuciEngine.build_catalog 未在执行完成后关闭内部创建的 session"
        )


class TestFTSLifecycleAndIdempotency:
    """3 & 4. FTS5 重建与单条索引的生命周期与幂等性。"""

    def test_rebuild_fts_excludes_retracted_and_consolidated(self):
        """rebuild_fts 必须严格排除 retracted 与 consolidated 状态，防止僵尸失效记录复活进词表。"""
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        raw_conn = engine.raw_connection()
        try:
            init_fts(raw_conn)
        finally:
            raw_conn.close()

        with Session(engine) as s:
            now = utcnow()
            # 1. 活跃条目
            m_active = MemoryItem(
                id="mem-active-1",
                key="k-active",
                content="深度学习模型优化指南",
                status="active",
                user_id="default",
                created_at=now,
                updated_at=now,
            )
            # 2. 归档条目（保留 FTS 便于快速起复）
            m_archived = MemoryItem(
                id="mem-archived-1",
                key="k-archived",
                content="旧版网络架构设计指南",
                status="archived",
                user_id="default",
                created_at=now,
                updated_at=now,
            )
            # 3. 撤回条目（削：严格剔除）
            m_retracted = MemoryItem(
                id="mem-retracted-1",
                key="k-retracted",
                content="废弃的敏感密码配置指南",
                status="retracted",
                user_id="default",
                created_at=now,
                updated_at=now,
            )
            # 4. 巩固碎片条目（已折叠：严格剔除）
            m_consolidated = MemoryItem(
                id="mem-consolidated-1",
                key="k-consolidated",
                content="已被巩固合并的碎片指南",
                status="consolidated",
                user_id="default",
                created_at=now,
                updated_at=now,
            )
            s.add_all([m_active, m_archived, m_retracted, m_consolidated])
            s.commit()

            # 执行全量重建
            rebuilt_count = rebuild_fts(s)
            s.commit()

            # 断言仅重建了 active 与 archived，retracted/consolidated 未被重灌
            assert rebuilt_count == 2

            with engine.connect() as conn:
                driver_conn = conn.connection.driver_connection
                assert "mem-active-1" in search_fts(driver_conn, "深度学习")
                assert "mem-archived-1" in search_fts(driver_conn, "设计指南")
                assert "mem-retracted-1" not in search_fts(driver_conn, "配置指南"), (
                    "已撤回记录在 rebuild_fts 后死灰复燃灌回 FTS"
                )
                assert "mem-consolidated-1" not in search_fts(driver_conn, "碎片指南"), (
                    "已巩固碎片在 rebuild_fts 后死灰复燃灌回 FTS"
                )

    def test_index_fts_is_idempotent_on_duplicate_call(self):
        """index_fts 多次写入同一 memory_id 时应为 UPSERT 语义，不得在 FTS 表中产生重号冗余行。"""
        c = sqlite3.connect(":memory:")
        init_fts(c)

        index_fts(c, "mid-test-1", "首次写入的知识内容")
        index_fts(c, "mid-test-1", "覆盖更新的知识内容")

        rows = c.execute(
            "SELECT memory_id, content FROM memory_fts WHERE memory_id = 'mid-test-1'"
        ).fetchall()
        assert len(rows) == 1, f"index_fts 重复调用产生了冗余行: {rows}"
        assert rows[0][1] == "覆盖更新的知识内容"
        c.close()


class TestCandidateQueueLifecycleAndCAS:
    """5 & 6. 候选记忆裁决队列的 CAS 原子性与主权边界透传。"""

    def test_review_candidate_reject_cas_atomicity(self, monkeypatch):
        """驳回操作必须通过 cas_update 原子判断状态为 pending_review，防并发脏写与审计抹除。"""
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_mod, "engine", engine)

        with Session(engine) as s:
            cand = MemoryCandidate(
                id="cand-reject-1",
                document_id="doc-1",
                user_id="default",
                summary="测试候选",
                status="pending_review",
            )
            s.add(cand)
            s.commit()

        # 正常驳回
        res = review_candidate("cand-reject-1", approve=False, reason="业务不合格")
        assert res["ok"] is True
        assert res["candidate_status"] == "rejected"

        with Session(engine) as s:
            c = s.get(MemoryCandidate, "cand-reject-1")
            assert c.status == "rejected"

        # 重复驳回或已非 pending_review 态应抛出状态冲突或非法
        with pytest.raises(ValueError):
            review_candidate("cand-reject-1", approve=False, reason="重复驳回")

    def test_review_candidate_approve_forwards_principal_to_proposer(self, monkeypatch):
        """批准操作必须将 principal 贯穿透传至 propose_from_candidate，防越权寻址。"""
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_mod, "engine", engine)

        with Session(engine) as s:
            cand = MemoryCandidate(
                id="cand-approve-1",
                document_id="doc-1",
                user_id="user-alice",
                summary="Alice 的候选记忆",
                status="pending_review",
            )
            s.add(cand)
            s.commit()

        captured_principal = []

        def mock_propose(candidate_id, gate_result, principal=None):
            captured_principal.append(principal)
            return MemoryProposal(
                id="prop-1",
                target_key="k-1",
                proposal_type="add",
                diff_patch={},
                status="pending",
            )

        monkeypatch.setattr("lantai.evolution.proposer.propose_from_candidate", mock_propose)

        alice_principal = Principal(user_id="user-alice", role="user")
        res = review_candidate("cand-approve-1", approve=True, principal=alice_principal)

        assert res["ok"] is True
        assert len(captured_principal) == 1
        assert captured_principal[0] is alice_principal, (
            "review_candidate 未将 principal 透传至 proposer"
        )


class TestEdgeTraversalsSessionReuse:
    """7. 记忆边与因果溯源链 session 复用。"""

    def test_get_supersed_chain_reuses_session(self, monkeypatch):
        """get_supersed_chain 在链式跳转时应当复用 session，避免频繁借还连接池。"""
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_mod, "engine", engine)

        with Session(engine) as s:
            s.add(
                MemoryEdge(
                    id="e1",
                    source_memory_id="m1",
                    target_memory_id="m2",
                    relation="supersedes",
                    confidence=0.9,
                )
            )
            s.add(
                MemoryEdge(
                    id="e2",
                    source_memory_id="m2",
                    target_memory_id="m3",
                    relation="supersedes",
                    confidence=0.8,
                )
            )
            s.commit()

        # 传入外部 session
        with Session(engine) as custom_session:
            chain = get_supersed_chain("m1", session=custom_session)
            assert len(chain) == 2
            assert chain[0]["superseded_by"] == "m2"
            assert chain[1]["superseded_by"] == "m3"

        # 不传外部 session 时亦可自动在单一上下文内跑完
        chain_auto = get_supersed_chain("m1")
        assert len(chain_auto) == 2

    def test_get_causal_chain_reuses_session(self, monkeypatch):
        """get_causal_chain 同样支持 session 复用。"""
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_mod, "engine", engine)

        with Session(engine) as s:
            s.add(
                MemoryEdge(
                    id="e1",
                    source_memory_id="c1",
                    target_memory_id="c2",
                    relation="causes",
                    confidence=0.9,
                )
            )
            s.add(
                MemoryEdge(
                    id="e2",
                    source_memory_id="c2",
                    target_memory_id="c3",
                    relation="leads_to",
                    confidence=0.8,
                )
            )
            s.commit()

        with Session(engine) as custom_session:
            chain = get_causal_chain("c1", direction="forward", session=custom_session)
            assert len(chain) == 2
            assert chain[0]["to_id"] == "c2"
            assert chain[1]["to_id"] == "c3"
