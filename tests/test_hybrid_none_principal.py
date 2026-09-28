"""票 `.scratch/mcp-identity-gaps/11` 的 **None 语义那一半**：
`hybrid_search` 在 `principal=None` 时三条召回通道 + 最终合并步全部不过滤。

**先说影响**：`hybrid_search` 是整个系统的主检索路径（MCP `search` 与
`verbatim_search` 都走它）。`principal=None` 时：

- 向量通道：`vector_owner_filter(None, ...)` → `None`（**不过滤**）
- FTS 通道（`storage/fts.py:154`/:209）：`if principal:` → 一个条件都不加
- LIKE 兜底（`retrieval/hybrid.py:890`）：`if principal:` → 同上
- **最终合并步 `_query_items`（`hybrid.py:536`）**：压根没有归属条件

决定性实证（`.scratch/mcp-identity-gaps/probe_hybrid_none_semantics.py`）：

```
S1 hybrid_search('报价', None)    → 召回 B 的私有记忆   ❌ 洞
S2 hybrid_search('报价', user-A)  → 搜不到 B            ✅ 护栏
S3 hybrid_search('排期', None)    → 召回 A 的私有记忆   ❌ 洞
S4 hybrid_search('老行', None)    → NULL 老行仍召回     ✅ 单人部署
S5 hybrid_search('default', None) → default 自己的仍召回 ✅ 单人部署
S6 hybrid_search('报价', admin)   → 见 B                ✅ 护栏
```

**修法（入口收敛一次，不动三处通道）**：在 `hybrid_search` 入口把
`principal=None` 收敛成 `Principal(user_id="default")`（经
`acl.viewer_of` 单一真源），admin 原样通过。收敛后三处 `if principal:`
恒真、自动生效——**不需要改那三处**，且将来新增通道也自动被覆盖。

**为什么不动 `vector_owner_filter` 的 None 分支**：它的 docstring 明写
"admin / principal=None → None（不过滤，worker/CLI 不能空转）"，
那是 15 号票**刻意**写的通用契约。判据同 10 号票：
**不动承重墙，在调用方收敛**——worker 直调它仍全表，MCP 路径被收窄。

**不 mock 冒烟**：真内存 SQLite + 真 FTS5 trigram + 真 `MemoryItem` 行；
向量通道 monkeypatch 成抛错以孤立验证 SQL 通道（否则向量召回会把结果
补回来，掩盖 SQL 侧的漏——fts-null-owner/01 号票踩过这个）。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search

MARK_B = "ZZBBBZZ B 的私有记忆：对家报价底牌 88 万"
MARK_A = "ZZAAAAZZ A 自己的记忆：本周排期"
MARK_LEGACY = "ZZLEGZZ 老行的记忆（NULL 属主）"
MARK_DEFAULT = "ZZDEFZZ default 自己的记忆"


class _FakeVectorStore:
    """复刻 Chroma `$or` 相等语义的假向量库（**不 mock 检索逻辑**）。

    只替换"外部存储"这一层——filter 判定、`_query_items` 的归属过滤、
    `hybrid_search` 的合并逻辑全是真代码在跑。这与测试纪律一致：
    mock 只允许用于外部网络/存储依赖，不允许让被测函数跳过内部计算。

    `rows` 由调用方直接塞 `(id, metadata)`，用来构造**向量 metadata 与
    DB 属主不一致**的真实坏态（见 `test_query_items_blocks_stale_vector_owner`）。
    """

    def __init__(self, rows=()):
        self.rows = list(rows)

    def add(self, ids, embeddings, metadatas):
        for i, m in zip(ids, metadatas):
            self.rows.append((i, m))

    def search(self, qv, top_k=10, filters=None):
        out = []
        for mid, meta in self.rows:
            if self._match(filters, meta):
                out.append({"id": mid, "distance": 0.1, "metadata": meta})
        return out[:top_k]

    @staticmethod
    def _match(filters, meta) -> bool:
        if not filters:
            return True
        if "$or" in filters:
            return any(all(meta.get(k) == v for k, v in sub.items()) for sub in filters["$or"])
        return all(meta.get(k) == v for k, v in filters.items())


@pytest.fixture
def engine():
    """真内存 SQLite + 真 FTS5 trigram（不 mock 检索逻辑）。"""
    from lantai.storage.fts import init_fts

    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


@pytest.fixture
def no_vector(monkeypatch):
    """关掉向量通道，孤立验证 SQL 通道（FTS + LIKE）。"""
    import lantai.retrieval.hybrid as hybrid_mod

    def _boom():
        raise RuntimeError("probe: vector channel disabled on purpose")

    monkeypatch.setattr(hybrid_mod, "get_vector_store", _boom)


def _seed(engine):
    now = utcnow()
    with Session(engine) as s:
        for mid, owner, content in [
            ("m-B", "user-B", MARK_B),
            ("m-A", "user-A", MARK_A),
            ("m-legacy", None, MARK_LEGACY),
            ("m-def", "default", MARK_DEFAULT),
        ]:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
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
                    user_id=owner,
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
        from lantai.storage.fts import sync_fts

        for mid, body in [
            ("m-B", MARK_B),
            ("m-A", MARK_A),
            ("m-legacy", MARK_LEGACY),
            ("m-def", MARK_DEFAULT),
        ]:
            sync_fts(s, mid, body)
        s.commit()


def _principal(uid=None, *, role="user"):
    return Principal(
        tenant_id=None, user_id=uid, agent_id=None, session_id=None, role=role, allowed_lanes=None
    )


def _sysv():
    """显式系统身份，**`role="user"` 而不是 `"system"`**——这不是随手写的。

    `Principal.is_admin` 是 `role in ("admin", "system")`（`acl.py`），所以
    `role="system"` 的身份**天然就是 admin**，走的是 admin 分支而不是
    SYSTEM_VIEWER 分支。本类要钉的正是 SYSTEM_VIEWER 那条分支，
    用 `role="system"` 会** vacuous 通过**——变异探针实证过：把
    `vector_owner_filter` 的 SYSTEM_VIEWER 分支删掉，用
    `role="system"` 的测试照样全绿（它走 admin 分支），
    换成 `role="user"` 才立刻红。

    生产形状也是 `role="user"`：eval 三处传的是
    `Principal(user_id=SYSTEM_VIEWER)`，`role` 取默认值 `"user"`。
    """
    return Principal(
        tenant_id=None,
        user_id=SYSTEM_VIEWER,
        agent_id=None,
        session_id=None,
        role="user",
        allowed_lanes=None,
    )


def _run(engine, q, who):
    """把 engine 指到被测临时库后直调 hybrid_search。"""
    import lantai.storage.db as db_module

    orig = db_module.engine
    db_module.engine = engine
    try:
        return hybrid_search(q, top_k=10, use_rerank=False, principal=who)
    finally:
        db_module.engine = orig


def _contents(r) -> list[str]:
    if isinstance(r, tuple):
        r = r[0]
    out = []
    for it in r or []:
        if isinstance(it, dict):
            m = it.get("memory")
            out.append(
                (m.get("content") or "") if isinstance(m, dict) else (it.get("content") or "")
            )
    return out


class TestHybridNonePrincipal:
    """`principal=None` 不再等于"三条通道一层都不过滤"。"""

    def test_none_principal_does_not_recall_others(self, engine, no_vector):
        """无身份检索 → 召回不到 B / A 的私有记忆（修好后才成立）。"""
        _seed(engine)
        for q, mark, who in [("报价", MARK_B, None), ("排期", MARK_A, None)]:
            cs = _contents(_run(engine, q, who))
            assert not any(mark in c for c in cs), (
                f"无身份检索 {q!r} 召回了别人的私有记忆：{[c[:40] for c in cs]}"
            )

    def test_none_principal_still_recalls_legacy_and_default(self, engine, no_vector):
        """单人部署不空转：NULL 老行与 default 自己的仍可召回。"""
        _seed(engine)
        for q, mark in [("老行", MARK_LEGACY), ("default", MARK_DEFAULT)]:
            cs = _contents(_run(engine, q, None))
            assert any(mark in c for c in cs), (
                f"单人部署空转：无身份检索 {q!r} 召回不到自己的数据（{[c[:40] for c in cs]}）"
            )

    def test_explicit_user_still_narrowed(self, engine, no_vector):
        """回归护栏：显式 user-A 的行为不变（搜不到 B）。"""
        _seed(engine)
        cs = _contents(_run(engine, "报价底牌", _principal("user-A")))
        assert not any(MARK_B in c for c in cs), f"A 搜到了 B 的私有记忆：{[c[:40] for c in cs]}"

    def test_explicit_user_still_recalls_own_and_legacy(self, engine, no_vector):
        """回归护栏：显式 user-A 仍能搜到自己的 + NULL 老行。"""
        _seed(engine)
        cs = _contents(_run(engine, "排期", _principal("user-A")))
        assert any(MARK_A in c for c in cs), f"A 搜不到自己的：{[c[:40] for c in cs]}"
        cs2 = _contents(_run(engine, "老行", _principal("user-A")))
        assert any(MARK_LEGACY in c for c in cs2), f"A 搜不到 NULL 老行：{[c[:40] for c in cs2]}"

    def test_admin_principal_still_unfiltered(self, engine, no_vector):
        """admin 仍全权——运维排障路径不能瞎（同 01a/07/09/10 口径）。"""
        _seed(engine)
        cs = _contents(_run(engine, "报价底牌", _principal(None, role="admin")))
        assert any(MARK_B in c for c in cs), f"admin 该见到 B 的：{[c[:40] for c in cs]}"

    def test_explicit_session_query_still_works(self, engine, no_vector):
        """护栏：显式传 `session=` 的既有调用路径不能被打断。

        `_query_items` 补了归属过滤之后，这条守着"直调 + 显式 session"
        的老形状——它们是内部工具路径，不该因为收窄而空转。
        """
        _seed(engine)
        import lantai.storage.db as db_module

        orig = db_module.engine
        db_module.engine = engine
        try:
            with Session(engine) as s:
                r = hybrid_search(
                    "报价底牌",
                    top_k=10,
                    use_rerank=False,
                    session=s,
                    principal=_principal("user-A"),
                )
        finally:
            db_module.engine = orig
        cs = _contents(r)
        assert not any(MARK_B in c for c in cs), (
            f"显式 session 路径漏了收窄：{[c[:40] for c in cs]}"
        )

    def test_query_items_blocks_stale_vector_owner(self, engine, monkeypatch):
        """**差集实证**：向量 metadata 空串 + DB 属主非空 → 只有 `_query_items` 拦得住。

        变异探针里 M2（删掉 `_query_items` 的归属过滤）**存活**——三通道
        各自都过滤了，看起来它是纯冗余的纵深防御。但差集真实存在
        （`.scratch/mcp-identity-gaps/probe_m2_diffset.py` 实证）：

        - `vector_owner_filter` 对普通用户返回
          `{"$or":[{"user_id":viewer},{"user_id":""}]}` —— **空串对所有人生效**
          （票 15 的契约：NULL 属主在 Chroma 里落成空串，单人部署不能空转）；
        - `_query_items` 过滤 `MemoryItem.user_id == viewer OR IS NULL`
          —— **只认 NULL，不认空串**。

        于是一条 DB 里 `user_id="user-B"`、向量 metadata 却是 `user_id=""`
        的记忆（`index_memory_item` 的 metadata 由调用方手搓，eval / 脚本 /
        测试都这么干过；**以及 391 行 verbatim 回填之后 DB 属主被改写、
        向量 metadata 仍是空串的既成事实**），对 user-A 来说：
        向量通道放行 → **`_query_items` 拦下**。

        删掉 `_query_items` 这道，本条立刻红——M2 从存活变被杀。
        """
        import lantai.retrieval.hybrid as hybrid_mod

        _seed(engine)
        # 只替外部存储层；filter 判定与合并逻辑仍是真代码
        monkeypatch.setattr(
            hybrid_mod,
            "get_vector_store",
            lambda: _FakeVectorStore([("m-B", {"memory_id": "m-B", "user_id": ""})]),
        )
        monkeypatch.setattr(hybrid_mod, "embed", lambda q: [[0.1, 0.2]])

        cs = _contents(_run(engine, "报价底牌", _principal("user-A")))
        assert not any(MARK_B in c for c in cs), (
            f"向量 metadata 陈旧导致越权召回：{[c[:40] for c in cs]}"
        )


class TestSystemViewerChannels:
    """显式系统身份（`acl.SYSTEM_VIEWER`）在**每一条通道**都必须是全表。

    **为什么单独立一个类**：`SYSTEM_VIEWER` 的全表口径是**逐函数手写**的。
    11 号票修入口收敛时发现它只在 SQL 侧六个 service 里生效，
    向量通道与 FTS 两处都没有——于是"显式系统身份"被当成普通用户
    `"__system__"` 过滤，**全量批处理被误滤成空集**（探针
    `.scratch/mcp-identity-gaps/probe_sysviewer_channels.py` 四通道实证）。

    没有这个类，把 `vector_owner_filter` / `fts.py` 的 SYSTEM_VIEWER 分支
    删掉，只有 eval 与 genglou 会红——而它们红的原因看起来跟别的回归
    一模一样，很容易被误判。这三条把"删分支必须立刻红"钉死。
    """

    def test_system_viewer_recalls_others_via_hybrid(self, engine, no_vector):
        """`SYSTEM_VIEWER` 走完整 `hybrid_search` 仍能召回别人的私有记忆。"""
        _seed(engine)
        who = _sysv()
        cs = _contents(_run(engine, "报价底牌", who))
        assert any(MARK_B in c for c in cs), (
            f"系统身份被收窄了（全表口径没生效）：{[c[:40] for c in cs]}"
        )

    def test_vector_owner_filter_skips_system_viewer(self):
        """向量通道：`vector_owner_filter(SYSTEM_VIEWER)` 必须返回 None（不过滤）。

        这条钉的是 `acl.py` 的那一行——删掉
        `or viewer_of(principal) == SYSTEM_VIEWER` 它立刻红。
        """
        from lantai.core.acl import vector_owner_filter

        got = vector_owner_filter(_sysv(), None)
        assert got is None, f"向量通道把系统身份当普通用户过滤了：{got}"
        # 顺带钉 admin 与普通用户没被这次改动带坏
        assert vector_owner_filter(_principal(None, role="admin"), None) is None
        assert vector_owner_filter(_principal("user-A"), None) == {
            "$or": [{"user_id": "user-A"}, {"user_id": ""}]
        }

    def test_fts_channels_skip_system_viewer(self, engine):
        """FTS 两条通道：`search_fts` / `search_fts_bm25` 对系统身份不加 user 条件。

        两条是**同一处手写的同一段**（`fts.py` 的 docstring 自己这么写），
        改一处漏一处等于没改，故两条都钉。
        """
        from lantai.storage.fts import search_fts, search_fts_bm25

        _seed(engine)
        who = _sysv()
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            hits = search_fts(raw, "报价底牌", principal=who)
            bm = [r[0] for r in search_fts_bm25(raw, "报价底牌", principal=who)]
        assert "m-B" in hits, f"FTS 通道把系统身份滤空了：{hits}"
        assert "m-B" in bm, f"BM25 通道把系统身份滤空了：{bm}"
        # 反向护栏：普通用户仍然滤得到（不放宽收敛）
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            assert "m-B" not in search_fts(raw, "报价底牌", principal=_principal("user-A"))
