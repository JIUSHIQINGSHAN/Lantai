"""票 04（P0-3）：Hermes 插件短查询常驻画像注入 + serve 闸门会话归属。

不 mock 冒烟（AGENTS.md）：真实 SQLite（StaticPool）+ 真实 FTS 索引 + 真实
`_handle_one` 协议帧 + 真实 `relevance_check` / `build_context` / 画像三段。
只 mock 外部网络（embed / 向量库 / intent LLM）。插件用真实模块实例，仅把
「serve 子进程管道」换成进程内 `_handle_one` 直调——换 I/O，不换判定逻辑。

三面验收：
1. 短问句（含 2 字边界）必须拿到常驻画像；插件侧「≤15 字且无触发词就跳过」
   已删除（续审 2026-10-31 P0-3），不得复活，也不得留尸体。
2. 空画像 / 1 字消息 / 无关短消息不得伪造注入，也不得强注检索噪声。
3. serve 闸门的追问热缓存必须按**会话**归属：`build_context` 此前调
   `relevance_check(query)` 不带任何身份，落到进程级全局 `_LAST_GATE_DECISION`，
   于是 B 会话的短消息会继承 A 会话的判定（reason=`session_followup_hot`），
   白跑昂贵检索并把 A 的记忆噪声注进 B。身份（session_id）本就在调用处手里。
"""

import importlib.util
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem
from lantai.storage.fts import sync_fts

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = str(ROOT / "scripts" / "shell_hook.py")
PLUGIN_PATH = str(ROOT / "hermes-plugin" / "lantai-hook" / "__init__.py")

# 哨兵串：与默认人格基座文本（其认知底色里写着「华硕天选三 RTX 3050」）严格不重叠，
# 否则画像段会被误当成检索噪声（本票排查中真踩过这个假阳性）。
PROFILE_MARK = "档案哨兵·青梧巷"
MEM_MARK = "青梧仓库盘点截止日"
MEM_Q = "青梧仓库每月的盘点截止日是哪一天"


def hook_fts_db():
    """检索链的 DB 绑定（与 db_module 同一单例，显式点名以免读者误以为漏了范围）。"""
    from lantai.retrieval import hybrid

    return hybrid.db


def _load_hook():
    spec = importlib.util.spec_from_file_location("_hook04_isolated", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_plugin():
    spec = importlib.util.spec_from_file_location("_plugin04_isolated", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def serve(monkeypatch, tmp_path):
    """真实 serve 路径 + 隔离 DB/文件系统；(mod, seed) 形态，seed 由用例填。"""

    def _build(*, with_persona=True, with_memories=True):
        import lantai.models.tables  # noqa: F401 — 触发 metadata 注册
        from lantai.storage.fts import init_fts

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            init_fts(conn.connection.driver_connection)  # FTS5 虚表不在 metadata 里

        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        if with_persona:
            from lantai.services.persona_service import ensure_default_persona

            (home / "user_profile.md").write_text(
                f"{PROFILE_MARK}：用户对花生过敏，长期偏好乌龙茶。", encoding="utf-8"
            )
            with Session(engine) as s:
                ensure_default_persona(s)
                s.commit()

        if with_memories:
            with Session(engine) as s:
                m = MemoryItem(
                    id="mem-04-a",
                    content=f"{MEM_MARK}是每月 26 号，负责人是老魏。",
                    lane="fact",
                    tier="longterm",
                    status="active",
                    importance=0.8,
                )
                s.add(m)
                s.commit()
                sync_fts(s, m.id, m.content)
                s.commit()

        mod = _load_hook()
        monkeypatch.setattr(mod.settings, "API_KEY", "test-04-key")
        monkeypatch.setattr(mod.settings, "RERANKER_ENABLED", False)
        monkeypatch.setattr(mod.settings, "LANTAI_HOME", str(home))
        # 生产全局的复位交给 monkeypatch（票 01 纪律：不留脏值给后续用例）
        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
        monkeypatch.setattr(hook_fts_db(), "get_session", lambda: Session(engine))
        monkeypatch.delenv("LANTAI_HOST", raising=False)
        return mod, engine

    return _build


@contextmanager
def _network_off(mod):
    """只关外部网络（embed / 向量库 / intent LLM），检索本体（BM25/FTS）真实跑。"""
    with (
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[])),
        ),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "candidate_n": 10},
        ),
        patch.object(mod, "embed", return_value=[[0.1] * 8]),
    ):
        yield


def _frame(mod, query, session_id=""):
    """真实协议帧 → 真实 parse/dispatch/超时包装 → 真实 build_context。"""
    return mod._handle_one(
        json.dumps({"query": query, "session_id": session_id}, ensure_ascii=False)
    )


