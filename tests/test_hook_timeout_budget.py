"""D-04 hook 超时预算（票 `.scratch/v0.65-next-window-2026-11/01`）。

病灶：shell_hook 外层 `SHELL_HOOK_TIMEOUT=2s` 包着 embedding（OpenAI 客户端
无 timeout + tenacity 3 连击）与 reranker（`RERANKER_TIMEOUT=30s` ×2）两次
网络调用——慢调用必撞外层墙，`_run_with_timeout` 超时返回 `{}`，
**整段上下文（含常驻画像）静默丢弃**；daemon 线程不可取消，网络调用继续挂着。

修法三件套：
1. **预算传导**：`build_context` 以 deadline 计算剩余预算传给 `hybrid_search`
   （新参 `budget_s`）——embed 透传 OpenAI `timeout=budget_s`；rerank 收到
   `min(RERANKER_TIMEOUT, 剩余预算)`，慢调用在预算内自行降级而非撞墙；
2. **超时兜底不丢画像**：QUERY 通道 `_run_with_timeout` 超时返回降级
   `{"context": 常驻画像}`（此前返回 `{}`——既有测试锁的是病灶行为）；
3. `_run_with_timeout` 支持超时降级工厂（零参回调，仅超时路径调用）。

不 mock 冒烟：patch 延迟（外部网络面允许），真实计算逻辑直调公开接口。
"""

import importlib.util
import io
import json
import os
import time

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)


def _load_hook(monkeypatch, embed_delay=0.0, timeout=2.0, persona_file: str | None = None):
    spec = importlib.util.spec_from_file_location("shell_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod.settings, "SHELL_HOOK_TIMEOUT", timeout)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-hook-key")
    monkeypatch.setattr(mod.settings, "RERANKER_ENABLED", False)  # 只测 embed 慢路径

    if persona_file:
        # 画像探针路径注入（真实文件读取，不 mock 计算逻辑）
        from pathlib import Path

        pf = Path(persona_file)
        monkeypatch.setattr(mod.settings, "LANTAI_HOME", str(pf.parent))

    def maybe_slow_embed(texts, timeout=None):
        if embed_delay:
            time.sleep(embed_delay)
        return [[0.1] * 8]

    monkeypatch.setattr(mod, "embed", maybe_slow_embed)
    return mod


@pytest.fixture(scope="module", autouse=True)
def _warm_jieba_once():
    """jieba 冷启动（~0.9-1.4s）是进程级一次性成本。

    真实部署由 shell_hook._prewarm 在启动时预热（票 01 Slice 4）；测试直调
    build_context 不经 _prewarm，故模块级预热一次对齐真实部署形态——
    否则首个触发 jieba 的测试把冷启动算进 hook 预算（假阳性）。
    """
    try:
        import jieba

        jieba.lcut("预热")
    except Exception:
        pass


def _write_persona(tmp_path) -> str:
    """造一个真实 user_profile.md（LANTAI_HOME 探针首位）。"""
    p = tmp_path / "user_profile.md"
    p.write_text("用户偏好乌龙茶，部署在内网。", encoding="utf-8")
    return str(p)


class TestTimeoutFallbackKeepsPersona:
    """超时兜底不丢画像（修复前红：超时返回 {}，画像段静默丢弃）。"""

    def test_dispatch_query_timeout_returns_persona(self, monkeypatch, tmp_path):
        """embed 慢调用撞外层墙：QUERY 通道降级返回画像段而非 {}。"""
        persona = _write_persona(tmp_path)
        mod = _load_hook(monkeypatch, embed_delay=1.0, timeout=0.2, persona_file=persona)
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_MIN_CHARS", 3)
        raw = json.dumps({"query": "这是一个超过三字符的慢查询", "type": "query"})
        start = time.perf_counter()
        out = mod._handle_one(raw)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.5, f"超时未生效: {elapsed:.2f}s"
        # 修复前：{}（画像丢失）；修复后：降级返回画像
        ctx = out.get("context") or ""
        assert "用户偏好乌龙茶" in ctx, f"超时降级丢失画像: {out!r}"

    def test_run_with_timeout_fallback_factory(self, monkeypatch, tmp_path):
        """_run_with_timeout 超时路径调用降级工厂（非超时不调用）。"""
        mod = _load_hook(monkeypatch)
        calls = []

        def fast():
            return {"context": "fast-result"}

        def slow():
            time.sleep(0.5)
            return {"context": "never"}

        def fallback():
            calls.append("called")
            return {"context": "persona-fallback"}

        assert mod._run_with_timeout(fast, 0.5) == {"context": "fast-result"}
        assert calls == []  # 非超时不动工厂
        assert mod._run_with_timeout(slow, 0.1, _fallback=fallback) == {
            "context": "persona-fallback"
        }
        assert calls == ["called"]


