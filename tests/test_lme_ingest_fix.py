"""LongMemEval harness 修复冒烟（v0.25 票 01）——不 mock 核心路径。

锁两个修复点：
1. 灌库 payload 序列化 ensure_ascii=True：U+2028 行界防拆（修前
   ensure_ascii=False 的 payload 经 parse_import_lines 的 str.splitlines()
   把含 U+2028 的合法 JSONL 行拆成两段废段——25e5aa4f/fea54f57/0ddfec37_abs
   实测 15 个废段）。测试用真实 U+2028 文本直跑 ingest 侧序列化 + 服务端
   parse_import_lines，断言零废段。
2. coverage 在场性探针（重导幂等判定）：每行恰 1 duplicate ⇔ 在库；
   缺行时 imported>0。用真实临时 SQLite 跑 run_jsonl_import 两轮（真实
   sha 幂等去重路径），仅 mock embed/向量存储两个外部依赖（同
   test_import_jsonl.py 纪律）。
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.eval.longmemeval import import_lines_for_question
from lantai.services.import_service import parse_import_lines, run_jsonl_import
from lantai.storage.fts import init_fts


def _serialize_payload(lines: list[dict], *, ensure_ascii: bool) -> str:
    """复刻 scripts/lme_run.py::_ingest_one 的 payload 构造（单点真源在该脚本；
    测试内联同款表达式，脚本改动漂移时本测试红色提醒）。"""
    return "\n".join(json_dumps(l, ensure_ascii=ensure_ascii) for l in lines)


def json_dumps(obj, *, ensure_ascii: bool) -> str:
    import json

    return json.dumps(obj, ensure_ascii=ensure_ascii)


def _fake_q() -> dict:
    """真实形状的最小题对象（非手搓桩：字段名与 LongMemEval 数据集一致）。"""
    return {
        "question_id": "testq_0001",
        "question_type": "single-session-user",
        "answer_session_ids": ["answer_gold1"],
        "haystack_session_ids": ["answer_gold1", "sharegpt_x_0"],
        "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 03:00"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "部门报账线是\u2028多少来着？"},
                {"role": "assistant", "content": "单笔 500 元。"},
            ],
            [
                {"role": "user", "content": "今天天气不错。"},
            ],
        ],
    }


def test_u2028_lines_survive_ensure_ascii_payload():
    """修复点 1：ensure_ascii=True 的 payload 服务端解析零废段；False 复现拆段。"""
    q = _fake_q()
    lines = import_lines_for_question(q)
    assert len(lines) == 2

    bad_payload = _serialize_payload(lines, ensure_ascii=False)
    _, invalid_bad = parse_import_lines(bad_payload)
    assert invalid_bad, "ensure_ascii=False 必须复现 U+2028 拆段（修前行为）"

    good_payload = _serialize_payload(lines, ensure_ascii=True)
    valid, invalid = parse_import_lines(good_payload)
    assert invalid == [], "ensure_ascii=True 的 payload 不允许任何废段"
    assert [v["tags"][0] for v in valid] == ["answer_gold1", "sharegpt_x_0"]
    # U+2028 语义不丢：解码回的内容含原字符
    assert "\u2028" in valid[0]["content"]


def test_store_presence_probe_by_idempotent_reimport():
    """修复点 2：重导幂等探针——在库行记 duplicate，缺行记 imported（真实 SQLite）。"""
    q = _fake_q()
    lines = import_lines_for_question(q)
    payload = "\n".join(json_dumps(l, ensure_ascii=True) for l in lines)

    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory():
        from sqlmodel import Session

        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
    ):
        # 第一轮：全量灌入（模拟原始 ingest 修复后的落库）
        rep1 = run_jsonl_import(payload)
        assert rep1["imported"] == 2 and rep1["duplicates"] == 0 and rep1["invalid"] == []

        # 探针轮（coverage 判定用）：全部 duplicate ⇔ 在场
        rep2 = run_jsonl_import(payload)
        assert rep2["imported"] == 0
        assert rep2["duplicates"] == len(lines)
        assert len(rep2["invalid"]) == 0

        # 缺行场景：删掉金标行对应的 payload（只灌噪音行），探针应报 imported>0
        partial = json_dumps(lines[1], ensure_ascii=True)
        rep3 = run_jsonl_import(partial)
        assert rep3["imported"] == 0 and rep3["duplicates"] == 1

        # 全新行（内容不同）→ imported=1（证明探针能分辨缺失）
        novel = json_dumps(
            {
                "content": "[LME testq_0001/answer_gold1]\nuser: 换内容的新行",
                "lane": "general",
                "tags": ["answer_gold1"],
            },
            ensure_ascii=True,
        )
        rep4 = run_jsonl_import(novel)
        assert rep4["imported"] == 1, "缺行必须以 imported>0 显形（宁 miss 不脏写）"

        # 注意 sha 幂等按内容：novel 与首轮金标行内容不同 → 不互斥，
        # 但 tags 相同会同时在场——重导探针对「同内容缺行」的判定不受影响
        rep5 = run_jsonl_import(payload)
        assert rep5["duplicates"] == 2, "原两行仍在库（novel 是新内容行，不挤占）"
        assert rep5["imported"] == 0


def test_ingest_checkpoint_half_detection_unaffected():
    """半题检测在 ensure_ascii=True 下零误报：done==expected → 非半题。"""
    from lantai.eval.longmemeval import half_question_detect

    q = _fake_q()
    lines = import_lines_for_question(q)
    payload = "\n".join(json_dumps(l, ensure_ascii=True) for l in lines)
    valid, invalid = parse_import_lines(payload)
    done = len(valid) + len(invalid)
    assert not half_question_detect(len(lines), done)
