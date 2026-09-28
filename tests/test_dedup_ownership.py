"""票 15：向量检索不带归属过滤——A 的一条 `/add` 不再把 B 的记忆正文
送进外部 LLM，也不再改写 B 的 `importance`。

**先说影响**：`memory_service._apply_dedup:52` 与 `gate/decision.py:26`
两处向量检索一个过滤都不带，返回**全库最近邻**。A 控制新记忆的正文即控制
embedding，反复试探即可命中 B 的记忆；随后 `_dedup_structural` 把 B 的正文
与 A 的正文一起送进外部 LLM（`DEDUP_RELATION_*`，内容离开本机即无法撤回），
`_dedup_merge` 改 B 的 `importance`，`_create_update_proposal` 把提案的
`target_memory_id` 钉在 B 的记忆上。

**同批修掉一个既存真 bug**：`hybrid.py:432-438` 原本就是「已做对」的范本，
实测它自己有两处失效——① Chroma 的 `where` **只接受一个顶层算符**，
lane + principal 多键并列直接 `ValueError` → 静默降级成纯关键词检索，
向量召回整条通道消失；② 用 plain equality 滤 `user_id`，
真实库 **332/353** 条向量是空串（NULL 属主）、636/657 行 memoryitem 是
`user_id IS NULL`，全被滤掉，单人部署下检索近乎空转。
新助手 `core.acl.vector_owner_filter` 两处一起修（`$or[user_id=viewer, '']`）。

**纪律**：不 mock 被测函数的内部计算。这里 mock 的只有外部网络
（embed / chat_json）与外部存储（vector_store.search）——
`find_similar` 的相似度比较、`classify_relation` 的规则判别全部真实执行。
"""

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.models.schemas import AddMemoryReq
from lantai.models.tables import ConflictEvent, MemoryCandidate, MemoryItem, MemoryProposal
from lantai.services import memory_service
from lantai.storage import db


# ── 外部 LLM 提示词捕获（票 14 的教训：spy 必须是独立变量）──────────
class PromptSpy:
    """捕获 chat_json 收到的 (system, user) 提示词。

    **不能写成 `patch(..., side_effect=PromptSpy()) as spy`**——那样绑到的
    `spy` 是 MagicMock，`spy.prompts[0]` 还是 MagicMock，而
    `SECRET in <MagicMock>` 恒为 False，`not any(...)` 恒真，测试一片绿
    且看不出任何异常。票 14 因此骗过第一轮变异验证（3 个 MISSED）。
    """

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, system, user, *a, **kw):
        self.prompts.append(user)
        return {"relation": "update", "reason": "stub"}

    def leaked(self, needle: str) -> list[str]:
        return [p for p in self.prompts if needle in p]