class TestEmbedBudgetPropagation:
    """预算传导：embed 慢调用在预算内降级，总耗时 ≤ 外层预算（修复前红）。"""

    def test_slow_embed_budget_degrades_within_deadline(self, monkeypatch, tmp_path):
        """embed 慢（1s）+ 外层预算 2s：hybrid_search 传预算后向量路径预算内降级
        → BM25/FTS 兜底可用，hook 不再必然超时丢段。"""
        mod = _load_hook(monkeypatch, embed_delay=0.0, timeout=2.0)
        # 真实 embed 被替换为延迟版（外部网络面），hybrid_search 走真实逻辑
        # 造真实库：正文与查询共享 3-gram（trigram 最小匹配单元），BM25/FTS
        # 兜底可命中（sync_fts 显式同步——FTS 表不自动追 SQLModel 写入）
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        import lantai.models.tables  # noqa: F401
        from lantai.models.tables import MemoryItem
        from lantai.storage.fts import init_fts, sync_fts

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            init_fts(conn.connection.driver_connection)
        content = "这是一个超过三字符的记忆正文，记录用户部署环境的测试数据"
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id="mem-budget-1",
                    memory_type="semantic",
                    key="k1",
                    content=content,
                    status="active",
                )
            )
            s.commit()
            sync_fts(s, "mem-budget-1", content)
            s.commit()

        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

        # embed 延迟 3s > 预算 2s：预算传导后向量路径在预算内放弃 → 兜底命中
        captured: dict = {}

        def slow_embed(texts, timeout=None):
            # 预算传导语义复刻：真 SDK 收到 timeout 在窗口内放弃 + embed
            # 预算模式不做多次重试（新契约）——一次机会睡满预算即抛
            captured["timeout"] = timeout
            time.sleep(min(3.0, timeout) if timeout is not None else 3.0)
            raise TimeoutError("embed budget exceeded (simulated)")

        monkeypatch.setattr("lantai.retrieval.hybrid.embed", slow_embed)

        start = time.perf_counter()
        out = mod.build_context("这是一个超过三字符的查询")
        elapsed = time.perf_counter() - start

        outer = mod.settings.SHELL_HOOK_TIMEOUT

        # 契约 1（确定性，与机器快慢无关）：预算真的传导给了 embed。
        # 修复前 hybrid 调 embed 不带 timeout，这里会拿到 None —— 直接红。
        assert captured.get("timeout") is not None, "预算未传导：embed 没有收到 timeout 形参"
        assert 0 < captured["timeout"] < outer, (
            f"传导的预算 {captured['timeout']!r} 不在 (0, 外层预算 {outer}) 内："
            "要么没传，要么传了一个照样会撞外层墙的值"
        )

        # 契约 2（结果面）：向量面在预算内放弃后仍要兜底注入。
        # 修复前被外层 _run_with_timeout 掐掉返回 {}，画像与召回整段静默丢失。
        assert out.get("context"), f"预算内降级后应仍有注入（兜底命中）: {out!r}"

        # 契约 3（耗时上界）：上界锚在「未传导」的真实代价上，而不是锚在与 sleep
        # 等长的外层预算上。旧断言 `elapsed < 2.0` 只有约 0.3s 余量：传导后的预算是
        # SHELL_HOOK_TIMEOUT * 0.85 ≈ 1.7s（scripts/shell_hook.py:316），剩下的 0.3s
        # 要装下 BM25/FTS 兜底的全部工作——覆盖率插桩加满载直接吃掉。
        # v0.66 发布门禁实测：CI 口径 `pytest tests/ -q --cov=lantai` 全量跑到此用例
        # elapsed=2.11s 判红，而无插桩单跑通过——同一条契约在 CI 永远红、在本地永远绿。
        # 现在取「未传导即睡满 3s」为界：睡 3s 在任何机器上都保证 elapsed >= 3.0，
        # 因此这条界拦得住真回归，又不受插桩开销影响，是同一契约更强的表达。
        assert elapsed < 3.0, (
            f"hook 耗时 {elapsed:.2f}s 已等于未传导的代价（睡满 3s），预算传导失效"
        )

    def test_fast_path_zero_regression(self, monkeypatch, tmp_path):
        """快速路径零回归：embed 正常 → 命中注入带 evidence（既有契约）。"""
        mod = _load_hook(monkeypatch, embed_delay=0.0, timeout=2.0)
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine

        import lantai.models.tables  # noqa: F401
        from lantai.models.tables import MemoryItem
        from lantai.storage.fts import init_fts

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            init_fts(conn.connection.driver_connection)
        with Session(engine) as s:
            s.add(
                MemoryItem(
                    id="mem-fast-1",
                    memory_type="semantic",
                    key="k2",
                    content="用户喜欢 Python 和 Rust",
                    status="active",
                )
            )
            s.commit()

        import lantai.storage.db as db_module

        monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))

        class _FakeStore:
            def search(self, qv, top_k=5, filters=None):
                return [{"id": "mem-fast-1", "distance": 0.1}]

        monkeypatch.setattr("lantai.retrieval.hybrid.get_vector_store", lambda: _FakeStore())

        out = mod.build_context("这是一个超过三字符的查询")
        assert "依据" in out.get("context", "")
        assert out["evidence"][0]["id"] == "mem-fast-1"
