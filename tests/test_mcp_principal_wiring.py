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

    def test_mem_stats_scoped_to_viewer(self, env):
        """A 调 `mem_stats`（带 user_id）→ 计数只统计 A 的记忆。

        票 .scratch/readside-gaps/05 修了 `/monitor/overview`（同一批
        计数），但 `get_overview()` 自己**没往下传 principal**——于是 MCP
        的 `mem_stats` 无论宿主传不传 user_id 都是全量计数。A 能看见
        B 制造了多少记忆、积压了多少待审。本票补这根线。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "ov-A-1", "A 的记忆一", user_id="user-A")
            _seed_memory(s, "ov-A-2", "A 的记忆二", user_id="user-A")
            _seed_memory(s, "ov-B-1", "B 的记忆", user_id="user-B")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_stats", {"user_id": "user-A"})
        memories = out.get("memories") or {}
        assert memories.get("total") == 2, (
            f"A 的概览统计了别人的记忆（该 2，实际 {memories.get('total')}）：{memories}"
        )
        assert memories.get("active") == 2

    def test_mem_stats_without_user_id_unchanged(self, env):
        """护栏：不透传 user_id → `principal=None` → 全量计数不变。

        `build_overview` 的 `None` 语义是「全量」（票 05 定死：内部
        worker/MCP/脚本不带身份，一过滤就空转）。收紧它是 02 号票的事。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "ov-A-3", "A 的记忆", user_id="user-A")
            _seed_memory(s, "ov-B-2", "B 的记忆", user_id="user-B")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_stats", {})
        assert (out.get("memories") or {}).get("total") == 2


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
    def test_handlers_all_take_principal(self):
        """**全部** handler 要么取了身份，要么在豁免清单里且写明理由。

        **为什么不数人给的 expected 集合**：01b 收尾时发现这个护栏原本
        数的是一个手写的 `expected` 集合——集合本身是人从普查表誊的，
        **誊漏了就查不出来**。01a/01b 两波合起来誊漏了 4 个 handler
        （`triage_auto_pilot` / `consolidation_report` / `backfill` /
        `mem_health`），护栏全程绿。改成机械枚举 `mcp.py` 里所有
        `def handle_`，逐个要求「取身份」或「在 EXEMPT 里有理由」——
        新增 handler 忘了接线会直接红，不需要有人记得更新集合。

        票 24 的教训仍在：`get_single_memory` 签名里有
        `principal=Depends(...)` 而函数体一次都没引用，代码审查看到
        `Depends` 就打勾。所以查的是**函数体源码**，不是签名。
        """
        import inspect
        import re
        from pathlib import Path

        import lantai.cli.mcp as mcp_mod

        # 豁免清单：逐个写明「为什么不取身份」。**空理由不许进。**
        EXEMPT = {
            # 身份来源就是 params.user_id 本身（ingest_dialogue /
            # submit_async_dialogue 都收 user_id 形参），再取一次 Principal
            # 是两套口径——宿主不透传时任务归 "default"，与
            # _principal_from_params 的 None 收敛到同一个 viewer
            "handle_add_dialogue": "身份来源是 params.user_id 本身",
            "handle_dialogue_add_async": "身份来源是 params.user_id 本身",
            # 纯函数：返回静态命令表，不碰库
            "handle_mem_help": "纯函数，不碰库",
            # 运维探针：宿主机挂了要能看出来，memory_count 语义上就是全库
            # （同 build_overview 保留全量口径，内部 worker 不能被打断）
            "handle_mem_health": "运维探针，全库计数是既定语义",
            # 以下三个是 01c 票范围内的真缺口，尚未修——留在这里是为了
            # 让「未修」是显式的、有人 review 的，而不是默默漏掉。
            # 01c 修完一个删一个。
            "handle_triage_auto_pilot": "01c：run_triage_auto_pilot 丢了 principal",
            "handle_backfill": "01c：RetrievalEvent 有 user_id 列但没比对",
            "handle_consolidation_report": "01c：读侧无归属口径（计数-only，P2）",
        }

        src = Path(mcp_mod.__file__).read_text(encoding="utf-8")
        all_handlers = set(re.findall(r"^def (handle_\w+)", src, re.M))
        assert len(all_handlers) >= 55, f"handler 数量异常偏少（{len(all_handlers)}），正则失效了？"

        took_identity, exempted, unexplained = [], [], []
        for name in sorted(all_handlers):
            fn = getattr(mcp_mod, name, None)
            if fn is None:
                unexplained.append(f"{name}（正则匹配到但模块里没有）")
                continue
            body = inspect.getsource(fn)
            if "_principal_from_params(params)" in body:
                took_identity.append(name)
            elif name in EXEMPT:
                exempted.append(name)
            else:
                unexplained.append(name)

        assert not unexplained, (
            f"这些 handler 既没取身份、也不在豁免清单里（新增 handler 忘了接线？）：{unexplained}"
        )
        # 豁免清单里不许有已经不存在/已接线的僵尸条目
        stale = sorted(set(EXEMPT) - all_handlers)
        assert not stale, f"豁免清单里有不存在的 handler（清理僵尸条目）：{stale}"
        for name in sorted(set(EXEMPT) & set(took_identity)):
            raise AssertionError(f"{name} 已取身份，请从豁免清单删除")

        assert len(took_identity) + len(exempted) == len(all_handlers)

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


