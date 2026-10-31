"""密钥闸门全链路覆盖与拾遗全表扫描治理（v0.63 票 02，X-06/N-02）。

真 DB + 真调用链，不 mock 内部逻辑；替身仅限外部面（向量库由 param_env
既有 DummyVS 承担）。**测试泄漏红线**：

- 时钟注入：拾遗熔断测试只 monkeypatch 引擎的 `_now` 时钟函数，
  绝不 `time.sleep` 真实等待（CI 抖动 = 假红/假绿）；
- 设置单例：每个用例 monkeypatch `settings.SECRET_GUARD_MODE`，
  pytest 自动回滚，不污染后续用例；
- coalesce 全局缓冲：本票测试均走 COALESCE_ENABLED=False 路径，
  不触全局单例，无跨用例状态泄漏。

X-06 口径（根因排查裁决，见票 02）：SECRET_GUARD_MODE 默认应为 shadow
（检测+记录+不阻断）——enforce 会把「记住：我的 key 是 sk-...」这类
正当记忆强制挂起人工裁决，是假阳性放大器；覆盖扩充下沉公共助手到
`secret_guard.py`（SSOT），`dialogue.py` 既有路由逻辑不动（3 个既有
测试锚定）。
"""

import logging

import pytest
from sqlmodel import Session, select

from lantai.models.tables import MemoryCandidate, MemoryItem

SECRET_TEXT = "记住:我的key是 " + "sk-" + "F" * 24 + " 请保存"  # 假密钥形状（绕过提交闸明文扫描）

# jieba 对「超声波清洗机保养周期」切出长度>1 的词，与记忆正文精确共享——
# SQL 预筛（token LIKE）可命中
SHIYI_QUERY = "超声波清洗机保养周期"
SHIYI_CONTENT = "超声波清洗机的保养周期是每 500 小时更换一次滤芯"


# ── X-06：密钥闸门全链路覆盖 ──────────────────────────────────────────────


def test_secret_guard_default_mode_is_shadow():
    """X-06：默认模式从 off 提升为 shadow——检测覆盖不以静默为前提。

    off 与 shadow 的行为差异只在「记录」：shadow 下 find_secret_shapes
    照常执行并 logger.warning，写入不阻断（正当密钥记忆不假阳性挂起）。
    """
    from lantai.core.settings import Settings

    assert Settings().SECRET_GUARD_MODE == "shadow"


def test_add_memory_fastpath_secret_shadow_logged_not_blocked(param_env, monkeypatch, caplog):
    """X-06 覆盖扩充：add_memory fastpath 路径（REST POST /memories / MCP add）
    shadow 模式下命中密钥形状 → 记录 warning，候选照常 fastpath（不阻断）。
    """
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.models.schemas import AddMemoryReq
    from lantai.services.memory_service import add_memory

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    with caplog.at_level(logging.WARNING):
        result = add_memory(AddMemoryReq(title="t", content=SECRET_TEXT), user_id="u1")
    assert "candidate_id" in result
    assert "密钥熔断命中" in caplog.text
    with session_factory() as s:
        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == "fastpath"  # shadow 不转待审


def test_add_memory_fastpath_secret_enforce_pending_review(param_env, monkeypatch):
    """X-06 覆盖扩充：fastpath 路径 enforce 模式 → 命中转待审队列
    （与 dialogue.py 既有口径一致：宁 miss 不脏写，交用户裁决）。
    """
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.models.schemas import AddMemoryReq
    from lantai.services.memory_service import add_memory

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    result = add_memory(AddMemoryReq(title="t", content=SECRET_TEXT), user_id="u1")
    assert result.get("status") == "pending_review"
    with session_factory() as s:
        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == "pending_review"


def test_add_memory_extraction_path_secret_enforce_pending_review(param_env, monkeypatch):
    """X-06 覆盖扩充：非 fastpath（LLM 提取路径，替身 extract）enforce → 待审。

    fastpath_check 对不带「记住：」前缀的普通陈述不命中白名单，走
    `_create_candidate_with_extraction`——extract_candidate 是该路径唯一
    外部面（LLM），按测试纪律允许替身；密钥检测在其前的内容闸，不替身。
    """
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.models.schemas import AddMemoryReq
    from lantai.services import memory_service
    from lantai.services.memory_service import add_memory

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    monkeypatch.setattr(
        memory_service,
        "extract_candidate",
        lambda title, content: {
            "topic": [],
            "summary": content[:50],
            "claims": [],
            "methods": [],
            "constraints": [],
            "actions": [],
            "extractor_confidence": 0.9,
        },
    )
    secret_plain = "数据库连接串备用 key " + "sk-" + "F" * 24 + " 别外发"
    result = add_memory(AddMemoryReq(title="t", content=secret_plain), user_id="u1")
    assert result.get("status") == "pending_review"
    with session_factory() as s:
        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == "pending_review"


def test_add_memory_clean_content_unaffected(param_env, monkeypatch, caplog):
    """X-06 正面对照：无密钥形状的内容三模式均不记录、不挂起（防误伤回验）。"""
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.models.schemas import AddMemoryReq
    from lantai.services.memory_service import add_memory

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "shadow")
    with caplog.at_level(logging.WARNING):
        result = add_memory(
            AddMemoryReq(title="t", content="记住：周三下午三点项目周会"), user_id="u1"
        )
    assert "密钥熔断命中" not in caplog.text
    with session_factory() as s:
        cand = s.get(MemoryCandidate, result["candidate_id"])
        assert cand.status == "fastpath"


