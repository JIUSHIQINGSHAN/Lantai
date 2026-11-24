"""重构守护锁：MMR 选择、检索预算传导、连接生命周期（不 mock 冒烟）。

本文件锁住三类**已实测**的问题，防止优化被回退：

1. `mmr_select` 的冗余度量在原实现里对同一正文反复分词——实测 80 候选选 20 条
   要 3.8~5.2s，而按正文缓存 token 后 72ms，且选择结果逐条一致。这里既锁**等价**
   （同输入同输出），也锁**耗时上限**（证明缓存确实在，不是靠小样本凑巧快）。
2. 检索预算（`budget_s`）此前只传导到 embed / rerank，**没有传导到意图分类**，
   而意图分类是每检索一次同步打一次外部 LLM、带 3 次 tenacity 重试的调用；实测
   LLM 卡住时单这一次就 6.5s，而 hook 外层硬墙只有 2s（`SHELL_HOOK_TIMEOUT`）——
   整段注入被墙丢弃。这里锁住「有预算时分类器收到预算」。
3. `init_db` 初始化 FTS 时 `raw_connection()` 用完不关，与同函数上方迁移块的
   正确写法（try/finally close）不一致——启动即漏一条 DBAPI 连接。这里用替身
   engine 记录 open/close 次数直接验证。

纪律：外部网络面允许替身（LLM / embed），产品代码自身的计算逻辑必须真跑。
"""

import time

import pytest


def _item(content: str):
    class _M:
        def __init__(self, c: str):
            self.content = c

    return _M(content)


def _corpus(n: int) -> list:
    """构造 n 条「同质但不同」的记忆：真实记忆库里这种近义条目占多数。"""
    return [
        (1.0 - i * 0.001, _item(f"用户偏好记录 第{i}条 关于部署脚本与环境配置的细节说明"))
        for i in range(n)
    ]


class TestMMRSelectCache:
    def test_selects_same_items_as_reference(self):
        """缓存版选择结果与逐条分词版逐位一致（等价性，非近似）。"""
        from lantai.retrieval.hybrid import RetrievalParams, mmr_select

        corpus = _corpus(60)
        got = mmr_select(list(corpus), 15, lam=0.7)
        # 参考实现：每次重算 token，无缓存——原实现的语义
        from lantai.retrieval.hybrid import _redundancy

        pool = list(corpus)
        chosen: list = []
        chosen_texts: list = []
        lam = RetrievalParams().mmr_lambda
        while pool and len(chosen) < 15:
            best_idx, best_val = None, None
            for i, (rel, m) in enumerate(pool):
                red = (
                    0.0
                    if getattr(m, "_supersedes_pinned", False)
                    else _redundancy(m.content, chosen_texts)
                )
                val = lam * rel - (1.0 - lam) * red
                if best_val is None or val > best_val:
                    best_idx, best_val = i, val
            rel, m = pool.pop(best_idx)
            chosen.append((rel, m))
            chosen_texts.append(m.content)

        assert [m.content for _, m in got] == [m.content for _, m in chosen]

    def test_does_not_repeat_tokenization(self, monkeypatch):
        """同一正文只分词一次（计数直证，不靠耗时运气）。"""
        from lantai.retrieval import hybrid

        calls: dict[str, int] = {}
        real = hybrid._token_set

        def counting(text):
            calls[text] = calls.get(text, 0) + 1
            return real(text)

        monkeypatch.setattr(hybrid, "_token_set", counting)
        corpus = _corpus(40)
        hybrid.mmr_select(list(corpus), 10, lam=0.7)
        assert calls, "分词必须被真实调用（否则本锁空转）"
        dup = [t for t, n in calls.items() if n > 1]
        assert not dup, f"同一正文被重复分词 {len(dup)} 条，例如：{dup[0][:30]!r}"

    def test_selection_is_fast_at_realistic_pool_size(self):
        """80 候选选 20 条须在 400ms 内（修前实测 3.8s+，缓存后 ~72ms）。"""
        from lantai.retrieval.hybrid import mmr_select

        corpus = _corpus(80)
        # 预热 jieba（进程级一次性 0.6-1.3s，不属被测逻辑）
        mmr_select(_corpus(3), 2, lam=0.7)

        t0 = time.perf_counter()
        got = mmr_select(list(corpus), 20, lam=0.7)
        elapsed = time.perf_counter() - t0
        assert len(got) == 20
        assert elapsed < 0.4, f"mmr_select 80->20 耗时 {elapsed:.2f}s，重复分词未除"

    def test_pinned_items_still_exempt(self):
        """被 supersedes 边置顶的新值条目豁免冗余惩罚（既有契约不回归）。

        口径是「不因近义被淘汰」，不是「压过所有人」：首位仍归分数最高的那条，
        与首位正文几乎相同的置顶条目必须能进榜（不置顶的同分条目会被挤出）。
        """
        from lantai.retrieval.hybrid import mmr_select

        dup = "兰台项目团队喜欢在周五下午发布新版"
        corpus = [(1.0, _item(dup))]  # 首位：分数最高
        corpus += [(0.99, _item(dup)) for _ in range(9)]  # 同分同文的普通重复条目
        pinned = _item(dup)
        pinned._supersedes_pinned = True
        corpus.insert(1, (0.96, pinned))  # 分数略低，但被 supersedes 置顶

        got = mmr_select(list(corpus), 3, lam=0.7)
        picked = [m for _, m in got]
        assert len(picked) == 3
        assert any(m is pinned for m in picked), (
            "置顶条目被冗余惩罚淘汰了（0.96 豁免后 0.672 > 重复条目 0.393，"
            "不豁免只有 0.372，必被同文重复条目挤掉）"
        )