# ── 第二波（01b）：service 层补 principal 的 13 个 handler ──────────
#
# 与第一波的区别：第一波下游 service 已有 principal（23 张票的成果），
# MCP 只差一根线；这一波是 service 自己也没有——要**先补 service 的
# 形参与收窄，再接 MCP 的线**，所以回归面更大（HTTP 路由与 worker
# 共用这些 service）。每条用例都同时打 MCP 入口 + 直调 service，
# 确保两层都收窄。


def _seed_scene(s, scene_id, name, *, members):
    """建场景 + 成员记忆（members = [(mid, content, user_id), ...]）。"""
    from lantai.models.tables import MemoryItem, MemoryScene

    now = datetime.now(UTC)
    s.add(
        MemoryScene(
            id=scene_id,
            name=name,
            summary=f"{name}的摘要",
            heat=10,
            member_count=len(members),
            centroid=[0.1, 0.2],
            created_at=now,
            updated_at=now,
        )
    )
    for mid, content, uid in members:
        _seed_memory(s, mid, content, user_id=uid)
        s.get(MemoryItem, mid).scene_id = scene_id


class TestReviveConsolidatedScoped:
    def test_revive_others_memory_rejected_and_nothing_written(self, env):
        """A 起复 B 的巩固产物 → 403，且**什么都没落库**。

        票 01b：`revive_consolidated` 是破坏性写（主记忆形态会改写整簇：
        全部 consolidated 碎片恢复 active + 删 supersedes 边）。校验下沉到
        service（HTTP 侧顺带受益，不另造判据），403 必须发生在任何
        commit 之前。
        """
        session_factory, _ = env
        from lantai.models.tables import MemoryItem

        with session_factory() as s:
            # B 的主记忆（active）+ 一片巩固碎片（consolidated）
            _seed_memory(s, "rev-main-B", "B 的主记忆", user_id="user-B")
            _seed_memory(
                s,
                "rev-frag-B",
                "B 的碎片",
                user_id="user-B",
                status="consolidated",
            )
            s.commit()

        # 拒绝的形态：service 抛 HTTPException(403)，MCP 的 handle() 把
        # 非 ValueError 一律收成 -32603（`-32603 internal error:
        # HTTPException`）——所以断言"有 error + 码是 -32603"
        resp = _call(
            _load_mcp(),
            "revive_consolidated",
            {"memory_id": "rev-frag-B", "reason": "我想看看这条", "user_id": "user-A"},
        )
        assert "error" in resp, f"A 起复 B 的记忆没被拒：{resp}"
        assert resp["error"]["code"] == -32603
        # 断言未落库：碎片仍是 consolidated，supersedes 边没被删
        with session_factory() as s:
            frag = s.get(MemoryItem, "rev-frag-B")
            assert frag.status == "consolidated", (
                f"403 之后碎片状态仍被改写了（{frag.status}）——校验没在写操作之前"
            )

    def test_revive_own_memory_allowed(self, env):
        """回归护栏：A 起复自己的碎片 → 正常恢复 active。"""
        session_factory, _ = env
        from lantai.models.tables import MemoryItem

        with session_factory() as s:
            _seed_memory(s, "rev-main-A", "A 的主记忆", user_id="user-A")
            _seed_memory(s, "rev-frag-A", "A 的碎片", user_id="user-A", status="consolidated")
            s.commit()

        out = _call_ok(
            _load_mcp(),
            "revive_consolidated",
            {"memory_id": "rev-frag-A", "reason": "恢复碎片", "user_id": "user-A"},
        )
        assert out.get("ok") is True, f"A 起复自己的碎片失败：{out}"
        with session_factory() as s:
            assert s.get(MemoryItem, "rev-frag-A").status == "active"


