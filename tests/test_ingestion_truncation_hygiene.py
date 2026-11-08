"""票 49：摄取面 HTML 不剥 + 截断卫生二项（RSS 标签 / GRAM 24 截断观测 / incoming_ref 无标记硬截）。

三病灶（立案轮 7 CJK 归一/截断面子审计，HEAD 亲核行号漂移后形态不变）：
1. `rss.py` 摘要原样入库：feedparser 内建 sanitize 只清 script/style/iframe
   等危险元素，`<p>/<a>/<img>/<table>` 普通标签随正文进 RawDocument → 候选
   → FTS → 宿主注入帧（evidence 截 200 字带满标签噪音）。
2. `_GRAM_TERMS_MAX=24` 静默截断查询尾部 gram（AND/OR 两路共用），截断事实
   零观测。修复口径：截断事实是**查询的确定函数**——纯谓词
   `bm25_terms_truncated` + `log_retrieval` 落列（同 is_system_noise/
   estimated_tokens「原查询在抹除前照算」既有范式 + is_system_noise 加列迁移
   先例）；工单红测字面形态（search_fts 自写事件行）被否：search_fts 只持裸
   conn、无回执生命周期上下文，自写 pending 行会被 10 分钟回执扫描器改判
   missed 并抹原话，且与真实事件行双计污染弱标注统计。
3. incoming_ref（冲突账本「对方主张」引用位）无标记硬截 200：截断点恰在
   语义差异之后时主张不完整，下游无从区分「原文如此」与「被截」。修复口径
   （工单「incoming_ref 倾向存全长」）：内容来源的 incoming_ref 存全长
   （语义完整性优先），调用方 reason（无界外部输入）走仓内既有
   `truncate_codepoints` helper 带省略号；dialogue/extractor 的候选摘要
   `[:400]` 同形对齐。

禁 mock 被测链路：真 feedparser、真迟到更正/闸门流程、真内存库；
网络边替身仅两处（fetch_with_safety 本地 feed 字节——票 69 豁免口径；
chat_json 注入 RuntimeError 模拟 LLM 缺席走离线兜底——外部 LLM 边界）。
"""

import json

import pytest
from sqlmodel import select

from lantai.models.tables import ConflictEvent, MemoryCandidate, MemoryItem, RetrievalEvent


# ── 病灶 1：RSS 摘要 HTML 剥离 ────────────────────────────────────────────

