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
from lantai.models.tables import MemoryAuditEvent, MemoryEdge, MemoryItem
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
    with (
        patch("lantai.services.record_ops_service.embed", side_effect=_fake_embed),
        patch("lantai.retrieval.hybrid.embed", side_effect=_fake_embed),
    ):
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
            events = s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid)).all()
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


def _seed_consolidated_cluster(engine, *, n: int = 3) -> tuple[str, list[str]]:
    """构造真实巩固产物：1 主记忆 + n 碎片（consolidated）+ n 条 supersedes 边。

    形状与 promoter.apply_proposal consolidation 分支落库一致（ADR-0050 决策 3）：
    主记忆 active + source_ids 非空，碎片 consolidated，边带 confidence。碎片先入 FTS
    再清索引（同折叠段 sync_fts(s, src.id, None)），起复补回索引的断言才有意义。
    """
    master_id = new_id("mem")
    frag_ids = [new_id("mem") for _ in range(n)]
    master_content = "大哥长期偏好饮用浙江新昌明前大佛龙井茶，冲泡水温偏好85度"
    frag_contents = [
        "大哥今天早上泡了明前大佛龙井茶",
        "大哥喜欢喝大佛龙井茶，水温要求85度",
        "大哥日常饮品偏好为浙江新昌大佛龙井茶",
    ]
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=master_id,
                memory_type="semantic",
                key=master_id,
                content=master_content,
                lane="preference",
                domain="user",
                source_ids=list(frag_ids),
                decay_score=1.0,
                decay_class="semantic",
                status="active",
                user_id="u1",
            )
        )
        for fid, text in zip(frag_ids, frag_contents):
            s.add(
                MemoryItem(
                    id=fid,
                    memory_type="semantic",
                    key=fid,
                    content=text,
                    lane="preference",
                    domain="user",
                    decay_score=0.85,
                    status="consolidated",
                    user_id="u1",
                )
            )
            sync_fts(s, fid, text)
        for fid in frag_ids:
            s.add(
                MemoryEdge(
                    id=new_id("edge"),
                    source_memory_id=master_id,
                    target_memory_id=fid,
                    relation="supersedes",
                    confidence=0.95,
                )
            )
        sync_fts(s, master_id, master_content)
        s.commit()
    # 折叠产物现状：碎片索引已清（同 promoter 折叠段 delete_memory_item）
    with Session(engine) as s:
        for fid in frag_ids:
            sync_fts(s, fid, None)
        s.commit()
    return master_id, frag_ids