class TestMemUsageScoped:
    def test_mem_usage_scoped_to_viewer(self, env):
        """A 调 `mem_usage`（带 user_id）→ 只统计 A 的记忆数。

        修前 `collect_usage` 全表 GROUP BY——A 能看见 B 每天写了多少
        记忆（行为画像素材：某天突然高产往往对应某件事）。
        """
        session_factory, _ = env
        with session_factory() as s:
            from lantai.models.tables import MemoryItem

            for i in range(3):
                _seed_memory(s, f"us-A-{i}", f"A 的第 {i} 条", user_id="user-A")
            for i in range(5):
                _seed_memory(s, f"us-B-{i}", f"B 的第 {i} 条", user_id="user-B")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_usage", {"days": 7, "user_id": "user-A"})
        daily = out.get("daily_new") or {}
        total = sum(daily.values())
        assert total == 3, f"A 的用量统计了别人的记忆（该 3，实际 {total}）：{daily}"

    def test_mem_usage_null_owner_rows_visible(self, env):
        """护栏：NULL 属主的老行对任何 viewer 可见（`== viewer OR IS NULL`）。

        单人部署下真实库 650 行里 629 行 NULL——判不可见会让报表归零，
        那是修废不是收窄。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "us-null-1", "无属主老行", user_id=None)
            _seed_memory(s, "us-B-1", "B 的记忆", user_id="user-B")
            s.commit()

        out = _call_ok(_load_mcp(), "mem_usage", {"days": 7, "user_id": "user-A"})
        total = sum((out.get("daily_new") or {}).values())
        assert total == 1, f"NULL 老行该可见、B 的该不可见（该 1，实际 {total}）"


class TestAutodreamScoped:
    def test_autodream_report_cluster_does_not_cross_users(self, env):
        """A 调 `autodream_report` → 聚类输入不含 B 的记忆。

        修前 `select(MemoryItem)` 全表 active——A 与 B 的记忆会聚成同一个
        簇，蒸馏出的提案 `content` 是两条记忆正文的拼接
        （`proposed_patch.content`），A 一次调用就读到 B 的原文。

        **种子必须让"A 自己能成簇"**：`AUTODREAM_MIN_CLUSTER=2`，所以 A
        要 2 条以上共享关键词 + 同 lane 的记忆。修前 B 的那 2 条会并进
        同一个簇（同关键词同 lane），修后只剩 A 的 2 条——**决定性**。
        """
        session_factory, _ = env
        with session_factory() as s:
            for i in (1, 2):
                _seed_memory(
                    s, f"ad-A-{i}", "数据库迁移要备份再执行", user_id="user-A", lane="fact"
                )
            for i in (1, 2):
                _seed_memory(
                    s, f"ad-B-{i}", "数据库迁移要备份再执行", user_id="user-B", lane="fact"
                )
            s.commit()

        from lantai.core.settings import settings

        if not settings.AUTODREAM_ENABLED:
            pytest.skip("AUTODREAM_ENABLED=false")
        out_mcp = _call_ok(_load_mcp(), "autodream_report", {"user_id": "user-A"})
        assert out_mcp.get("clusters", 0) >= 1, f"A 自己的记忆没聚成簇（宁 miss 修废了）：{out_mcp}"

        # 决定性断言必须来自**产物**，不能来自测试自己筛数据（那是恒真）。
        # `run_autodream_once` 的 plans 只是计数，唯一暴露簇内容的出口是
        # dry_run=False 落库的 MemoryProposal——`proposed_patch.content`
        # 是簇内记忆正文的拼接。所以真跑一轮，查落库提案。
        from lantai.core.auth import Principal
        from lantai.evolution.autodream import run_autodream_once

        trigger = run_autodream_once(dry_run=False, principal=Principal(user_id="user-A"))
        assert trigger["created"] >= 1, f"A 没有产出提案（宁 miss 修废了）：{trigger}"

        from lantai.models.tables import MemoryProposal

        with session_factory() as s:
            from sqlmodel import select as _select

            proposals = s.exec(_select(MemoryProposal)).all()
        assert proposals, "autodream_trigger 没落提案"
        for p in proposals:
            evidence = list(p.evidence_ids or [])
            assert not any(i.startswith("ad-B") for i in evidence), (
                f"A 的蒸馏簇里混进了 B 的记忆：{evidence}"
            )
            blob = json.dumps(p.proposed_patch or {}, ensure_ascii=False)
            assert "ad-B" not in blob, f"提案正文里出现了 B 的记忆 id：{blob[:300]}"

    def test_autodream_null_owner_rows_clustered(self, env):
        """护栏：NULL 老行仍参与聚类（单人部署下不空转）。"""
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "ad-null", "数据库迁移要备份再执行", user_id=None, lane="fact")
            _seed_memory(s, "ad-A2", "数据库迁移要备份再执行", user_id="user-A", lane="fact")
            s.commit()

        from lantai.core.settings import settings

        if not settings.AUTODREAM_ENABLED:
            pytest.skip("AUTODREAM_ENABLED=false")
        out = _call_ok(_load_mcp(), "autodream_report", {"user_id": "user-A"})
        assert out.get("clusters", 0) >= 1, f"NULL 老行没参与聚类，单人部署下蒸馏空转：{out}"


class TestCreateSkillScoped:
    def test_create_skill_stamps_owner(self, env):
        """A 调 `mem_create_skill`（带 user_id）→ 落的 skill 行带 `user-A`。

        修前四元组一个都不填，`user_id` 恒 NULL——按 viewer 收窄的读侧
        （`fts.py` 的 `AND m.user_id = ?`）把这类技能**全部滤掉**，
        等于沉淀了却检索不到。
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
                "mem_create_skill",
                {
                    "name": "数据库迁移",
                    "description": "迁移步骤",
                    "steps": ["备份", "执行", "验证"],
                    "user_id": "user-A",
                },
            )
        mem_id = out.get("memory_id")
        assert mem_id, f"mem_create_skill 没返回 id：{out}"

        from lantai.models.tables import MemoryItem

        with session_factory() as s:
            row = s.get(MemoryItem, mem_id)
            assert row is not None, "技能没落库"
            assert row.user_id == "user-A", f"技能属主是 {row.user_id!r}，不是 user-A"

    def test_create_skill_dedup_scoped_to_owner(self, env):
        """A 与 B 沉淀同名技能 → 各落各的（不是 A 拿到 B 的 memory_id）。

        票 readside-gaps/20 同款：去重查询不按归属收窄时，A 提交与 B
        完全相同的内容会返回 B 的 memory_id——A 从此"拥有"一条 B 的技能。
        """
        session_factory, _ = env
        vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
        args = {"name": "发布检查", "description": "上线前", "steps": ["跑测试", "看日志"]}
        with (
            patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock),
        ):
            out_b = _call_ok(_load_mcp(), "mem_create_skill", {**args, "user_id": "user-B"})
            out_a = _call_ok(_load_mcp(), "mem_create_skill", {**args, "user_id": "user-A"})
        assert out_b.get("dedup") is False and out_a.get("dedup") is False, (
            f"同名技能被跨用户去重了：B={out_b} A={out_a}"
        )
        assert out_a["memory_id"] != out_b["memory_id"]