_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t49feed</title>
<item><title>条目一</title><link>https://example.com/a</link>
<description><![CDATA[<p>票49第一段<b>加粗</b>正文</p><a href="https://example.com/x">/readme</a><table><tr><td>R&amp;D</td></tr></table>]]></description>
</item>
<item><title>条目二</title><link>https://example.com/b</link>
<description>票49纯文本控制条目，不应被改动</description>
</item>
</channel></rss>"""


def test_rss_summary_strips_html_and_unescapes(param_env, monkeypatch):
    """红：适配器出库的 RawDocument.content 带 <p>/<a>/<table> 标签。
    修复后纯文本、实体已解、纯文本控制条目逐字不变。"""
    from lantai.ingestion import rss as rss_mod

    monkeypatch.setattr(rss_mod, "fetch_with_safety", lambda url: _FEED_XML.encode("utf-8"))
    docs = rss_mod.RSSAdapter().fetch({"url": "https://feed.example/rss.xml"})
    assert len(docs) == 2, f"两条目都应入库；实得 {len(docs)}"

    html_doc = next(d for d in docs if "票49第一段" in (d.content or ""))
    assert "<p>" not in html_doc.content, f"普通标签随正文进库（病灶实锤）：{html_doc.content[:120]}"
    assert "<a " not in html_doc.content and "<table>" not in html_doc.content
    assert "R&D" in html_doc.content and "&amp;" not in html_doc.content, "实体应已 unescape"
    control = next(d for d in docs if "纯文本控制" in (d.content or ""))
    assert control.content == "票49纯文本控制条目，不应被改动", "纯文本条目不得被改写"


# ── 病灶 2：GRAM 24 截断观测 ─────────────────────────────────────────────

_LONG_WORDS = [f"词票四九序号{i:02d}尾" for i in range(30)]  # 30 个三字 CJK 词 → 30 gram > 24
_LONG_QUERY = " ".join(_LONG_WORDS)


def test_bm25_truncation_predicate(param_env):
    """红：纯谓词不存在。修复后超 24 gram 查询判真、短查询判假。"""
    from lantai.storage.fts import bm25_terms_truncated  # noqa: F401  红相：ImportError

    assert bm25_terms_truncated(_LONG_QUERY) is True
    assert bm25_terms_truncated("机器学习偏好") is False
    assert bm25_terms_truncated("") is False


def test_fts_truncation_marked_on_retrieval_event(param_env):
    """红：直调 search_fts（AND 路径真跑）+ log_retrieval 落事件——
    修复后 retrieval_event 可见截断标记；短查询对照恒假。"""
    sf, _engine = param_env
    with sf() as s:
        item = MemoryItem(id="m49", content="知识图谱记忆架构研究", status="active", user_id="u49")
        s.add(item)
        s.commit()
        from lantai.storage.fts import search_fts, sync_fts

        sync_fts(s, "m49", item.content)
        hits = search_fts(s.connection(), _LONG_QUERY, top_k=5)
        assert isinstance(hits, list), "直调 search_fts：长查询走 AND 截断路径不炸"

    from lantai.observability.retrieval_log import log_retrieval

    eid_long = log_retrieval(_LONG_QUERY, [], latency_ms=1)
    eid_short = log_retrieval("机器学习偏好", [], latency_ms=1)
    assert eid_long and eid_short
    with sf() as s:
        ev_long = s.get(RetrievalEvent, eid_long)
        ev_short = s.get(RetrievalEvent, eid_short)
        assert ev_long.fts_terms_truncated is True, (
            f"超 24 gram 查询的检索事件应带截断标记；实得 {ev_long.fts_terms_truncated}"
            f"（截断事实零观测 = 病灶实锤）"
        )
        assert ev_short.fts_terms_truncated is False, "短查询对照臂恒假"


# ── 病灶 3：incoming_ref 无标记硬截 200 ──────────────────────────────────

_CONTENT_250 = "更正后的新结论：" + "、".join(
    f"要点{i}：知识图谱的记忆架构需要长期维护与演化" for i in range(10)
)
assert len(_CONTENT_250) > 200


def test_late_correction_incoming_ref_full_length(param_env):
    """红：250 字 content 走替换型迟到更正，incoming_ref 现状截 200 无标记。
    修复后存全长（冲突判别语义完整性优先，工单口径）。"""
    sf, _engine = param_env
    old = MemoryItem(id="old49", content="旧结论：数据库用 MySQL", status="active", user_id="u49")
    new = MemoryItem(id="new49", content=_CONTENT_250, status="active", user_id="u49")
    with sf() as s:
        s.add(old)
        s.add(new)
        s.commit()

    from datetime import timedelta

    from lantai.cognition.late_correction import apply_supersede_correction
    from lantai.core.time import utcnow

    res = apply_supersede_correction(old, new, reason="票49更正", actor="t49")
    assert res.get("ok") is True, f"更正流程真跑失败：{res}"
    with sf() as s:
        ev = s.exec(
            select(ConflictEvent).where(ConflictEvent.kind == "override", ConflictEvent.memory_id == "old49")
        ).first()
        assert ev is not None
        assert ev.incoming_ref == _CONTENT_250, (
            f"incoming_ref 应存全长（{len(_CONTENT_250)} 字）；实得 {len(ev.incoming_ref or '')} 字"
            f"（无标记硬截 200 = 病灶实锤）"
        )


def test_expire_correction_reason_truncation_marked(param_env):
    """红：无界调用方 reason 截 200 无标记。修复后经 truncate_codepoints
    带省略号（有界外部输入保留钳制，但「被截」必须可见）。"""
    sf, _engine = param_env
    item = MemoryItem(id="exp49", content="到期结论", status="active", user_id="u49")
    with sf() as s:
        s.add(item)
        s.commit()

    from datetime import timedelta

    from lantai.cognition.late_correction import apply_expire_correction
    from lantai.core.time import utcnow

    reason = "失效原因" * 50  # 200 字 → 补一字确保 >200
    reason += "!"
    res = apply_expire_correction(item, valid_to=utcnow() + timedelta(days=1), reason=reason)
    assert res.get("ok") is True, f"失效型更正真跑失败：{res}"
    with sf() as s:
        ev = s.exec(
            select(ConflictEvent).where(ConflictEvent.memory_id == "exp49")
        ).first()
        assert ev is not None
        assert ev.incoming_ref.endswith("..."), (
            f"被截 reason 应带省略号尾标；实得尾 10 字：{ev.incoming_ref[-10:]!r}"
        )
        assert len(ev.incoming_ref) <= 200


def test_gate_decision_incoming_ref_full_length(param_env, monkeypatch):
    """红：闸门 salience 降权账本的 incoming_ref=summary_text[:200] 无标记。
    修复后存全长（上游 summary 本有界）。真 decide() 流程：向量降级 rowid
    召回 → 确定性互斥规则命中 → 降权写账本；LLM 相位以 chat_json 注入
    RuntimeError 拦（外部边界替身，确定性）。"""
    sf, _engine = param_env
    summary = "服务已启用" + "，附加说明" * 48  # >200 字，含「启用」
    with sf() as s:
        s.add(
            MemoryItem(
                id="old50", content="系统配置：该服务已禁用", status="active", user_id="u50", importance=0.1
            )
        )
        s.add(
            MemoryCandidate(
                id="cand50",
                document_id="doc50",
                user_id="u50",
                status="pending_review",
                summary=summary,
                extractor_confidence=0.9,
            )
        )
        s.commit()

    import lantai.llm.client as llm_client

    def _no_llm(*a, **kw):
        raise RuntimeError("llm unavailable (票49 测试替身)")

    monkeypatch.setattr(llm_client, "chat_json", _no_llm)

    from lantai.core.acl import Principal
    from lantai.gate.decision import decide

    principal = Principal(user_id="u50", tenant_id=None, allowed_lanes=None, role="user")
    res = decide("cand50", principal=principal)
    with sf() as s:
        ev = s.exec(
            select(ConflictEvent).where(
                ConflictEvent.kind == "salience_demote", ConflictEvent.memory_id == "old50"
            )
        ).first()
        assert ev is not None, f"互斥规则应命中并降权写账本；decide={res}"
        assert ev.incoming_ref == summary, (
            f"incoming_ref 应存全长（{len(summary)} 字）；实得 {len(ev.incoming_ref or '')} 字"
        )


def test_dialogue_summary_truncation_marked(param_env, monkeypatch):
    """红：候选摘要 text[:400] 无标记硬截（extractor 离线兜底同形）。
    修复后经 truncate_codepoints 带「...」尾标（下有 400 字上界、被截可见）。"""
    sf, _engine = param_env
    text = "记住：项目决策是采用事件溯源架构，" + "理由包括可审计可回放与时序一致等" * 30  # >400 字

    import lantai.parsing.extractor as extractor_mod

    def _no_llm(*a, **kw):
        raise RuntimeError("llm unavailable (票49 测试替身)")

    monkeypatch.setattr(extractor_mod, "chat_json", _no_llm)

    from lantai.ingestion.dialogue import ingest_dialogue

    res = ingest_dialogue(text, user_id="u51")
    assert res["ingested"] is True
    with sf() as s:
        cand = s.get(MemoryCandidate, res["candidate_id"])
        assert cand is not None
        assert cand.summary.endswith("..."), (
            f"候选摘要被截应带省略号尾标；实得尾 20 字：{(cand.summary or '')[-20:]!r}"
        )
        assert len(cand.summary) <= 400
        assert cand.summary.startswith("记住：项目决策"), "截断保留头部语义"
