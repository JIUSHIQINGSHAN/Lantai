"""MCP 层接线第一波：36 个 handler 补 `principal` 透传。

票 `.scratch/mcp-identity-gaps/01a-mechanical-wiring.md`

**先说影响**：`lantai/cli/mcp.py` 是**完全独立的第二个入口面，且没有
HTTP 鉴权层**。`handle()` 只做 JSON-RPC 分派，不构造 `Principal`；
身份只能由宿主在 `params` 里自愿透传 `user_id`。60 个 handler 里只有
5 个调了 `_principal_from_params`，其余 55 个直接调 service 且不传
principal——而 `principal=None` 在仓里的语义被明确定义为「不过滤」。

于是 **23 张票在 HTTP 侧修好的每一个洞，在 MCP 侧原样复现**：
A 调一次 `mem_recent` 就拿到 B 的记忆全文（探针场景 1 实测）。

**本波的修法**：下游 service 函数全部已有 `principal` 形参与收窄逻辑
（23 张票的成果），MCP 侧只差一根线——每个 handler 顶部
`principal = _principal_from_params(params)`，然后 `principal=principal`
传进去。范本是 `handle_search` / `handle_feedback` / `handle_rollback` /
`handle_reflect_run` / `handle_graph_expand_search` 这 5 个已在用的。

测试纪律（AGENTS.md）：不 mock 内部逻辑——真 `handle()` 分派 + 内存
SQLite 真建表 + `patch db_module.get_session`。mock 只允许覆盖外部
网络（LLM/embedding/向量存储），照抄 `tests/test_mcp.py` 的 `mcp_env`。
"""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