def _bridge_plugin(plugin, mod, calls=None):
    """插件的真实回调 + 进程内 serve（只替换子进程管道这一段 I/O）。"""

    def _call_hook(query, session_id=""):
        if calls is not None:
            calls.append((session_id, query))
        out = _frame(mod, query, session_id)
        return out if isinstance(out, dict) and out.get("context") else None

    plugin._call_hook = _call_hook
    plugin._call_checkpoint = lambda: None
    plugin._call_backfill = lambda *a, **k: None
    return plugin


class TestShortQueryPersonaRealServe:
    """验收面 1/2：短问句画像注入真的生效（不 mock 判定）。"""

    def test_short_persona_question_gets_persona(self, serve):
        mod, _ = serve()
        with _network_off(mod):
            out = _frame(mod, "我对什么过敏？", "s-p1")
        ctx = out.get("context", "")
        assert PROFILE_MARK in ctx, f"短问句丢了常驻画像: {ctx!r}"

    def test_two_char_boundary_query_gets_persona(self, serve):
        """边界：serve 阈值 3（`<=` 即早退）与插件阈值 2 之间的 2 字消息仍须拿到画像。"""
        assert settings.SHELL_HOOK_MIN_CHARS == 3  # 冻结值，本用例只读不改
        mod, _ = serve()
        with _network_off(mod):
            out = _frame(mod, "多高", "s-p2")
        assert PROFILE_MARK in out.get("context", "")

    def test_normal_length_query_gets_persona_and_retrieval(self, serve):
        """对照：正常长度相关问句——画像与检索**都**在（画像段不因检索存在而丢）。"""
        mod, _ = serve()
        with _network_off(mod):
            out = _frame(mod, MEM_Q, "s-p3")
        ctx = out.get("context", "")
        assert PROFILE_MARK in ctx, "长查询反而丢了画像"
        assert MEM_MARK in ctx, f"真实 FTS/BM25 检索未命中种子记忆: {ctx!r}"

    def test_empty_persona_short_query_returns_empty(self, serve):
        """空画像 + 短查询：不裸奔声明行、不伪造注入；event_id 照带出（v0.68 票 01）。"""
        mod, _ = serve(with_persona=False, with_memories=False)
        with _network_off(mod):
            out = _frame(mod, "我对什么过敏？", "s-p4")
        # 画像/注入内容必须为空；event_id 是协议簿记字段（零命中检索事件的
        # 否决链入口），不算伪造注入——「空响应」契约自 v0.68 票 01 起收窄
        assert not out.get("context"), f"无画像时短查询不得带注入内容: {out!r}"
        assert "fence" not in str(out).lower() and "【" not in str(out), out
        assert out.get("event_id"), f"零命中事件的 event_id 应带出: {out!r}"

    def test_irrelevant_short_closer_does_not_pull_memories(self, serve):
        """冷缓存下的无关短消息：只给画像，不得把记忆噪声塞进去。"""
        mod, _ = serve()
        with _network_off(mod):
            out = _frame(mod, "谢谢", "s-p5")
        ctx = out.get("context", "")
        assert PROFILE_MARK in ctx, "结束语仍应有常驻画像"
        assert MEM_MARK not in ctx, f"无关短消息被强注检索噪声: {ctx!r}"