class TestDialogueTaskStatusScoped:
    def test_task_status_other_users_task_not_found(self, env):
        """A 查 B 的 task_id → `not_found`（不是"有状态但不是你的"）。

        `_TASKS` 是进程内字典，`result` 带着摄取结果的完整 payload
        （含提取出的候选正文）。区分「没有」与「不是你的」本身就是
        信息泄漏——能据此探知某 task_id 是否存在。
        """
        from lantai.services import async_ingest_service as ais

        ais.clear_tasks()
        try:
            ais.submit_async_dialogue("B 的对话内容", user_id="user-B", source="dialogue")
            tid = next(iter(ais._TASKS))
            out = _call_ok(
                _load_mcp(), "dialogue_task_status", {"task_id": tid, "user_id": "user-A"}
            )
            assert out.get("status") == "not_found", (
                f"A 查到了 B 的任务（该 not_found）：status={out.get('status')}"
            )
            assert "result" not in out, f"返回里带了 B 的提取结果：{list(out)}"
        finally:
            ais.clear_tasks()

    def test_task_status_own_task_visible(self, env):
        """回归护栏：A 查自己的 task_id → 能看到（不能修废）。"""
        from lantai.services import async_ingest_service as ais

        ais.clear_tasks()
        try:
            ais.submit_async_dialogue("A 的对话内容", user_id="user-A", source="dialogue")
            tid = next(iter(ais._TASKS))
            out = _call_ok(
                _load_mcp(), "dialogue_task_status", {"task_id": tid, "user_id": "user-A"}
            )
            assert out.get("status") in ("queued", "processing", "completed", "failed"), (
                f"A 查不到自己的任务：{out}"
            )
        finally:
            ais.clear_tasks()


