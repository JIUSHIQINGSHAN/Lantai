"""coalesce 空闲冲刷真冒烟（v0.68 测试诚实性审计补强）。

审计结论：`run_coalesce_idle` 的三个直调用例**全部** patch 掉
`get_coalesce_buffer`（换入手工实例）+ `_create_candidate_with_extraction`
（换入假持久化）——「空闲冲刷 → 真库持久化」主路径从未真跑过。
本测试使用**真单例**缓冲（不 patch 取件器），LLM 提取走 conftest 替身、
落库走 param_env 真库，验证 combined_content 真的落成 RawDocument +
MemoryCandidate 且缓冲被清空。
"""

import pytest


def test_run_coalesce_idle_real_flush_persists(param_env):
    from sqlmodel import select

    from lantai.ingestion.coalesce import get_coalesce_buffer
    from lantai.models.tables import MemoryCandidate, RawDocument
    from lantai.workers.ingest_worker import run_coalesce_idle

    buf = get_coalesce_buffer()  # 真单例：取件器不替身
    user = "coalesce-real-flush-smoke"
    buf.add(user, "general", "第一条真冲刷的足够长的异步内容")
    buf.add(user, "general", "第二条真冲刷的足够长的异步内容")

    # 排布空闲超时（真对象上的状态准备，非逻辑替身）：键格式 user:lane:session
    with buf._lock:
        for key in list(buf._timestamps):
            if key.startswith(f"{user}:"):
                buf._timestamps[key] = 0.0

    run_coalesce_idle()

    assert buf.water_level()["total_messages"] == 0, "冲刷后缓冲应清空"
    session_factory, _ = param_env
    with session_factory() as s:
        docs = s.exec(select(RawDocument).where(RawDocument.title == "coalesced")).all()
        assert docs, "combined_content 未落成 RawDocument"
        assert all("第一条真冲刷" in d.content and "第二条真冲刷" in d.content for d in docs)
        cands = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.document_id.in_([d.id for d in docs]))
        ).all()
        assert cands, "RawDocument 未建对应候选"