class TestGateFollowupCacheIsSessionScoped:
    """验收面 3（本单修复本体）：追问热缓存按会话归属，不得跨会话继承。"""

    def test_other_session_short_followup_does_not_run_retrieval(self, serve):
        """跨会话不得继承判定：B 的无关短追问不该因为 A 刚问过就白跑检索。

        可观测处 = `RetrievalEvent` 埋点只在**检索分支真的执行**时落库（被闸门
        拦下的 `_persona_only()` 路径不写事件），所以「B 有没有检索事件」是直接
        证据。A 会话同批必须有事件作对照——否则「零事件」可能只是埋点失效，
        那条断言就成了假绿。
        """
        from sqlmodel import select

        from lantai.models.tables import RetrievalEvent

        mod, engine = serve()
        with _network_off(mod):
            a = _frame(mod, MEM_Q, "sess-A")
            # 「你先看看」4 字（越过 serve 的 `<= 3` 早退门）且自身无任何信号：
            # 冷缓存下闸门必判 no_signal，只有**继承**别人才会放行检索
            b = _frame(mod, "你先看看", "sess-B")

        assert MEM_MARK in a.get("context", ""), "前置对照失效：A 会话没检索命中"
        assert PROFILE_MARK in b.get("context", ""), "B 会话的短消息仍必须拿到常驻画像"
        with Session(engine) as s:
            ev_a = s.exec(select(RetrievalEvent).where(RetrievalEvent.session_id == "sess-A")).all()
            ev_b = s.exec(select(RetrievalEvent).where(RetrievalEvent.session_id == "sess-B")).all()
        assert ev_a, "前置对照失效：A 会话未落检索事件，零事件断言失去意义"
        assert not ev_b, f"B 会话继承了 A 会话的闸门判定，白跑检索并落事件：{len(ev_b)} 条"

    def test_same_session_hot_cache_repeats_within_session(self, serve):
        """同会话：第一次带信号 → 第二次短追问确实沿用（热语义活着）。"""
        import lantai.gate.prefilter as pf

        mod, _ = serve()
        with _network_off(mod):
            _frame(mod, MEM_Q, "sess-K")
            d = mod.relevance_check("你先看看", user_id="sess-K")
        assert d["reason"] == "session_followup_hot", f"同会话沿用被修没了: {d}"
        assert pf._USER_GATE_DECISIONS["sess-K"]["needs_memory"] is True

    def test_hook_path_does_not_write_keyless_global_cache(self, serve):
        """带 session 的注入帧不得再写无人身份的全局桶（跨会话污染的根）。"""
        import lantai.gate.prefilter as pf

        mod, _ = serve()
        with _network_off(mod):
            _frame(mod, MEM_Q, "sess-G")
        assert pf._LAST_GATE_DECISION["query"] == "", (
            f"注入路径仍在写进程级无身份闸门桶: {pf._LAST_GATE_DECISION!r}"
        )

    def test_frame_without_session_keeps_default_bucket(self, serve):
        """无 session 的调用方（桌面/CLI 单用户）行为零漂移：仍落 default 全局桶。"""
        import lantai.gate.prefilter as pf

        mod, _ = serve()
        with _network_off(mod):
            _frame(mod, MEM_Q, "")
        assert pf._LAST_GATE_DECISION["query"] == MEM_Q


class TestPersonaSurvivesDegradedPaths:
    """评审清单 D-04 的另一半（本单第二处生产缺陷）：异常路径也在丢画像。

    `_run_with_timeout` 此前只在「线程还活着 = 超时」时调用降级工厂；worker 线程里
    `contextlib.suppress(Exception)` 把结果留在 `{}`。于是 `build_context` 任何一处
    抛异常（闸门 / 检索 / 组装任一环节），Hermes 拿到的都是空上下文——短查询的常驻
    画像在这整类失败上**静默**消失；更糟的是 stdout 是 JSON 协议通道，被吞掉的异常
    连 stderr 都没有，运维侧完全不可观测。

    本单排查中它不是假设：给 `relevance_check` 换一个签名不符的替身，TypeError
    即被吞成 `{}`，而 user_profile.md 明明在盘上。
    """

    @staticmethod
    def _boom(*_a, **_k):
        raise RuntimeError("检索链炸了（模拟 vector/LLM 故障）")

    def test_gate_exception_still_injects_persona(self, serve):
        """闸门在 build_context 的内部 try **之外**——这里抛异常此前等于丢画像。

        画像「置顶无检索依赖」，它的产出条件不该取决于检索链是否健康。
        """
        mod, _ = serve()
        with (
            _network_off(mod),
            patch.object(mod, "relevance_check", self._boom),
        ):
            out = _frame(mod, MEM_Q, "s-d1")
        assert PROFILE_MARK in out.get("context", ""), f"闸门抛异常把常驻画像一并丢掉: {out!r}"

    def test_retrieval_exception_still_injects_persona(self, serve):
        """对照（修复前后都应绿）：检索段本身已被 build_context 内部 try 兜住。

        记下边界，免得后来人以为整个函数都受保护——本单修的是 try 之外的区段。
        """
        mod, _ = serve()
        with (
            _network_off(mod),
            patch.object(mod, "relevance_check", lambda q, **kw: {"needs_memory": True}),
            patch.object(mod, "hybrid_search", self._boom),
        ):
            out = _frame(mod, MEM_Q, "s-d1b")
        assert PROFILE_MARK in out.get("context", ""), f"检索抛异常把常驻画像一并丢掉: {out!r}"

    def test_degraded_path_is_logged_not_silent(self, serve, caplog):
        """降级必须留痕：静默吞异常等于把故障藏起来（宁 miss 不静默）。

        stdout 是 JSON 协议通道，所以痕迹只能走 logger（stderr）——测试用 caplog
        直接钉死「异常信息必须可见」，否则下次画像又被静默吃掉。
        """
        import logging

        mod, _ = serve()
        with caplog.at_level(logging.WARNING, logger="lantai"):
            with _network_off(mod), patch.object(mod, "relevance_check", self._boom):
                _frame(mod, MEM_Q, "s-d2")
        assert any("检索链炸了" in r.getMessage() for r in caplog.records), (
            f"异常被吞且未留痕: {[r.getMessage() for r in caplog.records]}"
        )

    def test_exception_without_persona_returns_empty(self, serve):
        """对照：确实没有画像时空返回是对的——不得凭空伪造注入内容。"""
        mod, _ = serve(with_persona=False, with_memories=False)
        with (
            _network_off(mod),
            patch.object(mod, "relevance_check", self._boom),
        ):
            out = _frame(mod, MEM_Q, "s-d3")
        assert out == {}, f"无画像时不得伪造 context: {out!r}"

    def test_normal_zero_hit_path_unchanged(self, serve):
        """对照：正常「检索无命中」路径不走降级工厂，行为逐字不变。"""
        mod, engine = serve()
        with _network_off(mod):
            out = _frame(mod, "元始天尊的坐骑编号是什么", "s-d4")
        assert PROFILE_MARK in out.get("context", ""), "无命中仍应有常驻画像"
        assert MEM_MARK not in out.get("context", "")