class TestIntentBudget:
    def test_classify_intent_accepts_timeout(self):
        """意图分类器接受 timeout 形参并透传给 LLM 客户端。"""
        from lantai.retrieval import intent as intent_module

        seen: dict = {}

        def fake_chat_json(system, user, timeout=None):
            seen["timeout"] = timeout
            return {"intent": "fact_lookup"}

        original = intent_module.chat_json
        intent_module.chat_json = fake_chat_json
        try:
            intent_module.classify_intent("部署脚本放哪了", timeout=0.4)
        finally:
            intent_module.chat_json = original
        assert seen.get("timeout") == 0.4, "预算必须传到真正发请求的那一层"

    def test_budgetless_call_keeps_old_shape(self):
        """无预算时保持旧调用形状（单参 chat_json），不让既有替身 TypeError。"""
        from lantai.retrieval import intent as intent_module

        seen: dict = {}

        def strict_single_arg(system, user):
            seen["called"] = True
            return {"intent": "procedural"}

        original = intent_module.chat_json
        intent_module.chat_json = strict_single_arg
        try:
            result = intent_module.classify_intent("怎么做记忆迭代")
        finally:
            intent_module.chat_json = original
        assert seen.get("called"), "无预算路径必须仍然调用分类器"
        assert result["intent"] == "procedural"

    def test_hybrid_search_passes_budget_to_intent(self, monkeypatch):
        """hybrid_search(budget_s=...) 把预算带到意图分类（票 90 口径）。

        主线独立做了同一处修正（PR #77，票 90）：有预算时传
        `max(0.05, budget_s * 0.25)`——留 75% 给后续 embed/BM25/组装；
        无预算时按旧单参形状调用。本锁只验「预算确实传下去了」，不锁具体
        比例（那是票 90 的取舍，改比例不该打红本锁）。
        """
        from lantai.retrieval import hybrid

        captured: dict = {}

        def fake_classify(query, timeout=None):
            captured["timeout"] = timeout
            return {"intent": "fact_lookup", "candidate_n": 2}

        def boom(*a, **k):
            raise RuntimeError("vector disabled for this probe")

        monkeypatch.setattr(hybrid, "classify_intent", fake_classify)
        monkeypatch.setattr(hybrid, "embed", boom)
        monkeypatch.setattr(hybrid, "get_vector_store", lambda: None)

        hybrid.hybrid_search("随便问问部署", top_k=2, budget_s=0.75, use_rerank=False)
        assert captured.get("timeout") is not None, "有预算时必须传导给意图分类"
        assert 0 < captured["timeout"] <= 0.75, "传下去的预算不得超过总预算"


class _FakeRawConn:
    def __init__(self, log: list):
        self._log = log
        self.closed = False

    def execute(self, *a, **k):
        return None

    def commit(self):
        return None

    def close(self):
        self.closed = True
        self._log.append("closed")


class _FakeEngine:
    """记录 open / close 的替身 engine（连接生命周期是外部资源面，允许替身）。"""

    def __init__(self):
        self.log: list = []
        self.opened = 0

    def raw_connection(self):
        self.opened += 1
        return _FakeRawConn(self.log)


class TestInitDbConnectionHygiene:
    def test_init_fts_connection_closed(self, monkeypatch):
        """init_db 初始化 FTS 用的 raw_connection 必须归还（修前漏关）。"""
        from lantai.storage import db as db_module

        fake = _FakeEngine()
        monkeypatch.setattr(db_module, "engine", fake)
        monkeypatch.setattr(
            db_module,
            "SQLModel",
            type(
                "X", (), {"metadata": type("Y", (), {"create_all": staticmethod(lambda e: None)})}
            ),
        )
        monkeypatch.setattr(db_module, "apply_migrations", lambda conn: None)
        monkeypatch.setattr(db_module, "init_fts", lambda conn: True)

        db_module.init_db()
        assert fake.opened == 2, "迁移一次 + FTS 一次"
        assert fake.log.count("closed") == 2, (
            f"两次 raw_connection 都必须 close，实测 close 次数 {fake.log.count('closed')}"
        )
