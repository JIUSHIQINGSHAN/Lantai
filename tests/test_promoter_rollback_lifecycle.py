"""promoter.rollback 生命周期字段还原面（票 36 P1 + 票 37 P2）。

根因（票 36）：rollback 把上一 checkpoint 的 `after` 整行快照逐字段
setattr 还原（`id`/`version` 已排除——票 23），但 `status`/`lifecycle_status`/
`superseded_by` 等治理字段仍在回拨范围——而 `retract_memory` 撤回不打
checkpoint、不 bump version，撤回后最新 checkpoint 的 `after.status` 恒为
撤回前的 "active" ⇒ **admin 撤回的记忆可被非 admin 属主借 rollback 复活**，
绕过 admin-only 的 unretract 门（ADR-0047 笔削四分法：主张错误即全检索面
禁用、不可自动复活）。撤回的撤销面有门、旁路面无门。

票 37：还原循环虽已不回拨 `version`（票 23），checkpoint 选取仍只有
`version.desc()` 单键排序——历史 tie 行（撞号）的「上一版」选取无契约。
另：`MemoryCheckpoint` 无 (memory_id, version) 唯一约束，链上 version 列
是否严格递增无回归锁（票 23 行为的守夜锁）。

七臂（36: 主臂/身份/archived/知命 + 无辜绿锁；37: tie 定序/幂等）：
形态与替身边界同 tests/test_promoter_rollback_vector.py——真 SQLite
（StaticPool 内存库）+ 真 FTS5 + 真内嵌 Chroma（临时目录）+ 确定性
3-gram 哈希 embed 替身（只替外部 embedding 服务），retract/archive/
rollback 全走产品入口，不 mock 任何被测函数。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.evolution.promoter import apply_proposal, rollback
from lantai.models.tables import MemoryCheckpoint, MemoryItem, MemoryProposal
from lantai.services.record_ops_service import archive_memory, retract_memory
from lantai.storage.fts import init_fts, search_fts
from lantai.storage.vector_store import get_vector_store

_EMBED_DIM = 512

V1 = "用户常住地在杭州滨江区，骑车上班"
V2 = "用户常住地在苏州工业园区，地铁上班"
V1_ONLY = "杭州滨江区"
V2_ONLY = "苏州工业园区"


def _hash_embed(texts: list[str], timeout: float | None = None) -> list[list[float]]:
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
def rb_env(monkeypatch, tmp_path):
    """真 SQLite + 真 FTS5 + 真内嵌 Chroma；仅 embed 走外部面替身。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(settings, "FTS_BM25_GRAM_TOKENIZE", True)
    monkeypatch.setattr("lantai.evolution.promoter.embed", _hash_embed)
    monkeypatch.setattr("lantai.services.record_ops_service.embed", _hash_embed)
    monkeypatch.setattr("lantai.retrieval.hybrid.embed", _hash_embed)
    yield e


def _prop(engine, proposal_type: str, patch: dict, target: str | None = None) -> str:
    """最小真实提案：PENDING 落库，apply 走产品入口全链路。"""
    with Session(engine) as s:
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type=proposal_type,
            target_memory_id=target,
            proposed_patch=patch,
            confidence=0.9,
            provenance={"prompt": "ticket36-smoke"},
        )
        s.add(prop)
        s.commit()
        return prop.id


def _apply_update_pair(engine, *, key: str = "home_city") -> str:
    """写入记忆 → 应用一次正文变更（全走 apply 路径），返回 memory_id。"""
    add_id = _prop(engine, "add", {"content": V1, "key": key, "memory_type": "semantic"})
    assert apply_proposal(add_id)["ok"] is True
    with Session(engine) as s:
        mem = s.exec(select(MemoryItem).where(MemoryItem.key == key)).one()
    upd_id = _prop(
        engine,
        "update",
        {"content": V2, "key": key, "memory_type": "semantic"},
        target=mem.id,
    )
    assert apply_proposal(upd_id)["ok"] is True
    return mem.id


def _fts_ids(engine, query: str) -> list[str]:
    with engine.connect() as conn:
        return list(search_fts(conn.connection.driver_connection, query))


def _fts_row_count(engine, memory_id: str) -> int:
    """memory_fts 中该 memory_id 的索引行数（撤回即清索引的验收口径）。"""
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        return int(
            raw.execute(
                "SELECT count(*) FROM memory_fts WHERE memory_id = ?", (memory_id,)
            ).fetchone()[0]
        )


def _status_of(engine, memory_id: str) -> str:
    with Session(engine) as s:
        return s.get(MemoryItem, memory_id).status


# ── 票 36 主臂：撤回 → rollback 不得复活 ──


