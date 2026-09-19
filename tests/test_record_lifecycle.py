"""笔削四分法测试（ADR-0047）：真 DB + 真 FTS + 真服务函数，不 mock 内部逻辑。

替身边界（纪律允许面）：embed（外部 LLM 面）与向量库（外部存储面）。
覆盖：撤回三面 0 命中（D23 验收口径「撤回后禁用命中=0」）、不可自动复活锚、
归档可逆、纠错保留版本、删除同步如实回报 + 无正文审计、unretract admin 门禁。
"""

import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.core.ids import new_id
from lantai.models.tables import MemoryAuditEvent, MemoryItem
from lantai.retrieval import hybrid
from lantai.services import record_ops_service as ops
from lantai.storage.fts import init_fts, search_fts, sync_fts


@pytest.fixture()
def engine(monkeypatch):
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    yield e


class FakeVectorStore:
    def __init__(self):
        self.ids: set[str] = set()
        self.deleted: list[str] = []
        self.upserted: list[str] = []
        self.last_metadata: dict | None = None

    def add(self, ids, embeddings, metadatas):
        # 形状守卫：embedding 必须是向量（数列表）而非三层嵌套——防 embed 返回值少取 [0] 的回归
        for emb in embeddings:
            assert isinstance(emb, list) and emb and isinstance(emb[0], (int, float)), (
                f"embedding shape broken: {type(emb)} / {type(emb[0]) if emb else 'empty'}"
            )
        # metadata 契约守卫：缺归属键会让重同步后的记忆在属主过滤检索中永久不可见
        for md in metadatas:
            assert {"key", "memory_type", "lane", "domain", "user_id", "tenant_id"} <= set(md), (
                f"metadata missing ownership keys: {sorted(md)}"
            )
        self.upserted.extend(ids)
        self.ids.update(ids)
        self.last_metadata = metadatas[0] if metadatas else None

    def search(self, query_embedding, top_k, filters=None):
        return [{"id": i, "distance": 0.1, "metadata": {}} for i in list(self.ids)[:top_k]]

    def delete(self, ids):
        self.deleted.extend(ids)
        self.ids.difference_update(ids)


@pytest.fixture()
def fake_vs():
    store = FakeVectorStore()
    with patch("lantai.retrieval.hybrid.get_vector_store", return_value=store):
        yield store


def _fake_embed(texts):
    return [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] for _ in texts]


@pytest.fixture()
def fake_embed():
    with patch(
        "lantai.services.record_ops_service.embed", side_effect=_fake_embed
    ), patch("lantai.retrieval.hybrid.embed", side_effect=_fake_embed):
        yield


def _add(engine, content: str, **kw) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=mid,
                content=content,
                lane=kw.get("lane", "general"),
                status=kw.get("status", "active"),
                user_id=kw.get("user_id", "u1"),
                tenant_id=kw.get("tenant_id"),
                decay_score=kw.get("decay_score", 1.0),
                helpful_count=kw.get("helpful_count", 0),
            )
        )
        sync_fts(s, mid, content)  # 同事务（ADR-0008）
        s.commit()
    return mid


def _hybrid_ids(query: str) -> set[str]:
    return {r["memory"]["id"] for r in hybrid.hybrid_search(query, top_k=5, use_rerank=False)}


def _fts_hits(conn, query: str) -> list[str]:
    return list(search_fts(conn.connection.driver_connection, query))


def _principal(user_id="u1", role="user", lanes=("general",)):
    return Principal(user_id=user_id, role=role, allowed_lanes=list(lanes))


@pytest.fixture()
def client(engine):
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


