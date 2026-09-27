"""写侧落 user_id：票 .scratch/ownership-gaps/03

`GET /memories` 修好越权（票 01）后，属主过滤第一次真正生效——随即撞上
「库里根本没有属主可过滤」：`add_memory` 收了 `user_id` 参数却不在构造
`MemoryCandidate` 时落列，`add_raw_memory` 签名里就没有该参数，
`create_edge` 也把 `MemoryEdge.user_id` 留在 NULL。于是
`hybrid.py:435` 的 `filters["user_id"]` 与 `fts.py:160` 的
`AND m.user_id = ?` 把**所有人**的检索结果过滤光。

本票治症状 A（写侧不落 → 过滤后为空）；症状 B（读侧不过滤 → 全库泄漏）
已由票 01 治好。只修其一会更糟：只修 A 泄漏依旧，只修 B 功能全废。

测试策略：真实临时 SQLite + 真实产品代码路径（fastpath 直写 / verbatim
直存 / 边写入 / 候选→提案→MemoryItem 演化链），不 mock 任何内部计算；
mock 仅覆盖外部网络（embed 已由 conftest 统一替身）与向量存储副作用。
Red 4 是决定性断言：写完一条 → `hybrid_search(principal=该 user)` 能召回它。
"""

import pytest
from sqlmodel import select

from lantai.core.auth import Principal
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.models.tables import MemoryCandidate, MemoryEdge, MemoryItem


@pytest.fixture()
def write_env(param_env):
    """param_env 底座（内存 SQLite + 建表 + FTS + 外部 LLM/向量替身）。"""
    return param_env


def _principal(user_id: str = "u_writer", tenant_id: str | None = None):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        allowed_lanes=["general", "fact", "preference", "distill"],
        role="user",
    )


# ── Red 1：add_memory 落 user_id ──────────────────────────────────


class TestAddMemoryWritesUserId:
    def test_fastpath_candidate_carries_user_id(self, write_env):
        """fastpath 直写路径：user_id 必须落到 MemoryCandidate。

        现状：`_create_candidate_direct` 只写 session_id，user_id 恒 NULL。
        """
        from lantai.services.memory_service import add_memory

        session_factory, _ = write_env
        add_memory(
            AddMemoryReq(title="偏好", content="我喜欢喝浙江新昌的明前大佛龙井茶", lane="preference"),
            user_id="u_writer",
        )
        from sqlmodel import select

        with session_factory() as s:
            cands = s.exec(select(MemoryCandidate)).all()
        assert cands, "前置条件：写入了一条候选"
        assert cands[0].user_id == "u_writer", (
            f"候选没落 user_id（{cands[0].user_id!r}）——来源链断了"
        )

    def test_extraction_candidate_carries_user_id(self, write_env):
        """LLM 提取路径：同一条 user_id 契约（两条路径必须一致）。"""
        from sqlmodel import select

        from lantai.services.memory_service import add_memory

        session_factory, _ = write_env
        add_memory(
            AddMemoryReq(
                title="部署记录",
                content="生产库连接池上限调整为 200，调整时间是上周三下午。",
                lane="fact",
            ),
            user_id="u_writer",
        )
        with session_factory() as s:
            cands = s.exec(select(MemoryCandidate)).all()
        assert cands, "前置条件：写入了一条候选"
        assert cands[0].user_id == "u_writer", (
            f"提取路径候选没落 user_id（{cands[0].user_id!r}）"
        )


# ── Red 2：add_raw_memory 落 user_id ──────────────────────────────