class TestReviveConsolidated:
    """起复（ADR-0052）：巩固撤销面——碎片起复 / 主记忆撤销全簇。

    真 DB + 真 FTS + 真实服务函数，不 mock 内部逻辑（替身边界仅 embed 与向量库）。
    """

    def test_fragment_revive_restores_active_and_searchable(self, engine, fake_vs, fake_embed):
        """碎片起复：consolidated→active + FTS/向量重同步后可召回（验收口径 ②）。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        for fid in frag_ids:  # 折叠后向量索引已清（同 promoter 折叠段 delete_memory_item）
            fake_vs.ids.discard(fid)

        res = ops.revive_consolidated(frag, reason="误折叠，恢复单条碎片")
        assert res["ok"] and res["scope"] == "fragment"

        with Session(engine) as s:
            item = s.get(MemoryItem, frag)
            assert item.status == "active"
            assert item.version == 2  # before=1 → 起复 +1
            # 其余两条碎片仍 consolidated（单碎片起复不动全簇）
            assert s.get(MemoryItem, frag_ids[1]).status == "consolidated"
            assert s.get(MemoryItem, frag_ids[2]).status == "consolidated"
        # FTS：碎片索引补回；主记忆索引仍在（碎片起复不动主记忆，如实）
        with engine.connect() as conn:
            assert set(_fts_hits(conn, "明前大佛龙井茶")) == {frag, master_id}
        assert frag in _hybrid_ids("明前大佛龙井茶")  # SQL active 谓词 + 索引双通
        assert frag in fake_vs.ids  # 向量重同步

    def test_fragment_revive_audit_event_without_content(self, engine, fake_vs, fake_embed):
        """起复留审计（action=revive，hash+长度，无正文）。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        assert ops.revive_consolidated(frag, reason="r", actor="root")["ok"]
        with Session(engine) as s:
            events = s.exec(
                select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == frag)
            ).all()
            assert [e.action for e in events] == ["revive"]
            assert events[0].actor == "root"
            assert events[0].content_len > 0
            assert events[0].content_hash

    def test_fragment_revive_checkpoint_written(self, engine, fake_vs, fake_embed):
        """起复落 checkpoint（trigger=revive，before=consolidated，after=active）。"""
        from lantai.models.tables import MemoryCheckpoint

        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        assert ops.revive_consolidated(frag, reason="r")["ok"]
        with Session(engine) as s:
            ckpts = s.exec(select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == frag)).all()
            assert len(ckpts) == 1
            assert ckpts[0].trigger == "revive"
            assert ckpts[0].before["status"] == "consolidated"
            assert ckpts[0].after["status"] == "active"

    def test_master_unconsolidate_full_cluster(self, engine, fake_vs, fake_embed):
        """撤销全簇（验收口径 ①②③）：主记忆三面 0 命中 + 碎片全 active + 边清零。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        fake_vs.ids.add(master_id)
        assert master_id in _hybrid_ids("大佛龙井茶")  # 前置：主记忆可召回

        res = ops.revive_consolidated(master_id, reason="提纯丢失关键细节，整簇撤销")
        assert res["ok"] and res["scope"] == "cluster"
        assert res["fragments_revived"] == len(frag_ids)
        assert res["edges_removed"] == len(frag_ids)

        with Session(engine) as s:
            assert s.get(MemoryItem, master_id).status == "retracted"
            for fid in frag_ids:
                assert s.get(MemoryItem, fid).status == "active"
            edges = s.exec(
                select(MemoryEdge).where(
                    MemoryEdge.source_memory_id == master_id,
                    MemoryEdge.relation == "supersedes",
                )
            ).all()
            assert edges == []  # 边清零，双活重复召回路径封死
        with engine.connect() as conn:
            assert master_id not in _fts_hits(conn, "大佛龙井茶")  # 主记忆 FTS 0 命中
            assert set(_fts_hits(conn, "大佛龙井茶")) == set(frag_ids)  # 碎片索引全补回
        assert master_id not in _hybrid_ids("大佛龙井茶")  # SQL/FTS/向量三面一致
        for fid in frag_ids:
            assert fid in _hybrid_ids("大佛龙井茶")  # 碎片三面可召回
        assert master_id not in fake_vs.ids

    def test_master_unconsolidate_audits_both_sides(self, engine, fake_vs, fake_embed):
        """撤销全簇审计双面：主记忆 unconsolidate + 每碎片 revive。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        assert ops.revive_consolidated(master_id, reason="整簇撤")["ok"]
        with Session(engine) as s:
            master_events = s.exec(
                select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == master_id)
            ).all()
            assert [e.action for e in master_events] == ["unconsolidate"]
            for fid in frag_ids:
                evs = s.exec(
                    select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == fid)
                ).all()
                assert [e.action for e in evs] == ["revive"]

    def test_master_unconsolidate_idempotent(self, engine, fake_vs, fake_embed):
        """幂等：已撤销簇重复调用 already_revoked，不重复落审计、不动碎片。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        assert ops.revive_consolidated(master_id, reason="整簇撤")["ok"]
        again = ops.revive_consolidated(master_id, reason="整簇撤")
        assert again["ok"] and again.get("already_revoked")
        with Session(engine) as s:
            assert (
                len(
                    s.exec(
                        select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == master_id)
                    ).all()
                )
                == 1
            )  # 不重复落 audit
            for fid in frag_ids:
                assert s.get(MemoryItem, fid).status == "active"  # 碎片不被再动

    def test_fragment_revive_idempotent(self, engine, fake_vs, fake_embed):
        """幂等：已 active 碎片重复起复 already_active。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        assert ops.revive_consolidated(frag, reason="r")["ok"]
        again = ops.revive_consolidated(frag, reason="r")
        assert again["ok"] and again.get("already_active")

    def test_plain_active_memory_rejected(self, engine, fake_vs, fake_embed):
        """非巩固目标一律拒绝（宁 miss 不脏写）：普通 active 记忆走本函数不猜意图。"""
        mid = _add(engine, "普通活跃记忆，不属巩固产物")
        res = ops.revive_consolidated(mid, reason="误操作")
        assert res["ok"] is False
        assert "invalid target" in res["error"]
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"  # 资源原样

    def test_retracted_master_completes_revoke(self, engine, fake_vs, fake_embed):
        """普通撤回过的主记忆再走本函数＝补完撤销：碎片恢复 + 边清零，如实标注。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        assert ops.retract_memory(master_id, reason="先普通撤回")["ok"]
        res = ops.revive_consolidated(master_id, reason="再想起复")
        assert res["ok"] and res["scope"] == "cluster"
        assert res["fragments_revived"] == len(frag_ids)
        assert any("already retracted" in w for w in res["warnings"])
        with Session(engine) as s:
            assert s.get(MemoryItem, master_id).status == "retracted"  # 不复活主记忆
            for fid in frag_ids:
                assert s.get(MemoryItem, fid).status == "active"
            assert (
                s.exec(
                    select(MemoryEdge).where(
                        MemoryEdge.source_memory_id == master_id,
                        MemoryEdge.relation == "supersedes",
                    )
                ).all()
                == []
            )

    def test_revived_cluster_blocks_double_master_on_reapply(self, engine, fake_vs, fake_embed):
        """撤销后同提案再 apply 被提案状态机拒（ADR-0052 验收 5）——不产生第二个主记忆。"""
        from lantai.evolution.promoter import apply_proposal
        from lantai.models.tables import MemoryProposal

        master_id, frag_ids = _seed_consolidated_cluster(engine)
        prop_id = new_id("prop")
        with Session(engine) as s:
            s.add(
                MemoryProposal(
                    id=prop_id,
                    proposal_type="consolidation",
                    target_memory_id=master_id,
                    evidence_ids=list(frag_ids),
                    confidence=0.95,
                    status="applied",
                    decided_by="consolidation",
                    proposed_patch={
                        "content": "大哥长期偏好饮用浙江新昌明前大佛龙井茶",
                        "source_ids": list(frag_ids),
                    },
                )
            )
            s.commit()

        assert ops.revive_consolidated(master_id, reason="整簇撤")["ok"]
        assert apply_proposal(prop_id)["ok"] is False  # 已 apply 提案不得二次 apply

        with Session(engine) as s:
            masters = s.exec(select(MemoryItem).where(MemoryItem.source_ids != [])).all()
            assert len(masters) == 1 and masters[0].id == master_id

    def test_master_unconsolidate_fts_failure_reported_sql_authoritative(
        self, engine, fake_vs, fake_embed
    ):
        """FTS 清理失败不静默：如实进 warnings，SQL 权威过滤面仍保证 0 命中。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        with patch("lantai.services.record_ops_service.sync_fts", side_effect=RuntimeError("down")):
            res = ops.revive_consolidated(master_id, reason="整簇撤")
        assert res["ok"] and res["fts_removed"] is False and res["warnings"]
        assert master_id not in _hybrid_ids("大佛龙井茶")  # SQL 谓词兜底，仍 0 命中
        with Session(engine) as s:
            assert s.get(MemoryItem, master_id).status == "retracted"

    def test_fragment_revive_vector_failure_reported(self, engine, fake_vs, fake_embed):
        """向量重同步失败不静默：warnings 如实，SQL/FTS 仍保证碎片可召回。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        with patch(
            "lantai.retrieval.hybrid.index_memory_item", side_effect=RuntimeError("vs down")
        ):
            res = ops.revive_consolidated(frag, reason="r")
        assert res["ok"] and res["warnings"]
        assert frag in _hybrid_ids("大佛龙井茶")  # FTS 命中 + SQL active 兜底

    def test_fragment_revive_fts_failure_reported(self, engine, fake_vs, fake_embed):
        """FTS 重同步失败不静默：索引残留状态如实可见，SQL 谓词保证检索语义不脏。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[1]
        with patch("lantai.services.record_ops_service.sync_fts", side_effect=RuntimeError("down")):
            res = ops.revive_consolidated(frag, reason="r")
        assert res["ok"] and res["warnings"]
        with Session(engine) as s:
            assert s.get(MemoryItem, frag).status == "active"  # 主语义完成
        with engine.connect() as conn:
            assert _fts_hits(conn, "水温要求85度") == []  # 折叠态索引仍清空（残留如实可见）