class TestRetract:
    def test_retract_zero_hit_all_three_surfaces(self, engine, fake_vs, fake_embed):
        """D23 验收口径：撤回后禁用命中=0（SQL/FTS/向量三面）。"""
        mid = _add(engine, "用户对花生过敏，严禁推荐花生制品")
        fake_vs.ids.add(mid)

        res = ops.retract_memory(mid, reason="医学错误")
        assert res["ok"] and res["fts_removed"] and res["vector_removed"]

        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "retracted"
        with engine.connect() as conn:
            assert _fts_hits(conn, "花生过敏") == []
        assert mid not in _hybrid_ids("花生过敏")
        assert mid not in fake_vs.ids

    def test_retract_idempotent(self, engine, fake_vs):
        mid = _add(engine, "待撤回记忆一")
        assert ops.retract_memory(mid, reason="x")["ok"]
        again = ops.retract_memory(mid, reason="x")
        assert again["ok"] and again.get("already_retracted")

    def test_retract_not_found(self, engine):
        assert ops.retract_memory("mem_none", reason="x")["ok"] is False

    def test_retract_fts_failure_reported_sql_authoritative(self, engine, fake_vs, fake_embed):
        """FTS 移除失败不静默：响应如实上报，SQL 权威过滤面仍保证 0 命中（宁 miss 不脏写）。"""
        mid = _add(engine, "撤回时FTS故障的记忆")
        fake_vs.ids.add(mid)
        with patch(
            "lantai.services.record_ops_service.sync_fts", side_effect=RuntimeError("fts down")
        ):
            res = ops.retract_memory(mid, reason="x")
        assert res["ok"] and res["fts_removed"] is False and res["warnings"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "retracted"
        with engine.connect() as conn:
            assert _fts_hits(conn, "撤回时FTS故障") == [mid]  # 索引残留如实可见
        assert mid not in _hybrid_ids("撤回时FTS故障")  # SQL 过滤面兜底，仍 0 命中

    def test_retract_vector_failure_reported_sql_authoritative(self, engine, fake_vs, fake_embed):
        """向量删除失败不静默：残留可见但 SQL 权威过滤面仍保证 0 命中（锚 hybrid status 谓词）。"""
        mid = _add(engine, "向量删除失败仍不可召回的记忆")
        fake_vs.ids.add(mid)
        with patch(
            "lantai.retrieval.hybrid.delete_memory_item", side_effect=RuntimeError("vs down")
        ):
            res = ops.retract_memory(mid, reason="x")
        assert res["ok"] and res["vector_removed"] is False and res["warnings"]
        assert mid in fake_vs.ids  # 向量残留如实可见
        assert mid not in _hybrid_ids("向量删除失败仍不可召回")  # SQL 谓词兜底，仍 0 命中

    def test_no_worker_resurrection(self, engine, fake_vs):
        """铁律 6：retracted 不被沉潜/晋升复活。"""
        from lantai.cognition.lifecycle import KnowledgeLifecycleManager, LifecycleTransitionError
        from lantai.services.consolidation_service import prune_decayed_synapses

        mid = _add(engine, "深度衰减的撤回记忆", decay_score=0.01, helpful_count=0)
        assert ops.retract_memory(mid, reason="x")["ok"]

        assert prune_decayed_synapses(threshold=0.05) == 0  # 只选 active，retracted 不入
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "retracted"
            with pytest.raises(LifecycleTransitionError):
                KnowledgeLifecycleManager(s).promote(s.get(MemoryItem, mid))


class TestUnretract:
    def test_route_admin_only(self, engine, fake_vs, fake_embed, client):
        mid = _add(engine, "误撤回待恢复的记忆")
        fake_vs.ids.add(mid)
        assert ops.retract_memory(mid, reason="x")["ok"]

        app.dependency_overrides[get_current_user] = lambda: _principal("u2", role="user")
        assert client.post(f"/terminal/memory/{mid}/unretract").status_code == 403

        app.dependency_overrides[get_current_user] = lambda: _principal("root", role="admin")
        resp = client.post(f"/terminal/memory/{mid}/unretract")
        assert resp.status_code == 200, resp.text
        assert resp.json()["fts_synced"]
        assert mid in _hybrid_ids("误撤回待恢复")

    def test_unretract_embed_failure_warns_fts_still_works(self, engine, fake_vs, client):
        """向量重同步失败如实回报；FTS 已同事务重同步，检索主路径不受阻（宁 miss 不脏写）。"""
        mid = _add(engine, "向量故障但FTS可恢复的记忆")
        assert ops.retract_memory(mid, reason="x")["ok"]

        def _boom(_texts):
            raise RuntimeError("embed down")

        with patch("lantai.services.record_ops_service.embed", side_effect=_boom):
            res = ops.unretract_memory(mid, actor="root")
        assert res["ok"] and res["vector_synced"] is False and res["warnings"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"
        with engine.connect() as conn:
            assert _fts_hits(conn, "向量故障但FTS可恢复") == [mid]

    def test_unretract_non_retracted_conflict(self, engine):
        mid = _add(engine, "未撤回的记忆")
        assert ops.unretract_memory(mid)["ok"] is False


class TestArchive:
    def test_archive_unarchive_roundtrip_reversible(self, engine, fake_vs, fake_embed):
        """归档可逆：检索面 0 命中，FTS/向量行保留（复原零成本证据）。"""
        mid = _add(engine, "归档恢复测试记忆内容")
        fake_vs.ids.add(mid)

        assert ops.archive_memory(mid)["ok"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "archived"
        assert mid not in _hybrid_ids("归档恢复测试")
        with engine.connect() as conn:
            assert _fts_hits(conn, "归档恢复测试") == [mid]  # FTS 行保留
        assert mid in fake_vs.ids  # 向量行保留

        assert ops.unarchive_memory(mid)["ok"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"
        assert mid in _hybrid_ids("归档恢复测试")

    def test_archive_idempotent_and_unarchive_only_from_archived(self, engine):
        mid = _add(engine, "幂等归档记忆")
        assert ops.archive_memory(mid)["ok"]
        assert ops.archive_memory(mid).get("already_archived")
        assert ops.unarchive_memory(mid)["ok"]
        assert ops.unarchive_memory(mid)["ok"] is False  # active 不可再恢复

    def test_archive_retracted_blocked(self, engine):
        mid = _add(engine, "撤回后不可归档降级")
        ops.retract_memory(mid, reason="x")
        res = ops.archive_memory(mid)
        assert res["ok"] is False and "active" in res["error"]


class TestCorrect:
    def test_correct_keeps_version_history(self, engine, fake_vs, fake_embed):
        mid = _add(engine, "用户的生日是3月2日")
        fake_vs.ids.add(mid)

        res = ops.correct_memory(mid, new_content="用户的生日是3月5日", reason="笔误")
        assert res["ok"] and res["version"] == 2
        assert res["vector_synced"] and res["warnings"] == []

        with Session(engine) as s:
            item = s.get(MemoryItem, mid)
            assert item.content == "用户的生日是3月5日"
            assert item.version == 2
            c = item.provenance["corrections"][0]
            assert c["old_content"] == "用户的生日是3月2日"
            assert c["from_version"] == 1 and c["reason"] == "笔误"
        assert mid in fake_vs.upserted  # 向量已重同步
        assert fake_vs.last_metadata["user_id"] == "u1"  # 归属键随重同步保留（review 整改锚）
        assert mid in _hybrid_ids("用户的生日是3月5日")
        with engine.connect() as conn:
            assert _fts_hits(conn, "生日是3月2日") == []  # 旧文不再可命中

    def test_correct_fts_failure_aborts_clean(self, engine, fake_vs, fake_embed):
        """改文类 FTS 失败强一致：随事务回滚，不留「可命中旧文」的脏索引（ADR-0008）。"""
        mid = _add(engine, "纠错FTS故障不落脏索引")
        with patch(
            "lantai.services.record_ops_service.sync_fts", side_effect=RuntimeError("fts down")
        ):
            with pytest.raises(RuntimeError):
                ops.correct_memory(mid, new_content="改文尝试", reason="r")
        with Session(engine) as s:
            item = s.get(MemoryItem, mid)
            assert item.content == "纠错FTS故障不落脏索引"
            assert item.version == 1
            assert not (item.provenance or {}).get("corrections")

    def test_correct_rejects_empty_and_unchanged(self, engine):
        mid = _add(engine, "不可空改与同文改")
        assert ops.correct_memory(mid, new_content="  ")["ok"] is False
        assert ops.correct_memory(mid, new_content="不可空改与同文改")["ok"] is False

    def test_correct_rejected_on_retracted(self, engine):
        mid = _add(engine, "撤回后不可纠错改文")
        ops.retract_memory(mid, reason="x")
        res = ops.correct_memory(mid, new_content="改文尝试")
        assert res["ok"] is False and "retracted" in res["error"]


class TestDelete:
    def test_delete_reports_sync_and_audit_without_content(self, engine, fake_vs, client):
        """删除：三面清净 + 审计留痕但无正文（铁律 4）。"""
        content = "待删除的隐私内容ABC"
        mid = _add(engine, content)
        fake_vs.ids.add(mid)
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")

        resp = client.delete(f"/terminal/memory/{mid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] and body["fts_removed"] and body["vector_removed"]

        with Session(engine) as s:
            assert s.get(MemoryItem, mid) is None
            events = s.exec(
                select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid)
            ).all()
            assert len(events) == 1
            a = events[0]
            assert a.action == "delete"
            assert a.content_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()
            assert a.content_len == len(content)
        assert "content" not in MemoryAuditEvent.model_fields  # 审计模型无正文字段
        with engine.connect() as conn:
            assert _fts_hits(conn, "待删除的隐私内容") == []
        assert mid not in fake_vs.ids

    def test_delete_fts_failure_visible_in_response(self, engine, fake_vs, client):
        """FTS 清理失败必须出现在响应（旧 remove_fts 不存在被 except 静默吞的隐患修复）。"""
        mid = _add(engine, "删除时FTS故障的记忆")
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")
        with patch("lantai.storage.fts.sync_fts", side_effect=RuntimeError("fts down")):
            resp = client.delete(f"/terminal/memory/{mid}")
        body = resp.json()
        assert body["ok"] and body["fts_removed"] is False and body["warnings"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid) is None  # 主语义完成
            assert (
                len(s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid)).all())
                == 1
            )

    def test_all_six_actions_audited(self, engine, fake_vs, fake_embed, client):
        """五服务操作 + 路由删除，审计 action 全枚举覆盖。"""
        mid = _add(engine, "审计全枚举覆盖记忆")
        fake_vs.ids.add(mid)
        assert ops.correct_memory(mid, new_content="审计全枚举覆盖记忆V2", reason="r")["ok"]
        assert ops.archive_memory(mid)["ok"]
        assert ops.unarchive_memory(mid)["ok"]
        assert ops.retract_memory(mid, reason="r")["ok"]
        assert ops.unretract_memory(mid, actor="root")["ok"]
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")
        assert client.delete(f"/terminal/memory/{mid}").status_code == 200

        with Session(engine) as s:
            actions = sorted(
                e.action
                for e in s.exec(
                    select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid)
                ).all()
            )
        assert actions == ["archive", "correct", "delete", "retract", "unarchive", "unretract"]


class TestRoutes:
    def test_retract_route_422_empty_reason(self, engine, client):
        mid = _add(engine, "空由撤回被拒")
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")
        resp = client.post(f"/terminal/memory/{mid}/retract", json={"reason": "   "})
        assert resp.status_code == 422

    def test_retract_route_ownership_enforced(self, engine, client):
        mid = _add(engine, "他人不可撤回我的记忆", user_id="u1")
        app.dependency_overrides[get_current_user] = lambda: _principal("u2")
        resp = client.post(f"/terminal/memory/{mid}/retract", json={"reason": "x"})
        assert resp.status_code == 403
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"  # 资源原样

    def test_correct_route_ownership_enforced(self, engine, client):
        mid = _add(engine, "他人不可纠错我的记忆", user_id="u1")
        app.dependency_overrides[get_current_user] = lambda: _principal("u2")
        resp = client.post(
            f"/terminal/memory/{mid}/correct", json={"content": "越权改文", "reason": "r"}
        )
        assert resp.status_code == 403
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).content == "他人不可纠错我的记忆"  # 资源原样

    def test_archive_route_roundtrip(self, engine, client):
        mid = _add(engine, "路由归档往返记忆")
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")
        assert client.post(f"/terminal/memory/{mid}/archive", json={}).status_code == 200
        assert client.post(f"/terminal/memory/{mid}/unarchive", json={}).status_code == 200
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"

    def test_unarchive_route_on_active_conflict(self, engine, client):
        mid = _add(engine, "活跃记忆不可恢复归档")
        app.dependency_overrides[get_current_user] = lambda: _principal("u1")
        assert client.post(f"/terminal/memory/{mid}/unarchive", json={}).status_code == 409