class TestOffloadReadScoped:
    def test_offload_read_others_memory_rejected(self, env, tmp_path, monkeypatch):
        """A 报 B 的 memory_id 读卸载全文 → 拒绝。

        卸载目录里躺着的是**完整原文**。判据按 memory_id 反查
        `MemoryItem.user_id`（文件系统没有归属列，同
        `resolve_probe_response` 对 ConflictEvent 的过渡推导）。
        """
        session_factory, _ = env
        from lantai.services import offload_service

        monkeypatch.setattr(offload_service, "offload_dir", lambda: tmp_path)
        with session_factory() as s:
            _seed_memory(s, "off-B", "B 的完整原文：收购报价底牌 1200 万", user_id="user-B")
            s.commit()
        offload_service.write_offload_file("off-B", "B 的完整原文：收购报价底牌 1200 万")

        resp = _call(_load_mcp(), "offload_read", {"memory_id": "off-B", "user_id": "user-A"})
        # 拒绝走 -32603 internal error: FileNotFoundError（宁 miss：不可见）
        assert "error" in resp, f"A 读到了 B 的卸载全文：{resp}"
        assert "1200 万" not in json.dumps(resp, ensure_ascii=False)

    def test_offload_read_own_memory_allowed(self, env, tmp_path, monkeypatch):
        """回归护栏：A 读自己的卸载全文 → 正常返回。"""
        session_factory, _ = env
        from lantai.services import offload_service

        monkeypatch.setattr(offload_service, "offload_dir", lambda: tmp_path)
        with session_factory() as s:
            _seed_memory(s, "off-A", "A 的完整原文", user_id="user-A")
            s.commit()
        offload_service.write_offload_file("off-A", "A 的完整原文")

        out = _call_ok(_load_mcp(), "offload_read", {"memory_id": "off-A", "user_id": "user-A"})
        assert out.get("content") == "A 的完整原文", f"A 读不到自己的全文：{out}"

    def test_offload_read_orphan_file_rejected(self, env, tmp_path, monkeypatch):
        """孤儿卸载文件（记忆已删）→ 拒绝，即使带着身份。

        判据是「反查不到 MemoryItem 就不可见」——否则删了记忆但文件还在，
        等于留了一个没有归属判据的泄漏面。
        """
        session_factory, _ = env
        from lantai.services import offload_service

        monkeypatch.setattr(offload_service, "offload_dir", lambda: tmp_path)
        offload_service.write_offload_file("off-orphan", "没人认领的完整原文")

        resp = _call(_load_mcp(), "offload_read", {"memory_id": "off-orphan", "user_id": "user-A"})
        assert "error" in resp, f"孤儿文件被读走了：{resp}"
        # 断言**错误类型**而不只是"有错误"：把 `raise` 换成 `item = None`
        # 的变异体同样会报错（AttributeError），只断言有 error 分不清
        # "按设计拒绝"与"撞崩了"
        assert "FileNotFoundError" in resp["error"]["message"], (
            f"孤儿文件的拒绝形态不对（该 FileNotFoundError）：{resp['error']}"
        )
        assert "没人认领" not in json.dumps(resp, ensure_ascii=False)