class TestReviveRoutes:
    """起复 REST 出口（ADR-0052 / 票 02）：路由层归属校验 + 409 映射 + 422 留痕强制。"""

    def test_route_fragment_revive(self, engine, fake_vs, fake_embed, client):
        """碎片起复 200：状态恢复 + 响应带 scope=fragment。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        frag = frag_ids[0]
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        resp = client.post(
            f"/terminal/memory/{frag}/revive-consolidated", json={"reason": "误折叠，恢复单条"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["scope"] == "fragment"
        with Session(engine) as s:
            assert s.get(MemoryItem, frag).status == "active"

    def test_route_master_revokes_full_cluster(self, engine, fake_vs, fake_embed, client):
        """主记忆撤销 200：主记忆 retracted + 碎片全 active + 边清零。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        resp = client.post(
            f"/terminal/memory/{master_id}/revive-consolidated",
            json={"reason": "提纯丢失关键细节，整簇撤销"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scope"] == "cluster" and body["fragments_revived"] == len(frag_ids)
        with Session(engine) as s:
            assert s.get(MemoryItem, master_id).status == "retracted"
            for fid in frag_ids:
                assert s.get(MemoryItem, fid).status == "active"
            assert (
                s.exec(
                    select(MemoryEdge).where(
                        MemoryEdge.source_memory_id == master_id,
                        MemoryEdge.relation == "supersedes",
                    )
                ).all()
                == []
            )

    def test_route_non_consolidation_target_409(self, engine, fake_vs, client):
        """非巩固目标 409（宁 miss 不脏写），资源原样。"""
        mid = _add(engine, "普通活跃记忆，不属巩固产物")
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        resp = client.post(
            f"/terminal/memory/{mid}/revive-consolidated", json={"reason": "误操作"}
        )
        assert resp.status_code == 409
        with Session(engine) as s:
            assert s.get(MemoryItem, mid).status == "active"

    def test_route_empty_reason_422(self, engine, fake_vs, client):
        """reason 空 → 422（同 retract 口径：撤销/恢复均须留痕）。"""
        _, frag_ids = _seed_consolidated_cluster(engine)
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        resp = client.post(
            f"/terminal/memory/{frag_ids[0]}/revive-consolidated", json={"reason": "   "}
        )
        assert resp.status_code == 422
        with Session(engine) as s:
            assert s.get(MemoryItem, frag_ids[0]).status == "consolidated"  # 资源原样

    def test_route_ownership_enforced(self, engine, fake_vs, client):
        """归属校验：他人不可起复我的记忆（403），资源原样。"""
        master_id, frag_ids = _seed_consolidated_cluster(engine)
        app.dependency_overrides[get_current_user] = lambda: _principal("u2", lanes=("general", "preference"))
        resp = client.post(
            f"/terminal/memory/{master_id}/revive-consolidated", json={"reason": "越权撤销"}
        )
        assert resp.status_code == 403
        with Session(engine) as s:
            assert s.get(MemoryItem, master_id).status == "active"
            assert s.get(MemoryItem, frag_ids[0]).status == "consolidated"

    def test_route_not_found_404(self, engine, fake_vs, client):
        """不存在的记忆 404。"""
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        resp = client.post(
            "/terminal/memory/mem_none/revive-consolidated", json={"reason": "x"}
        )
        assert resp.status_code == 404

    def test_route_idempotent(self, engine, fake_vs, fake_embed, client):
        """路由层幂等：重复撤销 200 + already_revoked。"""
        master_id, _ = _seed_consolidated_cluster(engine)
        app.dependency_overrides[get_current_user] = lambda: _principal("u1", lanes=("general", "preference"))
        first = client.post(
            f"/terminal/memory/{master_id}/revive-consolidated", json={"reason": "整簇撤"}
        )
        assert first.status_code == 200
        again = client.post(
            f"/terminal/memory/{master_id}/revive-consolidated", json={"reason": "整簇撤"}
        )
        assert again.status_code == 200 and again.json().get("already_revoked")