MCP_PATH = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env():
    """内存 SQLite 真实建表 + FTS；仅 mock 外部依赖（embedding/向量存储）。

    与 `tests/test_mcp.py` 的 `mcp_env` 同形状——本票的用例多数要在
    `patch` 里再嵌一层 `patch`（例如 `raw_add` 走 embedding），所以夹具
    只提供**基础设施**，具体的网络 mock 由各用例自己加。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _call(mod, name, args):
    resp = mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    return resp


def _call_ok(mod, name, args):
    """打一次 tools/call 并要求成功，返回解析后的 result。"""
    resp = _call(mod, name, args)
    assert "error" not in resp, f"{name} 报错（该成功）：{resp.get('error')}"
    return json.loads(resp["result"]["content"][0]["text"])


def _seed_memory(s, mid, content, *, user_id="default", lane="fact", status="active"):
    from lantai.models.tables import MemoryItem

    now = datetime.now(UTC)
    s.add(
        MemoryItem(
            id=mid,
            memory_type="semantic",
            key=f"k-{mid}",
            content=content,
            lane=lane,
            status=status,
            user_id=user_id,
            importance=0.5,
            decay_score=1.0,
            decay_class="episodic",
            use_count=0,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(hours=1),
        )
    )


# ── Red 1（决定性）：A 读不到 B 的记忆 ────────────────────────────


class TestReadSideScoped:
    def test_mem_recent_scoped_to_viewer(self, env):
        """A 调 `mem_recent`（带 user_id）→ 只回 A 的记忆。

        修前：返回全表，含 B 的 `user-B` 记忆全文（探针场景 1 实测：
        `★ B 的记忆进来了？ True`）。根因是 `build_memories_page`
        （memory_service.py:732）整个归属块挂在 `if principal:` 下，
        `None` 时一个条件都不加。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "mem-A-1", "A 的记忆：下周要发版", user_id="user-A")
            _seed_memory(s, "mem-B-1", "B 的私有记忆：收购对家的报价底牌", user_id="user-B")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_recent", {"limit": 50, "user_id": "user-A"})
        ids = [m["id"] for m in out["memories"]]
        assert "mem-B-1" not in ids, f"A 读到了 B 的记忆：{ids}"
        assert "mem-A-1" in ids, f"A 连自己的记忆都没读到：{ids}"
        assert "收购对家的报价底牌" not in json.dumps(out, ensure_ascii=False)

    def test_candidates_pending_scoped_to_viewer(self, env):
        """A 调 `candidates_pending`（带 user_id）→ 只回 A 的候选。

        **这一条与普查的笼统说法不同，必须写清**：`_owner_scope(None)`
        返回 `MemoryCandidate.user_id == _viewer_of(None)`，而
        `_viewer_of(None) == "default"`——所以宿主**不透传**时是收敛到
        "default"，**不是全表**。但宿主透传 `user_id="user-A"` 时，
        handler 若不取身份，service 收到的仍是 None → 仍是 "default"
        → A 看不到自己的候选。两种错法都要拦住。
        """
        session_factory, _ = env
        from lantai.models.tables import MemoryCandidate

        now = datetime.now(UTC)
        with session_factory() as s:
            for cid, uid, summary in (
                ("cand-A", "user-A", "A 的待审候选：并购谈判的底线"),
                ("cand-B", "user-B", "B 的待审候选：另一份底牌"),
            ):
                s.add(
                    MemoryCandidate(
                        id=cid,
                        content=summary,
                        summary=summary,
                        user_id=uid,
                        document_id=f"doc-{cid}",
                        status="pending_review",
                        review_due_at=now,
                        created_at=now,
                    )
                )
            s.commit()

        out = _call_ok(_load_mcp(), "candidates_pending", {"limit": 50, "user_id": "user-A"})
        blob = json.dumps(out, ensure_ascii=False)
        assert "cand-A" in blob, "A 看不到自己的待审候选（宁 miss 修废了）"
        assert "cand-B" not in blob, f"A 读到了 B 的待审候选：{blob[:300]}"
        assert "另一份底牌" not in blob

    def test_cognitive_context_scoped_to_viewer(self, env):
        """A 调 `cognitive_context`（带 user_id）→ 上下文不含 B 的记忆。

        这个端点的用途正是「把记忆喂给 Agent」，泄漏面直接是模型上下文
        （票 .scratch/readside-gaps/03）。
        """
        session_factory, _ = env
        from lantai.models.tables import CognitiveRole

        now = datetime.now(UTC)
        with session_factory() as s:
            for mid, uid, content in (
                ("cog-A", "user-A", "A 的观测：本周发布了三个版本"),
                ("cog-B", "user-B", "B 的观测：报价底牌是 1200 万"),
            ):
                s.add(
                    __import__("lantai.models.tables", fromlist=["MemoryItem"]).MemoryItem(
                        id=mid,
                        content=content,
                        memory_type="semantic",
                        role=CognitiveRole.OBSERVATION,
                        user_id=uid,
                        lane="fact",
                        confidence=0.8,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
            s.commit()

        out = _call_ok(
            _load_mcp(),
            "cognitive_context",
            {"task": "发布安排", "as_prompt": False, "user_id": "user-A"},
        )
        blob = json.dumps(out, ensure_ascii=False)
        assert "报价底牌是 1200 万" not in blob, f"A 的认知上下文含 B 的记忆：{blob[:300]}"
        assert "本周发布了三个版本" in blob, "A 连自己的观测都没拿到（宁 miss 修废了）"

    def test_get_digest_scoped_to_viewer(self, env, tmp_path):
        """A 调 `get_digest`（带 user_id）→ stats 只统计 A 的记忆。

        票 .scratch/readside-gaps/04：`load_today_digest` 的 `stats` 按
        viewer 过滤（`collect_digest_stats(principal=...)`）。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "dig-A", "A 的记忆", user_id="user-A")
            _seed_memory(s, "dig-B", "B 的记忆", user_id="user-B")
            s.commit()

        # digest 落盘目录隔离到 tmp_path，避免污染宿主仓库
        import lantai.workers.digest_worker as dw

        with patch.object(dw, "_digest_dir", lambda: tmp_path):
            out = _call_ok(_load_mcp(), "get_digest", {"user_id": "user-A"})
        # 计数在 stats.memories 下（`total` 不是顶层键）
        memories = (out.get("stats") or {}).get("memories") or {}
        assert memories.get("total") == 1, (
            f"A 的 digest 统计了别人的记忆（该 1，实际 {memories.get('total')}）：{memories}"
        )


# ── Red 2：写侧落库行带归属 ──────────────────────────────────────


class TestWriteSideStampsOwner:
    def test_raw_add_stamps_owner(self, env):
        """A 调 `raw_add`（带 user_id）→ 落的 MemoryItem 带 `user-A`。

        `add_raw_memory` 已有 `principal` 与 `user_id` 形参（票
        ownership-gaps/03），MCP 侧没传。修前落的行 `user_id` 恒
        "default"（形参缺省）——那不是 A，A 下次读侧收窄时读不到它。
        """
        session_factory, _ = env
        vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
        with (
            patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock),
        ):
            out = _call_ok(
                _load_mcp(),
                "raw_add",
                {"content": "A 的原文：合同第七条违约责任", "user_id": "user-A"},
            )
        mem_id = out.get("id") or out.get("memory_id")
        assert mem_id, f"raw_add 没返回记忆 id：{out}"

        from lantai.models.tables import MemoryItem

        with session_factory() as s:
            row = s.get(MemoryItem, mem_id)
            assert row is not None, "raw_add 没落库"
            assert row.user_id == "user-A", f"落库行的属主是 {row.user_id!r}，不是 user-A"

    def test_scratchpad_write_stamps_owner(self, env):
        """A 调 `scratchpad_write`（带 user_id）→ 行带 `user-A`。

        票 ownership-gaps/04：`write_scratchpad` 的归属随 principal 落列。
        """
        session_factory, _ = env
        _call_ok(
            _load_mcp(),
            "scratchpad_write",
            {"session_id": "sess-A", "content": "A 的工作便签", "user_id": "user-A"},
        )

        from lantai.models.tables import SessionScratchpad

        with session_factory() as s:
            row = s.get(SessionScratchpad, "sess-A")
            assert row is not None, "scratchpad_write 没落库"
            assert row.user_id == "user-A", f"便签属主是 {row.user_id!r}，不是 user-A"

    def test_scratchpad_get_cannot_read_other_users(self, env):
        """A 读 B 的 session 便签 → 空串（不是"没有"与"不是你的"要分开）。"""
        session_factory, _ = env
        from lantai.models.tables import SessionScratchpad

        with session_factory() as s:
            s.add(SessionScratchpad(session_id="sess-B", user_id="user-B", content="B 的便签"))
            s.commit()

        out = _call_ok(_load_mcp(), "scratchpad_get", {"session_id": "sess-B", "user_id": "user-A"})
        assert out.get("content") == "", f"A 读到了 B 的便签：{out}"


# ── Red 3：结构性护栏（签名有 principal 而函数体不用 = 票 24 的形状） ──


class TestStructuralGuard:
    def test_36_handlers_all_take_principal(self):
        """本波 36 个 handler 的源码里必须出现 `_principal_from_params(params)`。

        **为什么直接查源码**：票 24 的教训——`get_single_memory` 签名里
        有 `principal=Depends(...)` 而函数体一次都没引用，代码审查看到
        `Depends` 就打勾。HTTP 断言拦不住"绕路拿到数据"的实现，直接看
        源码比只打请求强。
        """
        import inspect

        import lantai.cli.mcp as mcp_mod

        expected = {
            "handle_candidates_pending",
            "handle_candidate_review",
            "handle_candidate_refine",
            "handle_triage_analyze",
            "handle_triage_apply",
            "handle_get_digest",
            "handle_kaogong_eval",
            "handle_memory_consolidate",
            "handle_probe_detect",
            "handle_probe_resolve",
            "handle_add",
            "handle_raw_add",
            "handle_obsidian_sync",
            "handle_conflicts_list",
            "handle_conflict_resolve",
            "handle_recall_report",
            "handle_mem_recent",
            "handle_proposals_list",
            "handle_proposal_decide",
            "handle_tree_view",
            "handle_tree_add",
            "handle_tree_assign",
            "handle_crystals_list",
            "handle_crystals_detect",
            "handle_crystal_decide",
            "handle_core_memory_get",
            "handle_verbatim_search",
            "handle_graph_view",
            "handle_recall_chain",
            "handle_checkpoint_write",
            "handle_checkpoint_latest",
            "handle_cognitive_context",
            "handle_persona_get",
            "handle_persona_set",
            "handle_scratchpad_get",
            "handle_scratchpad_write",
        }
        assert len(expected) == 36

        missing = []
        for name in sorted(expected):
            fn = getattr(mcp_mod, name, None)
            if fn is None:
                missing.append(f"{name}（handler 不存在）")
                continue
            if "_principal_from_params(params)" not in inspect.getsource(fn):
                missing.append(name)
        assert not missing, f"这些 handler 仍不取身份：{missing}"

    def test_principal_from_params_untouched(self):
        """护栏：`_principal_from_params` 的"不猜身份"语义不许被改。

        它的 docstring 明写不看环境变量/进程名/session_id 推导——那是
        宁 miss 不脏写的地基。若有人为了"让身份总能取到"而加 fallback，
        等于把别人的查询词挂到另一个人头上，比 NULL 更难排查。
        """
        import inspect

        import lantai.cli.mcp as mcp_mod

        src = inspect.getsource(mcp_mod._principal_from_params)
        assert "os.environ" not in src, "_principal_from_params 开始看环境变量了（猜身份）"
        assert "sys.argv" not in src, "_principal_from_params 开始看进程名了（猜身份）"


# ── Red 4：回归护栏——不透传 user_id 时行为不变 ────────────────────


class TestNoIdentityStillWorks:
    def test_mem_recent_without_user_id_unchanged(self, env):
        """宿主不透传 `user_id` → `principal=None` → 与改动前逐字一致。

        **这一条是护栏不是漏洞**：收紧它是 02 号票（MCP 身份契约）的
        范围。本票只接线，不改 None 的语义——否则现有 MCP 客户端
        （普遍不传 user_id）会被一次性全打断。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "m1", "发布会安排下周", user_id="default")
            _seed_memory(s, "m2", "已归档旧事", user_id="default", status="archived")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_recent", {"limit": 10})
        assert [m["id"] for m in out["memories"]] == ["m1"]