class TestWikiReadScoped:
    def _make_wiki(self, tmp_path, monkeypatch):
        from lantai.services import wiki_service

        monkeypatch.setattr(wiki_service, "wiki_dir", lambda: tmp_path)
        return tmp_path / "pages"

    def test_wiki_read_others_scene_page_rejected(self, env, tmp_path, monkeypatch):
        """A 报 B 的场景 slug 读 wiki 页 → 拒绝。

        wiki 页是渲染产物，正文里有成员记忆的截断片段 + 场景摘要。
        判据按来源反查：场景页看 `_scene_visible_member_ids`。
        """
        session_factory, _ = env
        self._make_wiki(tmp_path, monkeypatch)
        from lantai.services import wiki_service

        with session_factory() as s:
            _seed_scene(
                s,
                "scene-B",
                "B 的并购场景",
                members=[("wk-B-1", "B 的记忆：报价底牌", "user-B")],
            )
            s.commit()
        wiki_service.run_wiki_update_once(overview_llm=False)

        resp = _call(_load_mcp(), "wiki_read", {"slug": "B 的并购场景", "user_id": "user-A"})
        assert "error" in resp, f"A 读到了 B 的 wiki 页：{resp}"
        assert "报价底牌" not in json.dumps(resp, ensure_ascii=False)

    def test_wiki_read_own_scene_page_allowed(self, env, tmp_path, monkeypatch):
        """回归护栏：A 读自己场景的 wiki 页 → 正常返回。"""
        session_factory, _ = env
        self._make_wiki(tmp_path, monkeypatch)
        from lantai.services import wiki_service

        with session_factory() as s:
            _seed_scene(
                s,
                "scene-A",
                "A 的发布场景",
                members=[("wk-A-1", "A 的记忆：下周发版", "user-A")],
            )
            s.commit()
        wiki_service.run_wiki_update_once(overview_llm=False)

        out = _call_ok(_load_mcp(), "wiki_read", {"slug": "A 的发布场景", "user_id": "user-A"})
        assert "A 的发布场景" in out.get("content", ""), f"A 读不到自己的 wiki 页：{out}"


