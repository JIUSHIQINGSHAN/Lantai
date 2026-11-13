"""票 18：X-Tenant-Id / X-Session-Id 头在场时关键词通道塌缩——六处过滤缺 NULL 半边。

**病灶**（`.scratch/v0.68-next-window-2026-11/issues/18-header-scope-null-half-recall-collapse.md`）：
`lantai/core/auth.py:124-126` 把请求头直接读进 `Principal`；FTS/BM25/LIKE 六处
tenant/session 过滤是严格等值（无 `OR IS NULL` 半边），带头调用 → NULL 属主老行
（真实库主体形态）在关键词通道整批消失；向量降级时关键词即全部召回通道 → 整链
塌缩；向量在场时两通道可见集不一致，结果随 embed 服务健康度漂移。

**正确侧口径钉点**：终闸 `_query_items`（`hybrid.py:665-668`）tenant 是
`== OR IS NULL`、admin/system 全豁免、**从不过滤 session**；
`fts.py:75-78` 注释自证「`OR IS NULL` 半边不可删」。

形态镜像立案探针 `.scratch/_v068_probe_r4_header_null_recall.py`：
真内存库 + 真 `hybrid_search` 全链（embed 抛错走真 `_keyword_fallback`；
向量在场对照臂替身 VS 只断外部网络）；`search_fts` / `search_fts_bm25` /
`_keyword_fallback` 直调各一组。修复前本文件红（亲证 13F/2P，两绿为
fixture 真值锁与隔离现状锁），修复后全绿。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, search_fts, search_fts_bm25, sync_fts

# AND 面（search_fts）查询词：两 gram 在 mem_a/mem_b/mem_d/mem_f 正文全命中
Q_AND = "的生日是"
# OR 面（bm25 / hybrid 全链）查询词
Q_OR = "生日是什么时候"
# LIKE 臂：短 token（"显卡" 2 字符 → has_short_tokens 必真 → LIKE 必参与）
Q_LIKE = "3080 显卡"

NOW = utcnow()

# fixture 行：session/tenant 维度四态（NULL/NULL、有头、跨租户、跨会话）
ROWS = {
    "mem_a": dict(content="用户的生日是三月八号", session_id=None, tenant_id=None),
    "mem_b": dict(content="同事的生日是四月一号", session_id="sess-abc", tenant_id="tenant-x"),
    "mem_c": dict(content="3080 显卡驱动崩溃", session_id=None, tenant_id=None),
    "mem_d": dict(content="分部同事的生日是五月初", session_id=None, tenant_id="tenant-other"),
    "mem_f": dict(content="远端同事的生日是六月一号", session_id="sess-other", tenant_id=None),
}


def _principal(user_id="default", *, tenant_id=None, session_id=None, role="user"):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=None,
        session_id=session_id,
        role=role,
        allowed_lanes=None,
    )


@pytest.fixture
def engine():
    """真内存 SQLite + 真 FTS5 trigram 表（不 mock 检索逻辑）。"""
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed(engine, mid: str) -> None:
    spec = ROWS[mid]
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"key-{mid}",
                content=spec["content"],
                namespace="default",
                status="active",
                tier="working",
                importance=0.5,
                confidence=1.0,
                reason="",
                role="OBSERVATION",
                lane="general",
                domain="general",
                version=1,
                use_count=0,
                helpful_count=0,
                decay_score=1.0,
                decay_class="slow",
                event_time_precision="none",
                lifecycle_status="ACTIVE",
                session_id=spec["session_id"],
                tenant_id=spec["tenant_id"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        sync_fts(s, mid, spec["content"])
        s.commit()


def _seed_all(engine) -> None:
    for mid in ROWS:
        _seed(engine, mid)


def _ids(rows) -> list[str]:
    """`search_fts` 返回 str 列表，`search_fts_bm25` 返回 (id, score) 元组列表。"""
    return [r[0] if isinstance(r, tuple) else r for r in rows]


# ── 假绿防线：fixture 的 scope 落库值必须真为四态 ────────────────


class TestFixtureRowsActuallyScoped:
    def test_fixture_null_and_scoped_values_landed(self, engine):
        """防 fixture 自己带值导致空转锁：直接读表核 NULL/有值各就各位。"""
        _seed_all(engine)
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            got = {
                r[0]: (r[1], r[2])
                for r in raw.execute("SELECT id, session_id, tenant_id FROM memoryitem").fetchall()
            }
        assert got["mem_a"] == (None, None), f"mem_a 落库不是双 NULL：{got['mem_a']}"
        assert got["mem_b"] == ("sess-abc", "tenant-x"), f"mem_b 落库 scope 漂了：{got['mem_b']}"
        assert got["mem_c"] == (None, None), f"mem_c 落库不是双 NULL：{got['mem_c']}"
        assert got["mem_d"] == (None, "tenant-other"), f"mem_d 落库 tenant 漂了：{got['mem_d']}"
        assert got["mem_f"] == ("sess-other", None), f"mem_f 落库 session 漂了：{got['mem_f']}"


# ── SQL 级直调：search_fts / search_fts_bm25 ────────────────────


class TestSearchFtsNullScopeVisibleWithHeaders:
    def test_null_rows_visible_with_session_header(self, engine):
        """带 session 头：NULL session 老行必须可见（修前被 `m.session_id = ?` 滤掉）。"""
        _seed_all(engine)
        with engine.connect() as conn:
            bare = set(
                _ids(search_fts(conn.connection.driver_connection, Q_AND, top_k=50, principal=None))
            )
            got = set(
                _ids(
                    search_fts(
                        conn.connection.driver_connection,
                        Q_AND,
                        top_k=50,
                        principal=_principal(session_id="sess-abc"),
                    )
                )
            )
        assert {"mem_a", "mem_b"} <= bare, f"无头对照臂异常（fixture 失效）：{bare}"
        assert {"mem_a", "mem_b"} <= got, (
            f"带 session 头后 NULL 老行 mem_a 消失（关键词通道半召回塌缩）：{got}"
        )

    def test_null_rows_visible_with_tenant_header(self, engine):
        """带租户头：NULL tenant 老行必须可见（`OR IS NULL` 半边，终闸 :665-668 同口径）。"""
        _seed_all(engine)
        with engine.connect() as conn:
            got = set(
                _ids(
                    search_fts(
                        conn.connection.driver_connection,
                        Q_AND,
                        top_k=50,
                        principal=_principal(tenant_id="tenant-x"),
                    )
                )
            )
        assert {"mem_a", "mem_b"} <= got, (
            f"带租户头后 NULL 老行 mem_a 消失（关键词通道半召回塌缩）：{got}"
        )

    def test_null_rows_visible_with_tenant_header_bm25(self, engine):
        """BM25 通道同病同修（两函数是同一次手写的同一段）。"""
        _seed_all(engine)
        with engine.connect() as conn:
            got = set(
                _ids(
                    search_fts_bm25(
                        conn.connection.driver_connection,
                        Q_OR,
                        top_k=50,
                        principal=_principal(tenant_id="tenant-x"),
                    )
                )
            )
        assert {"mem_a", "mem_b"} <= got, f"BM25 带租户头后 NULL 老行消失：{got}"
        assert "mem_d" not in got, f"跨租户隔离被修穿（bm25 臂 tenant-other 行可见）：{got}"

    def test_cross_tenant_nonnull_row_still_blocked(self, engine):
        """隔离现状锁：跨租户**非 NULL** 行仍被阻断——修 NULL 半边不得把隔离修穿
        （镜像 test_tongpao_federation_storage_smoke.py:120-124 断言方向）。"""
        _seed_all(engine)
        with engine.connect() as conn:
            bare = set(
                _ids(search_fts(conn.connection.driver_connection, Q_AND, top_k=50, principal=None))
            )
            got = set(
                _ids(
                    search_fts(
                        conn.connection.driver_connection,
                        Q_AND,
                        top_k=50,
                        principal=_principal(tenant_id="tenant-x"),
                    )
                )
            )
        assert "mem_d" in bare, f"无头对照臂应可见跨租户行（fixture 失效）：{bare}"
        assert "mem_d" not in got, f"跨租户隔离被修穿（tenant-other 行对 tenant-x 可见）：{got}"
        assert "mem_b" in got, f"同租户行不应被误伤：{got}"

    def test_session_narrowing_removed_aligns_final_gate(self, engine):
        """session 收窄是孤儿语义：终闸 `_query_items` 从不过滤 session、向量通道
        同样不过——FTS 的收窄与其相反必有一侧错。修后带头不再收窄 session 维
        （修前 mem_f 被 `m.session_id = ?` 滤掉）。"""
        _seed_all(engine)
        with engine.connect() as conn:
            got = set(
                _ids(
                    search_fts(
                        conn.connection.driver_connection,
                        Q_AND,
                        top_k=50,
                        principal=_principal(session_id="sess-abc"),
                    )
                )
            )
        assert "mem_f" in got, f"session 头仍在收窄（跨会话行与终闸口径相左）：{got}"


# ── LIKE 兜底直调：_keyword_fallback ────────────────────────────


def _run_keyword_fallback(engine, query: str, principal):
    """直跑 `_keyword_fallback`（不 mock 其内部计算逻辑）；显式传 session 避免绑宿主库。"""
    from lantai.retrieval.hybrid import RetrievalParams, _keyword_fallback

    with Session(engine) as s:
        return _keyword_fallback(
            query,
            top_k=50,
            fetch_n=50,
            memory_types=None,
            lanes=None,
            trace=False,
            trace_steps=[],
            t0=0.0,
            params=RetrievalParams(),
            session=s,
            principal=principal,
        )


class TestLikeFallbackTenantHeaderNoCollapse:
    def test_like_tenant_header_null_rows_not_collapsed(self, engine):
        """LIKE 是关键词召回最后一层；带租户头时严格 `tenant_id == ?` 让 NULL 行
        在这层单独塌空（探针实测整臂返回 []）。"""
        _seed_all(engine)
        res = _run_keyword_fallback(engine, Q_LIKE, _principal(tenant_id="tenant-x"))
        docs = [r["document"] for r in res]

        assert any("3080 显卡" in d for d in docs), (
            f"LIKE 臂带租户头整行塌空（返回 {docs}）——NULL 行连最后一层兜底都出不去"
        )


# ── hybrid_search 全链（降级臂 / 向量在场对照臂）────────────────


def _patch_degraded_chain(monkeypatch, engine):
    """镜像探针：真 hybrid_search + 真 _keyword_fallback，只断外部网络面。"""
    import lantai.retrieval.hybrid as hybrid_mod
    import lantai.retrieval.intent as intent_mod
    import lantai.storage.db as db_module

    def _boom(*a, **k):
        raise RuntimeError("embed unavailable（向量降级臂）")

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(hybrid_mod, "embed", _boom)
    monkeypatch.setattr(intent_mod, "chat_json", lambda *a, **k: {"candidate_n": 5, "lanes": []})
    monkeypatch.setattr(hybrid_mod, "rerank", lambda *a, **k: [])


def _patch_vector_chain(monkeypatch, engine, visible_ids: list[str]):
    """向量在场对照臂：替身 VS 返回 visible_ids（_query_items 可见全集）。"""
    import lantai.retrieval.hybrid as hybrid_mod
    import lantai.retrieval.intent as intent_mod
    import lantai.storage.db as db_module
    import lantai.storage.vector_store as vector_store_module

    class VS:
        def search(self, *a, **k):
            return [{"id": i, "distance": 0.1} for i in visible_ids]

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(vector_store_module, "get_vector_store", lambda: VS())
    monkeypatch.setattr(hybrid_mod, "get_vector_store", lambda: VS())
    monkeypatch.setattr(hybrid_mod, "embed", lambda texts, timeout=None: [[0.1] * 8 for _ in texts])
    monkeypatch.setattr(intent_mod, "chat_json", lambda *a, **k: {"candidate_n": 5, "lanes": []})
    monkeypatch.setattr(hybrid_mod, "rerank", lambda *a, **k: [])


def _hybrid_ids(query: str, principal) -> set[str]:
    from lantai.retrieval import hybrid as hybrid_mod

    out = hybrid_mod.hybrid_search(query, top_k=50, principal=principal, use_rerank=False)
    return {r.get("memory", {}).get("id") for r in out}


class TestHybridFullChainHeaderScope:
    """全链降级臂：embed 抛错 → 真 `_keyword_fallback`——离线部署的真实形态。"""

    def test_degraded_full_chain_tenant_header_keeps_null_rows(self, engine, monkeypatch):
        _seed_all(engine)
        _patch_degraded_chain(monkeypatch, engine)
        got = _hybrid_ids(Q_OR, _principal(tenant_id="tenant-x"))

        assert {"mem_a", "mem_b"} <= got, (
            f"降级全链带租户头：NULL 老行 mem_a 消失（整链塌缩，仅剩 {got}）"
        )
        assert "mem_d" not in got, f"跨租户隔离被修穿：{got}"

    def test_degraded_full_chain_session_header_keeps_null_rows(self, engine, monkeypatch):
        _seed_all(engine)
        _patch_degraded_chain(monkeypatch, engine)
        got = _hybrid_ids(Q_OR, _principal(session_id="sess-abc"))

        assert {"mem_a", "mem_b"} <= got, (
            f"降级全链带 session 头：NULL 老行 mem_a 消失（仅剩 {got}）"
        )

    def test_full_chain_like_arm_tenant_header_no_collapse(self, engine, monkeypatch):
        _seed_all(engine)
        _patch_degraded_chain(monkeypatch, engine)
        got = _hybrid_ids(Q_LIKE, _principal(tenant_id="tenant-x"))

        assert "mem_c" in got, f"全链 LIKE 臂带租户头整行塌空（探针实测返回 []）：{got}"


class TestChannelAgreementVectorVsDegraded:
    """通道一致锁：同头同问句，向量在场与向量降级两臂召回集一致。

    修前塌缩的形态就是通道打架：向量通道（终闸口径正确）召回 A，
    关键词通道丢弃 A——结果集随 embed 服务健康度漂移。
    """

    def test_channel_agreement_birthday_query(self, engine, monkeypatch):
        _seed_all(engine)
        p = _principal(tenant_id="tenant-x")

        _patch_vector_chain(monkeypatch, engine, ["mem_a", "mem_b"])
        vector_arm = _hybrid_ids(Q_OR, p)
        # 对照臂自证：替身 VS 投喂的就是终闸可见全集，向量臂必须全收
        assert vector_arm >= {"mem_a", "mem_b"}, f"向量对照臂异常：{vector_arm}"

        _patch_degraded_chain(monkeypatch, engine)
        degraded_arm = _hybrid_ids(Q_OR, p)

        assert degraded_arm >= {"mem_a", "mem_b"}, (
            f"通道打架：向量臂可见 {sorted(vector_arm)}，降级臂只剩 {sorted(degraded_arm)}"
        )
        # 集等值：两臂必须一个宽——superset 不足以钉「一致」（降级臂多召回
        # 一行同样是通道打架，只是方向相反）。真值锚由上面的 inclusion 断言
        # 承担（防两臂同时空转的假绿）。
        assert degraded_arm == vector_arm, (
            f"通道打架（集不等）：向量臂 {sorted(vector_arm)} vs 降级臂 {sorted(degraded_arm)}"
        )

    def test_channel_agreement_like_query(self, engine, monkeypatch):
        _seed_all(engine)
        p = _principal(tenant_id="tenant-x")

        _patch_vector_chain(monkeypatch, engine, ["mem_c"])
        vector_arm = _hybrid_ids(Q_LIKE, p)
        assert "mem_c" in vector_arm, f"向量对照臂异常（mem_c 应过终闸）：{vector_arm}"

        _patch_degraded_chain(monkeypatch, engine)
        degraded_arm = _hybrid_ids(Q_LIKE, p)

        assert "mem_c" in degraded_arm, (
            f"通道打架（LIKE 面）：向量臂 {sorted(vector_arm)}，降级臂 {sorted(degraded_arm)}"
        )
        assert degraded_arm == vector_arm, (
            f"通道打架（LIKE 集不等）：向量臂 {sorted(vector_arm)} vs 降级臂 {sorted(degraded_arm)}"
        )


class TestAdminExemptionAlignedWithFinalGate:
    """admin 豁免锁：带头的管理员在关键词通道必须与终闸同宽（修前比向量通道窄）。"""

    def test_admin_with_tenant_header_same_width_as_bare(self, engine, monkeypatch):
        _seed_all(engine)
        _patch_degraded_chain(monkeypatch, engine)

        bare = _hybrid_ids(Q_OR, _principal("admin", role="admin"))
        with_header = _hybrid_ids(Q_OR, _principal("admin", role="admin", tenant_id="tenant-x"))

        # 同宽锁：带头不带头，管理员关键词通道一个宽（修前带头臂只剩 tenant-x 行）
        assert with_header == bare, (
            f"带头管理员关键词通道比终闸窄：无头 {sorted(bare)} vs 带头 {sorted(with_header)}"
        )
        assert {"mem_a", "mem_d"} <= with_header, (
            f"管理员带头后 NULL 老行/他租户行消失（终闸对 admin 全豁免）：{sorted(with_header)}"
        )

    def test_admin_with_tenant_header_like_channel_not_collapsed(self, engine, monkeypatch):
        _seed_all(engine)
        _patch_degraded_chain(monkeypatch, engine)
        got = _hybrid_ids(Q_LIKE, _principal("admin", role="admin", tenant_id="tenant-x"))

        assert "mem_c" in got, f"管理员带头 LIKE 臂塌空（终闸 admin 全豁免）：{got}"


class TestSequentialHeaderScopeNoPollution:
    """连续请求 / 混合 scope 边界：换头连查不得污染后续结果（现状锁）。"""

    def test_sequential_headers_no_cross_pollution(self, engine):
        _seed_all(engine)
        conn_cm = engine.connect()
        with conn_cm as conn:
            raw = conn.connection.driver_connection
            first_bare = set(_ids(search_fts(raw, Q_AND, top_k=50, principal=None)))
            with_sess = set(
                _ids(search_fts(raw, Q_AND, top_k=50, principal=_principal(session_id="sess-abc")))
            )
            with_tenant = set(
                _ids(search_fts(raw, Q_AND, top_k=50, principal=_principal(tenant_id="tenant-x")))
            )
            last_bare = set(_ids(search_fts(raw, Q_AND, top_k=50, principal=None)))

        assert last_bare == first_bare, f"连查后无头结果被污染：{last_bare} != {first_bare}"
        assert "mem_a" in with_sess, f"session 头臂 NULL 行消失：{with_sess}"
        assert "mem_a" in with_tenant, f"租户头臂 NULL 行消失：{with_tenant}"