def test_dialogue_path_secret_guard_unchanged(param_env, monkeypatch):
    """X-06 回归锚定：dialogue.py 既有 enforce 路由逻辑保持原行为
    （公共助手下沉后 SSOT 一致，不因重构漂移）。
    """
    session_factory, _ = param_env
    from lantai.core.settings import settings
    from lantai.ingestion.dialogue import ingest_dialogue

    monkeypatch.setattr(settings, "SECRET_GUARD_MODE", "enforce")
    result = ingest_dialogue(SECRET_TEXT)
    assert result["status"] == "pending_review"
    with session_factory() as s:
        assert s.get(MemoryCandidate, result["candidate_id"]).status == "pending_review"


# ── N-02：拾遗全表扫描剪枝与熔断 ─────────────────────────────────────────


def _seed_shiyi_memories(session_factory, n_filler: int):
    """1 条 token 命中记忆 + n_filler 条无关填充（验证 SQL 预筛不把无关行捞进 Python 层）。"""
    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem-shiyi-hit",
                memory_type="semantic",
                key="k-hit",
                content=SHIYI_CONTENT,
                status="active",
                user_id="u1",
            )
        )
        for i in range(n_filler):
            s.add(
                MemoryItem(
                    id=f"mem-filler-{i}",
                    memory_type="semantic",
                    key=f"k-f-{i}",
                    content=f"与查询完全无关的第 {i} 条随笔记录，谈的是厨艺与登山",
                    status="active",
                    user_id="u1",
                )
            )
        s.commit()


def test_shiyi_remedy_recall_finds_token_match(param_env):
    """N-02 正面对照：token 命中记忆照常补救召回（功能不回归）。"""
    session_factory, _ = param_env
    _seed_shiyi_memories(session_factory, n_filler=3)
    from lantai.cognition.shiyi import ShiyiEngine

    with session_factory() as s:
        engine = ShiyiEngine(session=s)
        out = engine.remedy_recall(SHIYI_QUERY)
    assert out["is_remedied"] is True
    ids = [item["id"] for item in out["remedied_items"]]
    assert "mem-shiyi-hit" in ids


def test_shiyi_sql_prefilter_skips_irrelevant_rows(param_env):
    """N-02 剪枝契约：返回体带 `scanned_count`（进入 Python 逐条匹配的行数）。

    剪枝生效 ⇒ scanned_count 远小于全表行数（1 命中 + 20 填充）；
    全表扫描（修复前）⇒ scanned_count == 21，本测试为红。
    """
    session_factory, _ = param_env
    _seed_shiyi_memories(session_factory, n_filler=20)
    from lantai.cognition.shiyi import ShiyiEngine

    with session_factory() as s:
        engine = ShiyiEngine(session=s)
        out = engine.remedy_recall(SHIYI_QUERY)
    assert out["is_remedied"] is True
    assert out["scanned_count"] <= 2  # 命中行（+预筛边界宽余），绝不接近 21


def test_shiyi_scan_budget_circuit_breaker(param_env, monkeypatch):
    """N-02 熔断：扫描超预算即中断并标注 partial（scan_truncated=True），
    不无限扫描——时钟注入推进，不 sleep（测试泄漏红线）。

    种子设计（防测试泄漏自检）：50 条全部含查询 token，SQL 预筛**不**收窄
    ——熔断压力真实作用于 Python 逐条匹配循环；若种子里混入无关行被
    预筛滤掉，循环提前结束会造成「熔断看似失效」的假红/假绿。
    """
    session_factory, _ = param_env
    with session_factory() as s:
        for i in range(50):
            s.add(
                MemoryItem(
                    id=f"mem-cb-{i}",
                    memory_type="semantic",
                    key=f"k-cb-{i}",
                    content=f"超声波清洗机保养周期的第 {i} 条变体记录",
                    status="active",
                    user_id="u1",
                )
            )
        s.commit()
    from lantai.cognition.shiyi import ShiyiEngine

    with session_factory() as s:
        engine = ShiyiEngine(session=s)
        # 时钟注入：起始 t=0，此后每次检查推进 500ms——默认 800ms 预算
        # 在第二次检查必然超支，循环必然中断（50 行远未扫完）。
        ticks = iter([0.0] + [i * 0.5 for i in range(1, 1000)])
        monkeypatch.setattr(engine, "_now", lambda: next(ticks))
        out = engine.remedy_recall(SHIYI_QUERY)
    assert out.get("scan_truncated") is True


def test_shiyi_scan_budget_not_truncated_when_fast(param_env, monkeypatch):
    """N-02 熔断正面对照：预算内完成 → scan_truncated=False（不少标）。"""
    session_factory, _ = param_env
    _seed_shiyi_memories(session_factory, n_filler=3)
    from lantai.cognition.shiyi import ShiyiEngine

    with session_factory() as s:
        engine = ShiyiEngine(session=s)
        ticks = iter([0.0] * 1000)  # 时钟冻结：永不超支
        monkeypatch.setattr(engine, "_now", lambda: next(ticks))
        out = engine.remedy_recall(SHIYI_QUERY)
    assert out.get("scan_truncated") is False
    assert out["is_remedied"] is True