def test_rollback_does_not_resurrect_retracted(rb_env):
    """主臂：add+update 两版 → retract（FTS 清零）→ rollback。

    修复前红：rollback 返回 ok=True、status 回 active、FTS 对正文重新
    召回——撤回被完全复活。
    修复后绿：ok=False/reason=retracted、status 仍 retracted、FTS 零行、
    content 未被改写（守卫先于 CAS，不留半写）。"""
    mid = _apply_update_pair(rb_env)
    assert _fts_row_count(rb_env, mid) == 1
    ret = retract_memory(mid, reason="票36:主张错误", actor="admin")
    assert ret["ok"] is True and ret["fts_removed"] is True
    assert _status_of(rb_env, mid) == "retracted"
    assert _fts_row_count(rb_env, mid) == 0, "撤回必须清索引（前置态）"
    assert _fts_ids(rb_env, V2_ONLY) == []

    res = rollback(mid)

    assert res["ok"] is False and res.get("reason") == "retracted", (
        f"撤回态 rollback 必须拒绝（ADR-0047 不可自动复活）；res={res}"
    )
    assert _status_of(rb_env, mid) == "retracted", (
        f"status 被快照回拨为 {_status_of(rb_env, mid)}——撤回被复活（票 36 病灶）"
    )
    assert _fts_row_count(rb_env, mid) == 0, "复活半程重建了 FTS 索引"
    assert _fts_ids(rb_env, V2_ONLY) == [], "撤回行经 rollback 重新可检索"
    with Session(rb_env) as s:
        mem = s.get(MemoryItem, mid)
        assert mem.content == V2, (
            f"守卫拒绝的 rollback 不得改写 content（不留半写），实得 {mem.content!r}"
        )


# ── 票 36 无辜臂（绿锁）：active 记忆的正常回滚逐字不变 ──


def test_rollback_active_memory_normal_semantics_unchanged(rb_env):
    """无辜臂（绿锁，修复前后皆绿）：active 记忆 rollback → content 回 v1、
    FTS 召回 v1 独有短语、向量库命中 v1、checkpoint 追加 trigger=rollback
    ——防「把 rollback 整体禁掉」的假绿。"""
    mid = _apply_update_pair(rb_env)

    res = rollback(mid)

    assert res["ok"] is True and res["vector_synced"] is True, res
    with Session(rb_env) as s:
        assert s.get(MemoryItem, mid).content == V1
    assert _fts_ids(rb_env, V1_ONLY) == [mid]
    assert _fts_ids(rb_env, V2_ONLY) == []
    assert get_vector_store().has(mid) is True
    hit = get_vector_store().search(_hash_embed([V1])[0], top_k=5)
    assert hit and hit[0]["id"] == mid, f"向量库未按 v1 召回：{hit}"
    with Session(rb_env) as s:
        rb_ckpts = s.exec(
            select(MemoryCheckpoint).where(
                MemoryCheckpoint.memory_id == mid,
                MemoryCheckpoint.trigger == "rollback",
            )
        ).all()
        assert len(rb_ckpts) == 1, (
            f"回滚应追加一笔 trigger=rollback 的 checkpoint；实得 {len(rb_ckpts)}"
        )


# ── 票 36 身份臂：非 admin 属主同样不可复活 ──


def test_rollback_retracted_by_non_admin_owner_rejected(rb_env):
    """身份臂：非 admin 属主对已撤回记忆 rollback → status 不得回 active
    （属主过 ensure_can_delete 后被撤回守卫拒，reason=retracted）。"""
    mid = _apply_update_pair(rb_env)
    with Session(rb_env) as s:
        mem = s.get(MemoryItem, mid)
        mem.user_id = "user-alice"  # 种属主（add 提案默认 NULL 归属）
        s.add(mem)
        s.commit()
    assert retract_memory(mid, reason="票36:admin撤回", actor="admin")["ok"] is True

    owner = Principal(user_id="user-alice", role="user")
    res = rollback(mid, principal=owner)

    assert res["ok"] is False, f"非 admin 属主借 rollback 复活撤回行必须被拒；res={res}"
    assert _status_of(rb_env, mid) == "retracted", (
        f"status={_status_of(rb_env, mid)}——旁路面复活（票 36 病灶：撤销面有门旁路面无门）"
    )


# ── 票 36 archived 臂：生命周期字段不回拨（archive 保持）──


def test_rollback_keeps_archived_status(rb_env):
    """archived 臂：archive（可逆退出常规检索）→ rollback → status 保持
    archived，内容照常回滚（回滚是内容版本回滚不是生命周期倒带）。"""
    mid = _apply_update_pair(rb_env)
    assert archive_memory(mid, actor="admin", reason="票36")["ok"] is True
    assert _status_of(rb_env, mid) == "archived"

    res = rollback(mid)

    assert res["ok"] is True, res
    assert _status_of(rb_env, mid) == "archived", (
        f"status 被快照回拨为 {_status_of(rb_env, mid)}——archived 被倒带成 active（票 36 同族面）"
    )
    with Session(rb_env) as s:
        assert s.get(MemoryItem, mid).content == V1, "archived 行的内容回滚语义不变"


# ── 票 36 知命臂：lifecycle_status / superseded_by 不回拨 ──


def test_rollback_keeps_lifecycle_fields(rb_env):
    """知命臂：lifecycle_status=superseded + superseded_by 种入后 rollback →
    两字段保持当前值（知命状态机单向迁移不被回滚倒转），内容照常回滚。"""
    mid = _apply_update_pair(rb_env)
    with Session(rb_env) as s:
        mem = s.get(MemoryItem, mid)
        mem.lifecycle_status = "superseded"
        mem.superseded_by = "mem_successor"
        s.add(mem)
        s.commit()

    res = rollback(mid)

    assert res["ok"] is True, res
    with Session(rb_env) as s:
        mem = s.get(MemoryItem, mid)
        assert mem.lifecycle_status == "superseded", (
            f"lifecycle_status 被快照回拨为 {mem.lifecycle_status!r}——知命单向迁移被倒转"
        )
        assert mem.superseded_by == "mem_successor", (
            f"superseded_by 被快照回拨为 {mem.superseded_by!r}——继任关系丢失"
        )
        assert mem.content == V1