class TestAddRawMemoryWritesUserId:
    def test_verbatim_item_carries_user_id(self, write_env):
        """原文直存：签名收下 user_id，落进 MemoryItem 与 Evidence。"""
        from sqlmodel import select

        from lantai.services.memory_service import add_raw_memory

        session_factory, _ = write_env
        result = add_raw_memory(
            RawMemoryReq(content="备份命令：mysqldump -u root db > b.sql", lane="fact"),
            user_id="u_writer",
        )
        with session_factory() as s:
            m = s.get(MemoryItem, result["memory_id"])
            assert m is not None
            assert m.user_id == "u_writer", (
                f"verbatim 记忆没落 user_id（{m.user_id!r}）——签名里就没这个参数"
            )
            from lantai.models.tables import Evidence

            ev = s.exec(
                select(Evidence).where(Evidence.source_memory_id == m.id)
            ).first()
            assert ev is not None and ev.user_id == "u_writer", "Evidence 属主未同步"

    def test_build_verbatim_item_takes_owner(self):
        """纯函数构造器：四元组显式入参（import_service 也走这条）。"""
        from lantai.services.memory_service import build_verbatim_item

        item = build_verbatim_item(
            "配置项 A=1", "fact", user_id="u_writer", tenant_id="t1"
        )
        assert item.user_id == "u_writer"
        assert item.tenant_id == "t1"
        # 不传则如实 NULL（宁 miss 不脏写：不猜归属）
        assert build_verbatim_item("配置项 A=1", "fact").user_id is None


# ── Red 3：create_edge 落 user_id ─────────────────────────────────


class TestCreateEdgeWritesUserId:
    def test_edge_carries_user_id(self, write_env):
        """边也带属主：列已存在（tables.py:271-282），只是从不填。"""
        from sqlmodel import select

        from lantai.storage.edges import create_edge

        session_factory, _ = write_env
        with session_factory() as s:
            s.add_all(
                [
                    MemoryItem(id="mem_e_a", content="甲事实", user_id="u_writer", status="active"),
                    MemoryItem(id="mem_e_b", content="乙事实", user_id="u_writer", status="active"),
                ]
            )
            s.commit()

        edge = create_edge("mem_e_a", "mem_e_b", "supports", user_id="u_writer")
        with session_factory() as s:
            row = s.exec(select(MemoryEdge).where(MemoryEdge.id == edge.id)).first()
            assert row is not None
            assert row.user_id == "u_writer", (
                f"边没落 user_id（{row.user_id!r}）——DELETE /edges 的 ACL 无从校验"
            )


# ── Red 4（决定性）：写完能召回 ───────────────────────────────────


class TestWriteThenRecall:
    def test_recall_finds_own_memory_after_write(self, write_env):
        """写完一条 verbatim → 以该 user 的 principal 能召回它（现状 0 条）。"""
        from lantai.retrieval.hybrid import hybrid_search
        from lantai.services.memory_service import add_raw_memory

        session_factory, _ = write_env
        content = "系统部署手册：备份命令 mysqldump -u root db > backup.sql"
        add_raw_memory(RawMemoryReq(content=content, lane="fact"), user_id="u_writer")

        principal = _principal("u_writer")
        results = hybrid_search(
            "mysqldump 备份命令",
            top_k=5,
            use_rerank=False,
            memory_types=["verbatim"],
            principal=principal,
        )
        texts = [r.get("memory", {}).get("content", "") for r in results]
        assert any(content in t for t in texts), (
            f"写完立刻召回不到自己刚写的记忆（返回 {len(results)} 条）——"
            "写侧不落 user_id，属主过滤把结果全滤光"
        )

    def test_evolution_chain_inherits_user_id(self, write_env):
        """候选 → 提案 → MemoryItem 全链继承 user_id（promoter 的来源链）。"""
        from sqlmodel import select

        from lantai.evolution.promoter import apply_proposal
        from lantai.evolution.proposer import propose_from_candidate
        from lantai.services.memory_service import add_memory

        session_factory, _ = write_env
        add_memory(
            AddMemoryReq(title="偏好", content="我喜欢喝浙江新昌的明前大佛龙井茶", lane="preference"),
            user_id="u_writer",
        )
        with session_factory() as s:
            cand = s.exec(select(MemoryCandidate)).first()
            assert cand is not None
            cand.status = "pending_review"
            s.add(cand)
            s.commit()
            cand_id = cand.id

        prop = propose_from_candidate(cand_id, {"decision": "working_only", "novelty": 0.9})
        assert prop is not None
        assert prop.user_id == "u_writer", (
            f"提案没继承 user_id（{prop.user_id!r}）——proposer 的来源链断了"
        )
        applied = apply_proposal(prop.id)
        assert applied["ok"] is True
        with session_factory() as s:
            mem = s.exec(
                select(MemoryItem).where(MemoryItem.status == "active")
            ).first()
            assert mem is not None
            assert mem.user_id == "u_writer", (
                f"MemoryItem 没继承 user_id（{mem.user_id!r}）——写完仍搜不到"
            )