@pytest.fixture()
def dd_env(monkeypatch):
    """真实临时 SQLite + 假向量存储（外部存储边界，见模块 docstring）。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(db, "engine", engine)
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(
        "lantai.services.memory_service.embed", lambda texts: [[0.1] * 768 for _ in texts]
    )
    monkeypatch.setattr(
        "lantai.parsing.extractor.chat_json",
        lambda *a, **kw: {
            "summary": "t",
            "claims": [],
            "methods": [],
            "constraints": [],
            "actions": [],
            "topic": [],
            "extractor_confidence": 0.9,
        },
    )
    return engine


def _principal(user_id, *, role="user", tenant_id=None):
    return Principal(user_id=user_id, tenant_id=tenant_id, allowed_lanes=None, role=role)


def _mem(engine, mem_id: str, user_id: str | None, content: str, importance: float = 0.5):
    from lantai.core.time import utcnow

    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mem_id,
                user_id=user_id,
                memory_type="preference",
                key=f"k-{mem_id}",
                title=mem_id,
                content=content,
                lane="preference",
                status="active",
                importance=importance,
                decay_score=0.9,
                tier="working",
                use_count=0,
                helpful_count=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()


def _row(engine, mem_id: str) -> MemoryItem:
    with Session(engine) as s:
        return s.get(MemoryItem, mem_id)


SECRET_B = "B 的银行密码是 9527"


# ── Red 1（决定性）：B 的正文不进外部 LLM 提示词 ─────────────────


class TestDedupNoCrossUserLLM:
    def test_other_users_memory_not_sent_to_llm(self, dd_env):
        """**Red 1（决定性）**：A 的候选命中 B 的最近邻 → B 的正文不进 LLM 提示词。

        断言落在**提示词文本**上：`classify_relation(target.content, content,
        llm_judge=_llm_judge)` 的 judge 把 old/new 都格式化进 user prompt。
        只看 candidates 列表等于只看中间量，提示词才是内容真正离开本机的出口。

        **非空转证明**：同一条测试里以**属主自己**的身份再跑一次，LLM 必须
        被调到且提示词**含有**该正文——否则「A 没进提示词」可能只是因为
        结构判别被关掉、或 judge 没被接上，与归属无关（票 14 同款陷阱：
        探针第一轮只种 B 的记忆，收窄正确时 A 的候选集为空、反思直接 idle、
        LLM 一次都不调，「没进提示词」是因为什么都没跑）。
        """
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B)
        assert memory_service.settings.DEDUP_STRUCTURAL_ENABLED, (
            "结构判别被关掉时本条走的是提案分支，同样不调 LLM——"
            "那样断言就分不清「归属挡住了」和「开关关着」，测试空转"
        )

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-B", "distance": 0.05}]

        # ── 第一半：以 A 的身份 ──
        spy_a = PromptSpy()
        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch("lantai.llm.client.chat_json", side_effect=spy_a),
        ):
            out_a = memory_service._dedup_structural(
                Session(engine), "m-B", "A 的标题", "A 的正文", "preference", 0.93,
                principal=_principal("user-A"),
            )
        assert not spy_a.prompts, (
            f"B 的正文被送进了外部 LLM 提示词（内容离开本机即无法撤回）：{spy_a.prompts}"
        )
        assert out_a is None, f"跨属主目标被改/被提案了：{out_a}"

        # ── 第二半：以属主自己的身份（证明第一半不是空转）──
        spy_b = PromptSpy()
        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch("lantai.llm.client.chat_json", side_effect=spy_b),
        ):
            out_b = memory_service._dedup_structural(
                Session(engine), "m-B", "B 的标题", "B 的新表述", "preference", 0.93,
                principal=_principal("user-B"),
            )
        assert spy_b.prompts, "属主自己也没走到 LLM——第一半的「没调」与归属无关，测试是空转的"
        assert spy_b.leaked(SECRET_B), (
            f"属主自己的提示词里没有该正文，说明 judge 根本没接上：{spy_b.prompts}"
        )
        assert out_b is not None, f"属主自己的结构判别返回了 None：{out_b}"

    def test_own_memory_still_reaches_llm(self, dd_env):
        """反向（Red 4）：A 自己的相似记忆照常走结构判别，功能没被修废。"""
        engine = dd_env
        _mem(engine, "m-A", "user-A", "A 喜欢喝咖啡")

        spy = PromptSpy()

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-A", "distance": 0.05}]

        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch("lantai.llm.client.chat_json", side_effect=spy),
        ):
            out = memory_service._dedup_structural(
                Session(engine), "m-A", "A 的标题", "A 也喜欢咖啡", "preference", 0.93,
                principal=_principal("user-A"),
            )

        assert spy.prompts, "A 自己的记忆也没走到 LLM——收窄把功能修废了"
        assert out is not None, f"A 自己的结构判别返回了 None：{out}"


# ── Red 2 / 3：不改 B 的行、不建指向 B 的提案 ─────────────────────


class TestDedupNoCrossUserMutation:
    def test_merge_does_not_bump_other_users_importance(self, dd_env):
        """**Red 2**：merge 分支不改 B 的 `importance` / `last_used_at`。

        断言落在**落库行字段值**上，不只看返回的 action——action 为 None
        可能有一堆别的原因。
        """
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B, importance=0.5)
        before = _row(engine, "m-B")

        out = memory_service._dedup_merge(
            Session(engine), before, 0.95, principal=_principal("user-A")
        )
        after = _row(engine, "m-B")

        assert out is None, f"跨属主 merge 还是返回了结果：{out}"
        assert after.importance == 0.5, f"B 的 importance 被改了：{before.importance} → {after.importance}"

    def test_update_proposal_not_pinned_to_other_users_memory(self, dd_env):
        """**Red 3**：不生成 `target_memory_id` 指向 B 的提案。"""
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B)
        target = _row(engine, "m-B")

        out = memory_service._create_update_proposal(
            Session(engine), target, "A 的标题", "A 的正文", "preference", 0.93,
            principal=_principal("user-A"),
        )
        assert out is None, f"跨属主 update 提案还是建了：{out}"

        with Session(engine) as s:
            props = s.query(MemoryProposal).all() if hasattr(s, "query") else []
        assert not props, f"落库了指向别人的提案：{[p.id for p in props]}"

    def test_own_merge_still_bumps(self, dd_env):
        """反向：A 对自己的记忆照常 merge bump（不能修废）。"""
        engine = dd_env
        _mem(engine, "m-A", "user-A", "A 喜欢喝咖啡", importance=0.5)

        out = memory_service._dedup_merge(
            Session(engine), _row(engine, "m-A"), 0.95, principal=_principal("user-A")
        )
        assert out is not None and out.get("dedup_action") == "merge", f"A 自己的 merge 被拒：{out}"
        assert _row(engine, "m-A").importance == pytest.approx(0.6), "A 自己的 importance 没 bump"


# ── Red 5 / 6：NULL 属主可见 + admin/None 全库 ────────────────────


class TestDedupOwnerBoundary:
    def test_null_owner_row_still_visible(self, dd_env):
        """Red 5：NULL 属主老行（真实库 636/657）对非 admin 仍走结构判别。

        判「不可见」会让单人部署整体空转——NULL 是「未记录」不是「属于所有人」。
        """
        engine = dd_env
        _mem(engine, "m-legacy", None, "老数据：没有属主")

        spy = PromptSpy()

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-legacy", "distance": 0.05}]

        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch("lantai.llm.client.chat_json", side_effect=spy),
        ):
            out = memory_service._dedup_structural(
                Session(engine), "m-legacy", "A 的标题", "A 的正文", "preference", 0.93,
                principal=_principal("user-A"),
            )

        assert spy.prompts, "NULL 属主老行被漏掉了——单人部署会整体空转"
        assert out is not None, f"NULL 属主行没走结构判别：{out}"

    def test_admin_and_none_see_everything(self, dd_env):
        """Red 6：admin 与 `principal=None`（worker/CLI）都能拿别人的行。"""
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B)
        for p in (_principal("user-A", role="admin"), None):
            assert memory_service._owns(_row(engine, "m-B"), p), (
                f"principal={p!r} 下别人的行不可见——worker/CLI 会空转"
            )


# ── 向量层过滤：`vector_owner_filter` 本体 ────────────────────────


class TestVectorOwnerFilter:
    """`core.acl.vector_owner_filter` 的 Chroma `where` 形状。

    这些断言锁的是**算子结构**，因为 Chroma 的 `where` 只接受一个顶层
    算符——多键并列直接 ValueError，而 `_apply_dedup` 的 except 会把它
    吞掉并静默降级成 insert（票 15 实测：lane + principal 叠加时向量召回
    整条通道消失，且日志里只有一行 warning）。
    """

    def test_none_principal_means_no_filter(self):
        from lantai.core.acl import vector_owner_filter

        assert vector_owner_filter(None) is None

    def test_admin_means_no_filter(self):
        from lantai.core.acl import vector_owner_filter

        assert vector_owner_filter(_principal("user-A", role="admin")) is None

    def test_user_gets_or_including_empty_string(self):
        from lantai.core.acl import vector_owner_filter

        f = vector_owner_filter(_principal("user-A"))
        assert f == {"$or": [{"user_id": "user-A"}, {"user_id": ""}]}, f

    def test_extra_conditions_wrapped_in_and(self):
        from lantai.core.acl import vector_owner_filter

        f = vector_owner_filter(_principal("user-A"), {"lane": "fact"})
        assert set(f) == {"$and"}, f"多条件必须塞进 $and（Chroma 只收一个顶层算符）：{f}"
        assert {"$or": [{"user_id": "user-A"}, {"user_id": ""}]} in f["$and"]
        assert {"lane": "fact"} in f["$and"]

    def test_admin_with_extra_passes_extra_through(self):
        from lantai.core.acl import vector_owner_filter

        assert vector_owner_filter(_principal("x", role="admin"), {"lane": "fact"}) == {
            "lane": "fact"
        }

    def test_real_chroma_accepts_the_shape(self, tmp_path, monkeypatch):
        """不 mock：真起一个 Chroma，确认这个 `where` 真的能查、且滤对了。

        这是本票唯一不能只靠单元断言的地方——算子结构对了但 Chroma 不认，
        `_apply_dedup` 的 except 会把它吞掉，线上表现是「去重静默失效」。
        """
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        monkeypatch.setattr(
            "lantai.core.settings.settings.CHROMADB_PATH", str(tmp_path / "chroma")
        )
        client = chromadb.PersistentClient(
            path=str(tmp_path / "chroma"),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        coll = client.create_collection(
            "lantai_vectors", metadata={"hnsw:space": "cosine"}
        )
        coll.add(
            ids=["m-A", "m-B", "m-legacy"],
            embeddings=[[0.5] * 4] * 3,
            metadatas=[
                {"user_id": "user-A", "lane": "fact"},
                {"user_id": "user-B", "lane": "fact"},
                {"user_id": "", "lane": "fact"},
            ],
        )
        from lantai.core.acl import vector_owner_filter

        where = vector_owner_filter(_principal("user-A"), {"lane": "fact"})
        got = coll.query(query_embeddings=[[0.5] * 4], n_results=10, where=where)
        assert sorted(got["ids"][0]) == ["m-A", "m-legacy"], (
            f"过滤结果不对：{sorted(got['ids'][0])}（应含 A 自己的与 NULL 属主的，不含 B 的）"
        )


# ── find_similar 的 session.get 盲区 ──────────────────────────────


class TestFindSimilarOwnerBoundary:
    """`find_similar` 按主键直读，**绕开一切 SQL 层 scope**。

    上游向量层滤对了，这一下仍要单独判——票 14 的 rejecter 就是同一个
    盲区（`s.get(MemoryItem, eid)` 把别人的正文读出来拼进 LLM 提示词）。
    """

    def test_skips_other_users_row(self, dd_env):
        from lantai.gate.dedup import find_similar

        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B)

        with Session(engine) as s:
            action, target, sim = find_similar(
                s, [{"id": "m-B", "distance": 0.05}], fastpath=True,
                principal=_principal("user-A"),
            )
        assert action == "insert", f"跨属主的行被当成 merge/update 目标了：action={action}"
        assert target is None, f"跨属主的 ORM 对象被交出去给下游写者：{target}"

    def test_keeps_own_and_null_owner_rows(self, dd_env):
        """反向：自己的行与 NULL 属主老行都要能当目标（不能修废）。"""
        from lantai.gate.dedup import find_similar

        engine = dd_env
        _mem(engine, "m-A", "user-A", "A 喜欢喝咖啡")
        _mem(engine, "m-legacy", None, "老数据：没有属主")

        for mid in ("m-A", "m-legacy"):
            with Session(engine) as s:
                action, target, sim = find_similar(
                    s, [{"id": mid, "distance": 0.05}], fastpath=True,
                    principal=_principal("user-A"),
                )
            assert action == "merge", f"{mid} 没能成为 merge 目标（功能被修废）：action={action}"
            assert target is not None and target.id == mid

    def test_admin_and_none_keep_other_users_row(self, dd_env):
        from lantai.gate.dedup import find_similar

        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B)
        for p in (_principal("user-A", role="admin"), None):
            with Session(engine) as s:
                action, target, _ = find_similar(
                    s, [{"id": "m-B", "distance": 0.05}], fastpath=True, principal=p
                )
            assert action == "merge", f"principal={p!r} 下别人的行不可见（worker 会空转）"


# ── _apply_dedup 真的把 filters 传给了向量检索 ───────────────────


class TestApplyDedupPassesFilters:
    """用**真 Chroma**（临时目录）验「filters 传对了且真的滤掉了 B」。

    不 mock `vector_store.search`：mock 掉的 store 收不收 `filters` 都行，
    断言就只剩「调用签名里有这个 kwarg」——那证明不了检索结果真的被收窄。
    真实 Chroma 会按 `where` 过滤，漏传/传错形状都会在这里现形。
    """

    @pytest.fixture()
    def chroma_env(self, dd_env, tmp_path, monkeypatch):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        path = str(tmp_path / "chroma")
        monkeypatch.setattr("lantai.core.settings.settings.CHROMADB_PATH", path)
        import lantai.storage.vector_store as vs_mod

        monkeypatch.setattr(vs_mod, "_store", None)
        client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False)
        )
        coll = client.create_collection(
            "lantai_vectors", metadata={"hnsw:space": "cosine"}
        )
        # 三条同向量的记忆：A 自己的、B 的、NULL 属主的老行
        coll.add(
            ids=["m-A", "m-B", "m-legacy"],
            embeddings=[[0.5] * 4] * 3,
            metadatas=[
                {"user_id": "user-A", "lane": "preference"},
                {"user_id": "user-B", "lane": "preference"},
                {"user_id": "", "lane": "preference"},
            ],
        )
        # embed 返回同一向量 → 三条距离相同，top_k=1 取哪条完全由 where 决定
        monkeypatch.setattr(
            "lantai.services.memory_service.embed", lambda texts: [[0.5] * 4 for _ in texts]
        )
        return dd_env

    def test_apply_dedup_never_returns_other_users_row(self, chroma_env):
        engine = chroma_env
        for uid in ("user-A", "user-B", "user-C"):
            _mem(engine, f"db-{uid}", uid, f"{uid} 的正文")

        with Session(engine) as s:
            action, target, sim = memory_service._apply_dedup(
                s, "任意内容", fastpath=True, principal=_principal("user-A")
            )
        assert target is None or target.user_id in (None, "user-A"), (
            f"_apply_dedup 把别人的行当成了目标：{target.id if target else None} "
            f"user_id={target.user_id if target else None}——filters 没传或没生效"
        )

    def test_apply_dedup_sees_own_and_null_owner(self, chroma_env):
        """反向：A 自己的行与 NULL 属主老行必须能被 _apply_dedup 选中。"""
        engine = chroma_env
        _mem(engine, "m-A", "user-A", "A 喜欢喝咖啡")
        _mem(engine, "m-legacy", None, "老数据：没有属主")

        with Session(engine) as s:
            action, target, sim = memory_service._apply_dedup(
                s, "任意内容", fastpath=True, principal=_principal("user-A")
            )
        assert action == "merge", (
            f"A 自己的行也没被选中（filters 收窄过头，单人部署会空转）：action={action}"
        )
        assert target is not None and target.id in ("m-A", "m-legacy"), (
            f"选中的不是自己的/NULL 属主的行：{target.id if target else None}"
        )


# ── fastpath 路径（`_create_candidate_direct`）───────────────────


class TestFastpathOwnerBoundary:
    """fastpath 直书路径也要收窄——它与提取路径是两个分支，漏一个就半修。

    `_create_candidate_direct` 此前只把 `user_id` 盖在新候选上，从不下传
    去重（形参在、值在、就是没往下走——票 15 最容易漏的一处）。
    """

    def test_fastpath_does_not_touch_other_users_row(self, dd_env):
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B, importance=0.5)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-B", "distance": 0.02}]

        spy = PromptSpy()
        req = AddMemoryReq(title="A 的标题", content=SECRET_B, lane="preference")
        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch("lantai.llm.client.chat_json", side_effect=spy),
        ):
            out = memory_service._create_candidate_direct(
                req,
                {"topic": [], "summary": "s", "claims": [], "methods": [],
                 "constraints": [], "actions": [], "extractor_confidence": 0.9,
                 "lane": "preference"},
                user_id="user-A",
                principal=_principal("user-A"),
            )

        after = _row(engine, "m-B")
        assert after.importance == 0.5, f"fastpath 路径改了 B 的 importance：{after.importance}"
        assert not spy.prompts, f"fastpath 路径把 B 的正文送进了 LLM：{spy.prompts}"
        # 该走正常建候选，而不是 merge/update
        assert "candidate_id" in out, f"fastpath 没落到建候选分支：{out}"

    def test_fastpath_still_merges_own_row(self, dd_env):
        """反向：A 自己的重复内容照常 merge bump（不能修废）。"""
        engine = dd_env
        _mem(engine, "m-A", "user-A", "A 喜欢喝咖啡，每天早上喝两杯", importance=0.5)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-A", "distance": 0.02}]

        req = AddMemoryReq(
            title="A 的标题", content="A 喜欢喝咖啡，每天早上喝两杯", lane="preference"
        )
        with patch("lantai.services.memory_service.get_vector_store", lambda: VS()):
            out = memory_service._create_candidate_direct(
                req,
                {"topic": [], "summary": "s", "claims": [], "methods": [],
                 "constraints": [], "actions": [], "extractor_confidence": 0.9,
                 "lane": "preference"},
                user_id="user-A",
                principal=_principal("user-A"),
            )

        assert out.get("dedup_action") == "merge", f"A 自己的 fastpath merge 被拒：{out}"
        assert _row(engine, "m-A").importance == pytest.approx(0.6), "A 自己的 importance 没 bump"


# ── `_principal_of`：显式 principal 必须优先 ─────────────────────


class TestPrincipalOf:
    """显式传入的 principal 优先于按 user_id 收敛。

    否则 admin/worker 显式传来的身份会被 user_id 悄悄覆盖，
    「admin 全表」的口径在去重链上失效。
    """

    def test_explicit_principal_wins(self):
        from lantai.services.memory_service import _principal_of

        admin = _principal("someone", role="admin")
        assert _principal_of(admin, "user-A", "T1") is admin

    def test_falls_back_to_user_id(self):
        from lantai.services.memory_service import _principal_of

        p = _principal_of(None, "user-A", "T1")
        assert p.user_id == "user-A", f"回落构造的 principal user_id 不对：{p.user_id}"
        assert p.tenant_id == "T1"

    def test_none_user_id_converges_to_default(self):
        from lantai.services.memory_service import _principal_of

        assert _principal_of(None, None, None).user_id == "default"


# ── gate/decision：冲突比对的归属边界 ────────────────────────────


@pytest.fixture()
def gate_env(dd_env, monkeypatch):
    """候选 + B 的记忆；矛盾检测与向量检索都mock 成外部依赖。

    模块级（不是类内）：`/gate` 路由测试要用同一套夹具——路由层与 service
    层必须验的是同一条链，分两个夹具就可能一边修好一边没修。
    """
    from lantai.core.time import utcnow

    engine = dd_env
    with Session(engine) as s:
        s.add(
            MemoryCandidate(
                id="cand-A",
                user_id="user-A",
                document_id="doc-A",
                summary="A 的摘要：与 B 的记忆关键词相撞",
                claims=["A 的主张"],
                extractor_confidence=0.9,
                status="pending",
                created_at=utcnow(),
            )
        )
        s.commit()
    _mem(engine, "m-B", "user-B", SECRET_B, importance=0.9)

    # 向量检索返回 B 的记忆：模拟「全库最近邻命中 B」
    class VS:
        def search(self, qe, top_k=8, filters=None):
            return [{"id": "m-B", "distance": 0.05}]

    monkeypatch.setattr("lantai.gate.decision.get_vector_store", lambda: VS())
    monkeypatch.setattr("lantai.gate.decision.embed", lambda texts: [[0.1] * 768 for _ in texts])
    # 矛盾检测：外部 LLM
    monkeypatch.setattr(
        "lantai.gate.decision.check_contradiction",
        lambda a, b: {"contradicts": True, "severity": "high", "reason": "stub"},
    )
    return engine


class TestGateDecideOwnerBoundary:
    """`decide` 的冲突比对此前在**全库**范围做。

    A 构造一条与 B 的记忆关键词相撞的候选，`decide` 就会：把 B 的正文送进
    矛盾检测 LLM、按 salience **降 B 的 importance**、并往 B 的记忆上写
    ConflictEvent——三件事 A 都无权做。
    """

    def test_decide_does_not_demote_other_users_importance(self, gate_env):
        from lantai.gate.decision import decide

        before = _row(gate_env, "m-B")
        out = decide("cand-A", principal=_principal("user-A"))
        after = _row(gate_env, "m-B")

        assert "decision" in out, f"decide 没返回裁决结果：{out}"

        assert after.importance == before.importance, (
            f"A 的候选降了 B 的 importance：{before.importance} → {after.importance}"
            f"（importance 是遗忘与考功的输入，等于间接操控别人的演化结果）"
        )

    def test_decide_writes_no_conflict_event_on_other_users_memory(self, gate_env):
        from lantai.gate.decision import decide
        from lantai.models.tables import ConflictEvent

        decide("cand-A", principal=_principal("user-A"))
        with Session(gate_env) as s:
            evs = list(s.exec(__import__("sqlmodel").select(ConflictEvent)).all())
        assert not [e for e in evs if e.memory_id == "m-B"], (
            f"往 B 的记忆上写了 ConflictEvent：{[e.id for e in evs if e.memory_id == 'm-B']}"
        )

    def test_decide_owner_sees_own_conflicts(self, gate_env):
        """反向：属主自己的冲突照常检出（不能修废）。"""
        from lantai.gate.decision import decide

        _mem(gate_env, "m-A2", "user-A", "A 自己的旧记忆", importance=0.9)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-A2", "distance": 0.05}]

        with patch("lantai.gate.decision.get_vector_store", lambda: VS()):
            out = decide("cand-A", principal=_principal("user-A"))
        # 命中硬冲突 → ARCHIVE_CONFLICT（不是「什么都没发生」）
        assert out.get("conflicts"), f"属主自己的冲突没被检出：{out}"

    def test_decide_null_owner_row_still_visible(self, gate_env):
        """NULL 属主老行（真实库 636/657）对非 admin 仍参与比对。"""
        from lantai.gate.decision import decide

        _mem(gate_env, "m-legacy", None, "老数据：没有属主", importance=0.9)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [{"id": "m-legacy", "distance": 0.05}]

        with patch("lantai.gate.decision.get_vector_store", lambda: VS()):
            out = decide("cand-A", principal=_principal("user-A"))
        assert out.get("conflicts"), f"NULL 属主老行被漏掉了（单人部署会空转）：{out}"

    def test_decide_without_principal_converges_to_candidate_owner(self, gate_env):
        """`principal=None` → 按候选自身 user_id 收敛（worker 调用的默认）。"""
        from lantai.gate.decision import decide

        out = decide("cand-A")
        after = _row(gate_env, "m-B")
        assert "decision" in out, f"decide 没返回裁决结果：{out}"
        assert after.importance == 0.9, (
            f"不传 principal 时按候选属主收敛失效，B 的 importance 被改：{after.importance}"
        )

    # ── 端到端可见性：B 的弱记忆不能被 A 的候选降权 ──
    #
    # 上面几条断言的是「B 的 importance 没变」，那只能证明「没写坏」；
    # 这里要证明的是**降权通道本身对 A 是关着的**——否则把 B 的
    # importance 调低一点，或换一条刚好高于阈值的记忆，就又能得手。
    # 做法：让命中真的走通 salience demote（低 importance + 确定性规则），
    # 然后验 B 没被降权、没被写账本、而 A 自己的弱记忆照常被降权。

    @pytest.fixture()
    def demote_env(self, dd_env, monkeypatch):
        """候选与两条**弱**记忆（importance < CONFLICT_SALIENCE_MIN_IMPORTANCE）。"""
        from lantai.core.time import utcnow

        engine = dd_env
        with Session(engine) as s:
            s.add(
                MemoryCandidate(
                    id="cand-A",
                    user_id="user-A",
                    document_id="doc-A",
                    summary="A 的摘要：这个功能已启用",
                    claims=["A 的主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()
        # B 的弱记忆：命中「启用/禁用」互斥规则，importance 0.1 < 0.4 → 本应被降权
        _mem(engine, "m-B-weak", "user-B", "这个功能已禁用", importance=0.1)
        # A 自己的弱记忆：同样命中 → 该被降权
        _mem(engine, "m-A-weak", "user-A", "这个功能已禁用", importance=0.1)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                return [
                    {"id": "m-B-weak", "distance": 0.05},
                    {"id": "m-A-weak", "distance": 0.05},
                ]

        monkeypatch.setattr("lantai.gate.decision.get_vector_store", lambda: VS())
        monkeypatch.setattr(
            "lantai.gate.decision.embed", lambda texts: [[0.1] * 768 for _ in texts]
        )
        return engine

    def _demoted_events(self, engine, mem_id):
        with Session(engine) as s:
            evs = s.exec(
                select(ConflictEvent).where(ConflictEvent.memory_id == mem_id)
            ).all()
        return [e for e in evs if e.kind == "salience_demote"]

    def test_cross_owner_weak_memory_is_not_demoted(self, demote_env):
        from lantai.gate.decision import decide

        before = _row(demote_env, "m-B-weak").importance
        out = decide("cand-A", principal=_principal("user-A"))
        after = _row(demote_env, "m-B-weak").importance

        assert "decision" in out, f"decide 没返回裁决结果：{out}"

        assert after == before, (
            f"A 的候选降了 B 的弱记忆 importance：{before} → {after}"
            "（salience 降权会写 Checkpoint 与 ConflictEvent，等于改别人的演化结果）"
        )
        assert not self._demoted_events(demote_env, "m-B-weak"), (
            "往 B 的记忆上写了 salience_demote 账本"
        )

    def test_owner_weak_memory_still_demoted(self, demote_env):
        """反向：A 自己的弱记忆照常降权（不能修废——降权是 ADR-0020 的正常行为）。"""
        from lantai.gate.decision import decide

        before = _row(demote_env, "m-A-weak").importance
        decide("cand-A", principal=_principal("user-A"))
        after = _row(demote_env, "m-A-weak").importance

        assert after < before, (
            f"A 自己的弱记忆没被降权：{before} → {after}"
            "（降权通道被顺手关掉了，ADR-0020 的弱记忆让位行为失效）"
        )
        assert self._demoted_events(demote_env, "m-A-weak"), (
            "A 自己的降权没写账本（宁 miss 不脏写：可回滚性要求留痕）"
        )

    def test_demote_boundary_survives_without_principal(self, demote_env):
        """`principal=None` 的 worker 调用同样收敛到候选属主（M22）。"""
        from lantai.gate.decision import decide

        before = _row(demote_env, "m-B-weak").importance
        decide("cand-A")
        assert _row(demote_env, "m-B-weak").importance == before, (
            "不传 principal 时没按候选属主收敛，B 的弱记忆被降权了"
        )

    def test_demote_boundary_survives_via_route(self, demote_env):
        """路由层同样收敛（M25）——宿主打的是 HTTP。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        before = _row(demote_env, "m-B-weak").importance
        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                r = c.post("/gate", json={"candidate_id": "cand-A"})
            assert r.status_code == 200, f"/gate 失败：{r.status_code} {r.text[:200]}"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
        assert _row(demote_env, "m-B-weak").importance == before, (
            "经 /gate 路由 B 的弱记忆仍被降权——principal 没传到 decide"
        )


