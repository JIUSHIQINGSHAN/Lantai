"""笔削四分法确定性端到端测试（ADR-0047，D23 验收口径「撤回后禁用命中=0」）。

与 tests/test_record_lifecycle.py 的差别：检索三面全部走**真实实现**——
真 SQLite（StaticPool 内存库）+ 真 FTS5 trigram 索引（memory_fts）+
真内嵌 ChromaDB（PersistentClient 指向临时目录，本库无网络依赖）+
真 service 函数（record_ops_service）与真路由（/terminal/memory/*）。

替身边界（测试纪律允许面）只有 embed：外部 LLM embedding 服务用确定性
字符 3-gram 哈希向量替身——共享 gram 越多余弦越近，查询命中其正文、
不命中无关文本，使向量面的命中/清除断言具备区分度，而非同值向量下的
平凡命中。sha256 哈希跨进程/跨平台稳定，断言确定性不依赖 PYTHONHASHSEED。
reranker 不打桩：直接以产品参数 use_rerank=False 关闭；意图分类走产品
自带的确定性关档 LANTAI_INTENT_OFF=1。

五例对应四分语义 + 门禁：
1. test_retract_deterministic_zero_hit_all_three_surfaces —— 撤回三面 0 命中（验收口径本体）
2. test_correct_version_provenance_retention              —— 纠错就地改文 + 版本链留存 + 旧词 FTS 不可命中
3. test_archive_reversible_and_search_filter              —— 归档可逆 + 常规检索不返回 archived（索引行保留）
4. test_delete_privacy_wipe_with_audit_log                —— 删除三面彻底擦净 + 无正文审计
5. test_unretract_admin_guard                             —— unretract 仅 admin（路由层门禁）
"""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.tables import MemoryAuditEvent, MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.services import record_ops_service as ops
from lantai.storage.fts import init_fts, search_fts, sync_fts
from lantai.storage.vector_store import get_vector_store

_EMBED_DIM = 512


def _hash_embed(texts: list[str]) -> list[list[float]]:
    """确定性 3-gram 哈希嵌入（外部 embedding 服务的测试替身，纪律允许面）。"""
    vecs = []
    for t in texts:
        v = [0.0] * _EMBED_DIM
        t = (t or "").strip()
        grams = [t[i : i + 3] for i in range(len(t) - 2)] if len(t) >= 3 else ([t] if t else [])
        for g in grams:
            idx = int.from_bytes(hashlib.sha256(g.encode("utf-8")).digest()[:4], "big") % _EMBED_DIM
            v[idx] += 1.0
        vecs.append(v)
    return vecs