class TestPluginSideNoShortCircuitResidue:
    """验收面 1 的防腐层：15 字硬跳过与触发词门不得复活，也不得留尸体。"""

    @pytest.mark.parametrize(
        "q", ["我对什么过敏？", "多高", "我女朋友叫什么名字", "龙井茶冲泡水温是多少", "这个呢"]
    )
    def test_short_queries_without_trigger_words_reach_serve(self, serve, q):
        """插件真回调 + 真 serve：无触发词的短问句必须打到闸门并拿回画像。"""
        hook, _ = serve(with_memories=False)
        calls = []
        plugin = _load_plugin()
        _bridge_plugin(plugin, hook, calls)
        with _network_off(hook):
            r = plugin._on_pre_llm_call(user_message=q, session_id="s-x")
        assert calls == [("s-x", q)], f"短查询 {q!r} 未透传 serve（插件侧仍有硬跳过？）: {calls!r}"
        ctx = (r or {}).get("context") or ""
        assert PROFILE_MARK in ctx, f"短查询 {q!r} 没拿到常驻画像: {ctx!r}"

    def test_plugin_has_no_length_hardcode_above_minimum(self):
        """源码护栏：插件里任何 `len(q) <N`/`<=N` 的门都不得超过 `_MIN_QUERY_CHARS`。"""
        src = Path(PLUGIN_PATH).read_text(encoding="utf-8")
        plugin = _load_plugin()
        floor = plugin._MIN_QUERY_CHARS
        assert floor >= 2, f"最小字符限制不得小于 2（工单修复目标 2）: {floor}"
        offenders = [
            int(n) for n in re.findall(r"len\(\s*q\s*\)\s*(?:<=|<)\s*(\d+)", src) if int(n) > floor
        ]
        assert not offenders, f"插件侧残留硬编码短句阈值: {offenders}"
        assert "<= 15" not in src and "<=15" not in src, "15 字硬跳过未清除"

    def test_no_dead_trigger_word_gate_left_behind(self):
        """尸体护栏：删掉硬跳过后，触发词表若无人引用就是复活旧行为的诱饵，必须清除。"""
        src = Path(PLUGIN_PATH).read_text(encoding="utf-8")
        # 允许注释里写它的历史（解释「为什么这里没有第二道门」），不允许重新出现
        # 定义或使用——否则「≤15 字且无触发词就跳过」迟早照着尸体复活。
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        assert "_TRIGGER_WORDS" not in code, (
            "插件仍保留触发词判据（旧「≤15 字且无触发词就跳过」的尸体/诱饵）——"
            "短问句放行已由 serve 闸门判定，插件侧不得留第二道门"
        )
        assert "in _TRIGGER_WORDS" not in src, "触发词成员判定不得复活"

    def test_one_char_message_is_plugin_boundary_not_silent_persona_loss(self):
        """1 字消息：按工单口径保留 >=2 的最小限制——不得伪造画像，也不得算作注入成功。"""
        plugin = _load_plugin()
        calls = []
        tmp = _bridge_plugin(plugin, _load_hook(), calls)
        r = tmp._on_pre_llm_call(user_message="好", session_id="s-1c")
        assert calls == [], "1 字消息按设计不该打到 serve"
        assert r is None or not r.get("context"), f"1 字消息不得凭空造出注入内容: {r!r}"