# ── 下传链：`_apply_dedup` 与 `hybrid_search` 真的把过滤交给向量检索 ──


class TestFilterThreading:
    """「把 `filters` 传下去」这件事必须单独有断言。

    `_owns` 是最后一道防线，它挡住了绝大多数越权——但防线之前的
    检索层如果全库取，`find_similar` 拿到的候选集本身就带着别人的行，
    既浪费 top_k 名额，也让「防线有没有生效」只能靠事后回查。
    本票修的是**两头**：检索层收窄 + 行层兜底，缺一头都算半修。
    """

    def test_apply_dedup_passes_owner_filter_to_vector_search(self, dd_env):
        captured: dict = {}

        class VS:
            def search(self, qe, top_k=8, filters=None):
                captured["filters"] = filters
                return []

        with patch("lantai.services.memory_service.get_vector_store", lambda: VS()):
            with Session(dd_env) as s:
                memory_service._apply_dedup(
                    s, "任意内容", fastpath=True, principal=_principal("user-A")
                )

        assert captured.get("filters") is not None, (
            "`_apply_dedup` 没给 `vector_store.search` 传 filters"
            "——检索层仍是全库最近邻（票 15 的主病灶没修）"
        )
        assert captured["filters"] == {
            "$or": [{"user_id": "user-A"}, {"user_id": ""}]
        }, f"filters 形状不对：{captured['filters']}"

    def test_apply_dedup_admin_gets_no_filter(self, dd_env):
        """admin / principal=None 必须无过滤（worker 不能空转）。"""
        seen: list = []

        class VS:
            def search(self, qe, top_k=8, filters=None):
                seen.append(filters)
                return []

        for p in (_principal("user-A", role="admin"), None):
            with patch("lantai.services.memory_service.get_vector_store", lambda: VS()):
                with Session(dd_env) as s:
                    memory_service._apply_dedup(s, "任意内容", fastpath=True, principal=p)
        assert seen == [None, None], f"admin/None 被加了过滤：{seen}"

    def test_hybrid_search_passes_owner_filter(self, dd_env):
        """`hybrid.py` 这一处是**本票同批修的既存 bug**，不是新加的功能。

        原来它在 `lanes` 与 principal 条件叠加时因 Chroma 多键限制整次
        ValueError → 静默降级成纯关键词检索（向量召回整条通道消失）。
        所以这里连 `lanes` 一起传：既验归属过滤，也验 $and 包裹。
        """
        captured: dict = {}

        class VS:
            def search(self, qe, top_k=8, filters=None):
                captured["filters"] = filters
                return []

        with (
            patch("lantai.retrieval.hybrid.get_vector_store", lambda: VS()),
            patch("lantai.retrieval.hybrid.embed", lambda texts: [[0.1] * 768 for _ in texts]),
        ):
            from lantai.retrieval.hybrid import hybrid_search

            hybrid_search(
                "查询词", top_k=5, lanes=["preference"],
                principal=_principal("user-A"), use_rerank=False,
            )

        assert captured.get("filters") is not None, (
            "`hybrid_search` 没把 filters 交给向量检索——A 的查询能召回 B 的记忆"
        )
        expect = {"$and": [{"$or": [{"user_id": "user-A"}, {"user_id": ""}]},
                           {"lane": "preference"}]}
        assert captured["filters"] == expect, (
            f"filters 形状不对：{captured['filters']}\n期望：{expect}"
            "（多键并列会被 Chroma 拒掉并静默降级成纯关键词检索）"
        )