class TestScenesScoped:
    def test_scene_get_others_scene_not_found(self, env):
        """A 下钻纯 B 的场景 → `scene not found`（不区分"没有"与"不是你的"）。"""
        session_factory, _ = env
        with session_factory() as s:
            _seed_scene(
                s,
                "scene-only-B",
                "B 的私密场景",
                members=[("sc-B-1", "B 的记忆：底牌", "user-B")],
            )
            s.commit()

        resp = _call(_load_mcp(), "scene_get", {"scene_id": "scene-only-B", "user_id": "user-A"})
        assert "error" in resp, f"A 下钻到了 B 的场景：{resp}"
        assert "底牌" not in json.dumps(resp, ensure_ascii=False)

    def test_scene_get_own_scene_shows_only_own_members(self, env):
        """A 下钻混簇（A + B 成员）→ 场景可见，但 members 只含 A 的。

        聚类是产物，混簇本身要靠重建解决（票 02）；本票保证的是
        **A 的成员不被过滤掉**（宁 miss 不脏写）且 B 的正文不外泄。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_scene(
                s,
                "scene-mixed",
                "混合场景",
                members=[
                    ("sc-A-1", "A 的记忆：发版", "user-A"),
                    ("sc-B-1", "B 的记忆：底牌", "user-B"),
                ],
            )
            s.commit()

        out = _call_ok(_load_mcp(), "scene_get", {"scene_id": "scene-mixed", "user_id": "user-A"})
        ids = [m["id"] for m in out.get("members") or []]
        assert "sc-A-1" in ids, f"A 下钻自己的混簇却看不到自己的成员：{ids}"
        assert "sc-B-1" not in ids, f"混簇下钻把 B 的成员正文带出来了：{ids}"

    def test_scenes_list_scoped_to_viewer(self, env):
        """A 调 `scenes_list` → 只列有自己成员的场景。

        `summary` 是 LLM 依据成员内容生成的摘要——A 刷列表就读到 B 的
        场景主题画像。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_scene(s, "scene-ls-A", "A 的场景", members=[("ls-A-1", "A 的记忆", "user-A")])
            _seed_scene(s, "scene-ls-B", "B 的场景", members=[("ls-B-1", "B 的记忆", "user-B")])
            # NULL 属主的老场景：单人部署下必须仍可见（`OR IS NULL`）
            _seed_scene(s, "scene-ls-null", "无主老场景", members=[("ls-null-1", "老记忆", None)])
            s.commit()

        out = _call_ok(_load_mcp(), "scenes_list", {"limit": 50, "user_id": "user-A"})
        names = [sc["name"] for sc in out.get("scenes") or []]
        assert "A 的场景" in names, f"A 看不到自己的场景（宁 miss 修废了）：{names}"
        assert "无主老场景" in names, f"NULL 老场景被过滤掉了（单人部署下场景全空）：{names}"
        assert "B 的场景" not in names, f"A 的列表里有 B 的场景：{names}"


class TestMemSyncScoped:
    def test_mem_sync_digest_scoped_to_viewer(self, env, tmp_path):
        """A 调 `mem_sync` → digest 快照只统计 A 的记忆。

        修前 `run_digest_once()` 不带身份 → 全库统计。`mem_sync` 的返回
        里带着整个 digest stats（`digest.stats`）。
        """
        session_factory, _ = env
        with session_factory() as s:
            _seed_memory(s, "sy-A-1", "A 的记忆一", user_id="user-A")
            _seed_memory(s, "sy-A-2", "A 的记忆二", user_id="user-A")
            _seed_memory(s, "sy-B-1", "B 的记忆", user_id="user-B")
            s.commit()

        import lantai.workers.digest_worker as dw

        with patch.object(dw, "_digest_dir", lambda: tmp_path):
            out = _call_ok(_load_mcp(), "mem_sync", {"user_id": "user-A"})
        digest = out.get("digest") or {}
        stats = digest.get("stats") or {}
        memories = stats.get("memories") or {}
        assert memories.get("total") == 2, (
            f"A 的 mem_sync 统计了别人的记忆（该 2，实际 {memories.get('total')}）：{stats}"
        )