@pytest.fixture()
def bixiao_env(monkeypatch, tmp_path):
    """真 SQLite + 真 FTS5 + 真内嵌 Chroma；仅 embed 走外部面替身。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    # 意图分类确定性关档（产品自带开关），候选集固定 fact_lookup=10
    monkeypatch.setenv("LANTAI_INTENT_OFF", "1")
    # 真实内嵌 Chroma 指向临时目录；重置模块级单例（撕卸自动还原原值）
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    # 钉住影响断言语义的档位：.env 部署配置不得渗入确定性用例
    monkeypatch.setattr(settings, "FTS_BM25_GRAM_TOKENIZE", True)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    # embed 替身只挂在两个外部面引用点：hybrid 查询侧 / record_ops 索引侧
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", _hash_embed)
    monkeypatch.setattr("lantai.services.record_ops_service.embed", _hash_embed)
    yield e


@pytest.fixture()
def client(bixiao_env):
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _seed(engine, content: str, *, user_id: str = "u1") -> str:
    """最小真实数据：真 MemoryItem 行 + 真同事务 FTS 索引 + 真向量库登记。

    全部走已实现的存储/服务接口：sync_fts（ADR-0008 同事务）与
    record_ops_service.sync_vector_upsert（8 键 metadata 契约入口）。
    """
    mid = new_id("mem")
    with Session(engine) as s:
        item = MemoryItem(
            id=mid,
            memory_type="semantic",
            key=mid,
            content=content,
            lane="general",
            user_id=user_id,
            status="active",
            decay_score=1.0,
        )
        s.add(item)
        sync_fts(s, mid, content)
        s.commit()
        assert ops.sync_vector_upsert(mid, item), "vector upsert must succeed"
    return mid


def _hybrid_ids(query: str, top_k: int = 8) -> set[str]:
    return {r["memory"]["id"] for r in hybrid_search(query, top_k=top_k, use_rerank=False)}


def _fts_ids(engine, query: str) -> list[str]:
    with engine.connect() as conn:
        return list(search_fts(conn.connection.driver_connection, query))


def _vector_ids(query: str) -> set[str]:
    """向量面直查（真 Chroma，不过 hybrid 距离筛）：验证 id 在/不在索引本体。"""
    qv = _hash_embed([query])[0]
    return {r["id"] for r in get_vector_store().search(qv, top_k=50)}


def _principal(user_id: str = "u1", role: str = "user") -> Principal:
    return Principal(user_id=user_id, role=role, allowed_lanes=["general"])


# ── 1. D23 验收口径本体：撤回后禁用命中=0 ────────────────────────────────


def test_retract_deterministic_zero_hit_all_three_surfaces(bixiao_env):
    """撤回后 hybrid_search / memory_fts / 向量库三面均 0 命中。

    另种一例共享「花生过」gram 的旁证记忆：撤回后它仍可命中——证明
    0 命中来自逐条撤回本身，而非整库失明或索引损坏。
    """
    mid = _seed(bixiao_env, "用户对花生过敏，严禁推荐花生制品")
    bystander = _seed(bixiao_env, "花生过期了不能吃")
    query = "花生过敏"

    # 活跃时三面均能命中
    assert mid in _hybrid_ids(query)
    assert _fts_ids(bixiao_env, query) == [mid]  # AND 语义：两 gram 全占者唯一
    assert mid in _vector_ids(query)

    # 撤回：同步结果如实回报（成功路径零警告）
    res = ops.retract_memory(mid, reason="医学结论已废止", actor="u1")
    assert res["ok"] is True
    assert res["fts_removed"] is True
    assert res["vector_removed"] is True
    assert res["warnings"] == []

    # 行保留供审计：SQL 行仍在、status=retracted、正文未动
    with Session(bixiao_env) as s:
        row = s.get(MemoryItem, mid)
        assert row.status == "retracted"
        assert row.content == "用户对花生过敏，严禁推荐花生制品"

    # 撤回后三面均 0 命中
    assert mid not in _hybrid_ids(query)  # hybrid（向量+BM25+FTS 并集 + status 谓词）
    assert _fts_ids(bixiao_env, query) == []  # memory_fts 面：字面 0 行
    assert mid not in _vector_ids(query)  # Chroma：id 已从索引本体删除
    # 旁证仍命中：禁用是逐条的，不是整库失明
    assert bystander in _hybrid_ids(query)


# ── 2. 纠错：就地改文 + 版本链留存 + 旧词 FTS 不可命中 ───────────────────


def test_correct_version_provenance_retention(bixiao_env):
    """纠错（笔）：provenance.corrections 完整历史链，旧词 FTS 搜不到。"""
    v1 = "兰台项目的发布窗口定在3月2日"
    v2 = "兰台项目的发布窗口定在3月5日"
    v3 = "兰台项目的发布窗口定在3月8日"
    mid = _seed(bixiao_env, v1)

    r1 = ops.correct_memory(mid, new_content=v2, reason="口误更正", actor="op1")
    assert r1["ok"] is True and r1["version"] == 2
    assert r1["vector_synced"] is True and r1["warnings"] == []
    r2 = ops.correct_memory(mid, new_content=v3, reason="再次顺延", actor="op2")
    assert r2["ok"] is True and r2["version"] == 3
    assert r2["vector_synced"] is True

    with Session(bixiao_env) as s:
        item = s.get(MemoryItem, mid)
        assert item.content == v3  # 就地更新，同一行同一 id
        assert item.version == 3
        chain = item.provenance["corrections"]
        assert [c["old_content"] for c in chain] == [v1, v2]  # 完整历史版本链
        assert [c["from_version"] for c in chain] == [1, 2]
        assert [c["actor"] for c in chain] == ["op1", "op2"]
        assert [c["reason"] for c in chain] == ["口误更正", "再次顺延"]
        assert all(c["at"] for c in chain)
        # 审计同步留存两笔纠错，旧文以 hash 留档（不含明文正文）
        corr_events = sorted(
            (e for e in s.exec(select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid))),
            key=lambda e: e.version_at,
        )
        assert [e.action for e in corr_events] == ["correct", "correct"]
        assert [e.version_at for e in corr_events] == [1, 2]
        assert corr_events[0].content_hash == hashlib.sha256(v1.encode("utf-8")).hexdigest()

    # FTS 同事务强一致（ADR-0047 约束 6）：旧词 v1/v2 均不可命中，现行词可命中
    assert _fts_ids(bixiao_env, "3月2日") == []
    assert _fts_ids(bixiao_env, "3月5日") == []
    assert _fts_ids(bixiao_env, "3月8日") == [mid]
    assert mid in _hybrid_ids("兰台项目的发布窗口定在3月8日")


# ── 3. 归档：可逆 + 常规检索过滤（索引行保留） ───────────────────────────


def test_archive_reversible_and_search_filter(bixiao_env):
    """归档（藏）：unarchive 可逆恢复；常规 hybrid 不返回 archived——
    而 FTS/向量行保留（复原零成本证据），过滤靠 SQL status 谓词。"""
    mid = _seed(bixiao_env, "团队站会固定在工作日早晨九点半")
    query = "站会固定在工作日"
    assert mid in _hybrid_ids(query)

    assert ops.archive_memory(mid, reason="阶段性挂起")["ok"]
    with Session(bixiao_env) as s:
        assert s.get(MemoryItem, mid).status == "archived"
    assert mid not in _hybrid_ids(query)  # 常规检索面退出
    assert _fts_ids(bixiao_env, query) == [mid]  # FTS 行保留
    assert mid in _vector_ids(query)  # Chroma 行保留

    assert ops.unarchive_memory(mid)["ok"]
    with Session(bixiao_env) as s:
        assert s.get(MemoryItem, mid).status == "active"
    assert mid in _hybrid_ids(query)  # 可逆：恢复后照常命中


# ── 4. 删除：隐私擦净 + 无正文审计 ──────────────────────────────────────


def test_delete_privacy_wipe_with_audit_log(bixiao_env, client):
    """删除：SQLite 源行 / memory_fts 物理行 / Chroma 索引三面彻底擦净；
    MemoryAuditEvent 留存 hash+长度，正文永不落审计表。"""
    secret = "机密备忘：王总的私人电话是13900001111"
    mid = _seed(bixiao_env, secret)
    assert mid in _vector_ids("私人电话是13900001111")
    assert _fts_ids(bixiao_env, "13900001111") == [mid]

    app.dependency_overrides[get_current_user] = lambda: _principal("u1")
    resp = client.delete(f"/terminal/memory/{mid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["fts_removed"] is True
    assert body["vector_removed"] is True
    assert body["warnings"] == []

    with Session(bixiao_env) as s:
        assert s.get(MemoryItem, mid) is None  # SQLite 源行已删
        remaining = s.execute(
            text("SELECT COUNT(*) FROM memory_fts WHERE memory_id = :id"), {"id": mid}
        ).scalar()
        assert remaining == 0  # memory_fts 物理行已擦
        events = s.exec(
            select(MemoryAuditEvent).where(MemoryAuditEvent.memory_id == mid)
        ).all()
        assert len(events) == 1
        a = events[0]
        assert a.action == "delete"
        assert a.actor == "u1"
        assert a.content_hash == hashlib.sha256(secret.encode("utf-8")).hexdigest()
        assert a.content_len == len(secret)
        # 审计模型无正文字段；落库行扫不出隐私串
        assert "content" not in MemoryAuditEvent.model_fields
        assert secret not in str(a.model_dump(mode="json"))

    assert _fts_ids(bixiao_env, "13900001111") == []  # FTS 面 0 命中
    assert mid not in _vector_ids("私人电话是13900001111")  # 向量面已删


# ── 5. unretract 门禁：普通权限不可解撤回 ────────────────────────────────


def test_unretract_admin_guard(bixiao_env, client):
    """unretract 仅 admin：普通 user 403（状态不动、检索面仍禁用），
    admin 显式放行后全索引重同步、检索恢复。提权面即 auth.Principal.is_admin
    （role ∈ {admin, system}，lantai/core/auth.py），无旁路。"""
    mid = _seed(bixiao_env, "对外口径：版本号尚未最终确定")
    assert ops.retract_memory(mid, reason="口径作废")["ok"]
    query = "版本号尚未最终确定"
    assert mid not in _hybrid_ids(query)

    # 普通 user：403，资源原样，检索面仍禁用
    app.dependency_overrides[get_current_user] = lambda: _principal("u2", role="user")
    resp = client.post(f"/terminal/memory/{mid}/unretract")
    assert resp.status_code == 403
    with Session(bixiao_env) as s:
        assert s.get(MemoryItem, mid).status == "retracted"
    assert mid not in _hybrid_ids(query)

    # admin：放行，FTS/向量重同步，检索恢复
    app.dependency_overrides[get_current_user] = lambda: _principal("root", role="admin")
    resp = client.post(f"/terminal/memory/{mid}/unretract")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["fts_synced"] is True
    assert body["vector_synced"] is True
    with Session(bixiao_env) as s:
        assert s.get(MemoryItem, mid).status == "active"
    assert mid in _hybrid_ids(query)
    assert mid in _vector_ids(query)