# ── /gate 路由层 ─────────────────────────────────────────────────


class TestGateRouteIdentity:
    """路由层：宿主打的是 HTTP，路由少取一次身份照样漏（票 12/13 都在路由层栽过）。"""

    def test_route_passes_principal(self, gate_env):
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                r = c.post("/gate", json={"candidate_id": "cand-A"})
            assert r.status_code == 200, f"/gate 失败：{r.status_code} {r.text[:200]}"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert _row(gate_env, "m-B").importance == 0.9, (
            "经 /gate 路由 B 的 importance 仍被改——principal 没传到 decide"
        )

    def test_route_rejects_adjudicating_other_users_candidate(self, gate_env):
        """A 打 `/gate` 裁 B 的候选 → 403。

        **为什么单靠「B 的 importance 没变」不够**：路由丢掉 `ctx` 后
        `decide` 会自行按候选属主收敛，B 的记忆一行不动，那条断言全绿。
        但**B 的裁决结果仍由 A 说了算**——A 能让 B 的待决内容晋升成正式
        记忆。所以必须直接断言越权被拒（口径同票 02 的提案裁决）。
        """
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user
        from lantai.core.time import utcnow

        with Session(gate_env) as s:
            s.add(
                MemoryCandidate(
                    id="cand-B",
                    user_id="user-B",
                    document_id="doc-B",
                    summary="B 的摘要：这个功能已启用",
                    claims=["B 的主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                r = c.post("/gate", json={"candidate_id": "cand-B"})
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 403, (
            f"A 裁 B 的候选没被拒：{r.status_code} {r.text[:200]}"
            "——路由的登录身份只当默认值用，从没被强制校验"
        )

    def test_route_admin_can_adjudicate_any_candidate(self, gate_env):
        """反向：admin 全权（单机部署 + 运维场景）。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user
        from lantai.core.time import utcnow

        with Session(gate_env) as s:
            s.add(
                MemoryCandidate(
                    id="cand-B2",
                    user_id="user-B",
                    document_id="doc-B2",
                    summary="B 的摘要：这个功能已启用",
                    claims=["B 的主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()

        app.dependency_overrides[get_current_user] = lambda: _principal("adm", role="admin")
        try:
            with TestClient(app) as c:
                r = c.post("/gate", json={"candidate_id": "cand-B2"})
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 200, (
            f"admin 裁别人的候选被拒：{r.status_code} {r.text[:200]}"
        )

    def test_decide_rejects_cross_owner_candidate_directly(self, gate_env):
        """service 层同口径：MCP / CLI 传了 principal 也一样拒。"""
        from fastapi import HTTPException

        from lantai.core.time import utcnow
        from lantai.gate.decision import decide

        with Session(gate_env) as s:
            s.add(
                MemoryCandidate(
                    id="cand-B3",
                    user_id="user-B",
                    document_id="doc-B3",
                    summary="B 的摘要：这个功能已启用",
                    claims=["B 的主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()

        with pytest.raises(HTTPException) as ei:
            decide("cand-B3", principal=_principal("user-A"))
        assert ei.value.status_code == 403, f"期望 403，实得 {ei.value.status_code}"

    def test_decide_cross_tenant_candidate_rejected(self, gate_env):
        """同 user_id 但不同 tenant → 403（M27：丢了租户维度这条就漏）。

        多租户部署下 `user_id` 可重名，只按 user_id 判等于把租户墙拆了。
        """
        from fastapi import HTTPException

        from lantai.core.time import utcnow
        from lantai.gate.decision import decide

        with Session(gate_env) as s:
            s.add(
                MemoryCandidate(
                    id="cand-other-tenant",
                    user_id="user-A",
                    tenant_id="T2",
                    document_id="doc-t2",
                    summary="另一个租户的同名用户候选",
                    claims=["跨租户主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()

        with pytest.raises(HTTPException) as ei:
            decide("cand-other-tenant", principal=_principal("user-A", tenant_id="T1"))
        assert ei.value.status_code == 403, (
            f"跨租户裁决没被拒：{ei.value.status_code}"
            "——user_id 重名时租户墙被拆穿"
        )

    def test_decide_null_owner_candidate_policy(self, gate_env):
        """NULL 属主老候选的口径**跟 `ensure_can_delete` 单一真源走**，不自创。

        `acl.ensure_can_delete` 的既定口径：资源 `user_id` 为空「不视为
        越权，只受 lane 约束」（v022 之前的历史行）。裁决复用同一 helper，
        所以 NULL 属主候选非 admin 也能裁——这是**有意的**，不是漏判：
        真实库 636/657 行 memoryitem 是 NULL 属主，判不可见会让单人部署
        整体空转。要收紧就得改 helper 本身并同步票 02 的提案裁决，
        不能在裁决处另立一套。
        """
        from lantai.core.time import utcnow
        from lantai.gate.decision import decide

        with Session(gate_env) as s:
            s.add(
                MemoryCandidate(
                    id="cand-legacy",
                    user_id=None,
                    document_id="doc-legacy",
                    summary="老候选：没有属主",
                    claims=["老主张"],
                    extractor_confidence=0.9,
                    status="pending",
                    created_at=utcnow(),
                )
            )
            s.commit()

        out = decide("cand-legacy", principal=_principal("user-A"))
        assert "decision" in out, f"NULL 属主候选没走通（单人部署会空转）：{out}"

        out_admin = decide("cand-legacy", principal=_principal("adm", role="admin"))
        assert "decision" in out_admin, f"admin 裁 NULL 属主候选失败：{out_admin}"


class TestAddMemoryEndToEnd:
    def test_add_memory_scoped_to_owner(self, dd_env):
        """端到端：A `add_memory` 相似内容 → 不动 B 的行、不建指向 B 的提案。

        `add_memory` 收了 `user_id` 却只用来盖**新**候选的归属列，从不下传
        去重——这是票 15 最容易被漏掉的一处（形参在、值在、就是没往下走）。
        """
        engine = dd_env
        _mem(engine, "m-B", "user-B", SECRET_B, importance=0.5)

        class VS:
            def search(self, qe, top_k=8, filters=None):
                # 真实 Chroma 在此处会按 filters 滤掉 B；这里直接模拟「滤完只剩 B」
                # 已不可能——返回 B 是为了证明**下游仍会拒**（纵深防御）。
                return [{"id": "m-B", "distance": 0.05}]

        req = AddMemoryReq(title="A 的标题", content=SECRET_B, lane="preference")
        with (
            patch("lantai.services.memory_service.get_vector_store", lambda: VS()),
            patch(
                "lantai.llm.client.chat_json",
                side_effect=PromptSpy(),
            ),
        ):
            memory_service.add_memory(
                req, user_id="user-A", tenant_id=None, principal=_principal("user-A")
            )

        after = _row(engine, "m-B")
        assert after.importance == 0.5, f"B 的 importance 被 A 的写入改了：{after.importance}"
        with Session(engine) as s:
            props = list(s.exec(__import__("sqlmodel").select(MemoryProposal)).all())
        assert not props, f"落库了指向 B 的提案：{[p.id for p in props]}"