# ── 路由层：principal 必须一路传到写侧 ────────────────────────────


class TestRoutesForwardPrincipalToWriteSide:
    """service 层修好了还不够——路由不转发就前功尽弃（票 01 的同款陷阱）。

    真实 FastAPI app + `dependency_overrides` 注入身份；断言落在**行为**上
    （库里那行的 user_id 是不是注入的这个），不是「参数声明了没有」。
    """

    def test_add_route_persists_principal_owner(self, write_env):
        """POST /add 写的候选，user_id 必须是当前登录者的。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import Principal, get_current_user

        session_factory, _ = write_env
        app.dependency_overrides[get_current_user] = lambda: Principal(
            user_id="u_route", tenant_id="t_route", allowed_lanes=["fact"], role="user"
        )
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/add",
                    json={
                        "title": "部署记录",
                        "content": "生产库连接池上限调整为 200，调整时间是上周三下午。",
                        "lane": "fact",
                    },
                )
                assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        with session_factory() as s:
            cand = s.exec(select(MemoryCandidate)).first()
            assert cand is not None
            assert cand.user_id == "u_route", (
                f"路由没把 principal 传下去（候选 user_id={cand.user_id!r}）"
            )
            assert cand.tenant_id == "t_route", (
                f"路由没把 tenant 传下去（{cand.tenant_id!r}）"
            )

    def test_add_raw_route_persists_principal_owner(self, write_env):
        """POST /add/raw 同款：verbatim 是条数最多的一类，漏了整类搜不回。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import Principal, get_current_user

        session_factory, _ = write_env
        app.dependency_overrides[get_current_user] = lambda: Principal(
            user_id="u_route", tenant_id="t_route", allowed_lanes=["fact"], role="user"
        )
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/add/raw",
                    json={"content": "备份命令 mysqldump -u root db > b.sql", "lane": "fact"},
                )
                assert resp.status_code == 200, resp.text
                mid = resp.json()["memory_id"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        with session_factory() as s:
            m = s.get(MemoryItem, mid)
            assert m is not None
            assert m.user_id == "u_route", (
                f"verbatim 没落登录者的 user_id（{m.user_id!r}）"
            )

    def test_edges_route_persists_principal_owner(self, write_env):
        """POST /edges 建的边必须带属主，否则 DELETE 的 ACL 形同虚设。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import Principal, get_current_user

        session_factory, _ = write_env
        with session_factory() as s:
            s.add_all(
                [
                    MemoryItem(id="mem_r_a", content="甲", user_id="u_route", status="active"),
                    MemoryItem(id="mem_r_b", content="乙", user_id="u_route", status="active"),
                ]
            )
            s.commit()

        app.dependency_overrides[get_current_user] = lambda: Principal(
            user_id="u_route", tenant_id="t_route", allowed_lanes=["fact"], role="user"
        )
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/edges",
                    json={
                        "source_memory_id": "mem_r_a",
                        "target_memory_id": "mem_r_b",
                        "relation": "supports",
                    },
                )
                assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        with session_factory() as s:
            edge = s.exec(select(MemoryEdge)).first()
            assert edge is not None
            assert edge.user_id == "u_route", (
                f"边没落登录者的 user_id（{edge.user_id!r}）——ACL 无从校验"
            )

    def test_terminal_merge_edge_persists_principal_owner(self, write_env):
        """POST /terminal/merge 的 supersedes 边同样要带属主。

        该边直接参与召回排序（hybrid.py 的 `_edge_cb` 无用户过滤），
        不带属主时既无法审计也无法用 DELETE 的 ACL 收回。
        """
        from fastapi.testclient import TestClient
        from sqlmodel import Session as _Session

        from lantai.api.app import app
        from lantai.core.auth import Principal, get_current_user

        session_factory, _ = write_env
        with session_factory() as s:
            s.add_all(
                [
                    MemoryItem(
                        id="mem_m_src",
                        content="旧的说法",
                        user_id="u_route",
                        lane="fact",
                        status="active",
                    ),
                    MemoryItem(
                        id="mem_m_tgt",
                        content="新的说法",
                        user_id="u_route",
                        lane="fact",
                        status="active",
                    ),
                ]
            )
            s.commit()

        principal = Principal(
            user_id="u_route", tenant_id="t_route", allowed_lanes=["fact"], role="user"
        )
        app.dependency_overrides[get_current_user] = lambda: principal
        # 合并路由自己开 session（get_db_conn），必须指向同一个临时库。
        # 补丁要打在 `lantai.api.routes_terminal.get_session` 上——该模块顶部
        # `from lantai.storage.db import get_session` 已在 import 期绑死一份引用，
        # 改 storage.db 的同名属性对它无效（实测：路由打到宿主真库，404）。
        import lantai.api.routes_terminal as rt_mod

        orig = rt_mod.get_session
        rt_mod.get_session = lambda: _Session(session_factory().get_bind())
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/terminal/merge", json={"source_id": "mem_m_src", "target_id": "mem_m_tgt"}
                )
                assert resp.status_code == 200, resp.text
        finally:
            rt_mod.get_session = orig
            app.dependency_overrides.pop(get_current_user, None)

        with session_factory() as s:
            edges = s.exec(select(MemoryEdge)).all()
            assert edges, "前置条件：合并产生了 supersedes 边"
            assert all(e.user_id == "u_route" for e in edges), (
                f"合并边没落登录者 user_id（{[e.user_id for e in edges]}）"
            )


# ── 回归护栏 ──────────────────────────────────────────────────────


class TestNoRegression:
    def test_internal_write_defaults_to_dev_mode_owner(self, write_env):
        """不传 user_id 的既有内部调用方（CLI/eval/worker）落到 "default"。

        这不是猜归属：`add_memory` 的签名一直就是 `user_id="default"`
        （票 03 之前参数收了却没落列），DEV MODE principal 也正是
        `auth.py:187` 的 `"default"`。选它而不是 NULL 的理由：NULL 行会被
        按属主过滤的检索整类滤掉（fts.py:160），CLI/MCP 写的内容就永远
        搜不回来；落 "default" 至少与 DEV MODE 自洽。
        """
        from sqlmodel import select

        from lantai.services.memory_service import add_memory

        session_factory, _ = write_env
        add_memory(AddMemoryReq(title="偏好", content="我喜欢喝浙江新昌的明前大佛龙井茶", lane="preference"))
        with session_factory() as s:
            cand = s.exec(select(MemoryCandidate)).first()
            assert cand is not None
            assert cand.user_id == "default", (
                f"未声明归属应落到 DEV MODE 的 default，实得 {cand.user_id!r}"
            )

    def test_build_verbatim_item_without_owner_stays_null(self):
        """构造器不传归属则如实 NULL——那是「未记录」的事实状态，不猜。"""
        from lantai.services.memory_service import build_verbatim_item

        assert build_verbatim_item("配置项 A=1", "fact").user_id is None

    def test_dedup_still_returns_same_memory(self, write_env):
        """幂等语义不变：同内容两次直存返回同一 id。"""
        from lantai.services.memory_service import add_raw_memory

        session_factory, _ = write_env
        r1 = add_raw_memory(RawMemoryReq(content="配置项 A=1"), user_id="u_writer")
        r2 = add_raw_memory(RawMemoryReq(content="配置项 A=1"), user_id="u_writer")
        assert r1["memory_id"] == r2["memory_id"]
        assert r2["dedup"] is True
