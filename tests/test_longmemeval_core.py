"""LongMemEval 评测核心不 mock 冒烟（票 02 步骤④，测试纪律）。

真实构造最小输入直调 lantai.eval.longmemeval 的核心函数（纯函数真实执行，
不 mock 任何东西）；数据面用真实下载数据集的前几题（真实形状，非手搓桩）。
"""

import json
from pathlib import Path

import pytest

from lantai.eval.longmemeval import (
    aggregate_metrics,
    assert_prereg_subset,
    config_hash,
    coverage_audit,
    half_question_detect,
    import_lines_for_question,
    load_dataset,
    parse_lme_date,
    preregistered_ids,
    result_session_ids,
    session_recall_hit,
)

DATASET = Path("data/longmemeval/longmemeval_s_cleaned.json")

pytestmark = pytest.mark.skipif(not DATASET.exists(), reason="数据集未下载（本地跑测专用）")


@pytest.fixture(scope="module")
def data():
    return load_dataset(DATASET)


def test_load_and_prereg_smoke(data):
    """真实数据集加载 + 预注册抽样可复现 + 子集断言主路径。"""
    ids_a = preregistered_ids(data, seed=20261026, n=50)
    ids_b = preregistered_ids(data, seed=20261026, n=50)
    assert ids_a == ids_b, "同种子必须可复现"
    assert len(ids_a) == 50 and len(set(ids_a)) == 50
    assert_prereg_subset(ids_a, data)  # ⊆ 断言过
    with pytest.raises(RuntimeError, match="拒跑"):
        assert_prereg_subset(ids_a + ["nonexistent-id"], data)
    # 缩题只减不换：前缀即缩
    assert preregistered_ids(data, seed=20261026, n=25) == ids_a[:25]


def test_import_lines_for_question_smoke(data):
    """题 → 导入行主路径：lane 在默认集合内（API ACL 可入）、tags 会话映射、时间戳解析。

    题级隔离已改为每题独立评测库（scripts/lme_run.py 每题 LANTAI_HOME），
    不再依赖 per-question lane（X-API-Key 主体的 allowed_lanes=DEFAULT_LANES
    会拒 lme_* lane 行）。
    """
    q = data[0]
    lines = import_lines_for_question(q)
    assert len(lines) == len(q["haystack_sessions"])
    assert all(l["lane"] == "general" for l in lines)  # DEFAULT_LANES 内，import ACL 放行
    assert [l["tags"][0] for l in lines] == q["haystack_session_ids"]
    assert all(l["content"].startswith(f"[LME {q['question_id']}/") for l in lines)
    # 每行内容含 role 前缀的 transcript；同 sid 跨题前缀保证内容唯一
    other_q = next(x for x in data if x["question_id"] != q["question_id"])
    other_lines = import_lines_for_question(other_q)
    shared = set(q["haystack_session_ids"]) & set(other_q["haystack_session_ids"])
    if shared:
        sid = shared.pop()
        c1 = next(l["content"] for l in lines if l["tags"][0] == sid)
        c2 = next(l["content"] for l in other_lines if l["tags"][0] == sid)
        assert c1 != c2, "跨题共享 session 内容必须因前缀唯一（防 sha 幂等独占）"
    assert parse_lme_date("2023/05/20 (Sat) 02:21") == "2023-05-20T02:21:00+00:00"


def test_mapping_recall_audit_aggregate_smoke(data):
    """结果→会话映射、recall@K、覆盖审计、半题检测、聚合主路径。"""
    q = data[0]
    gold = q["answer_session_ids"]
    # 构造含金标与噪音的检索结果（真实 result 形状）
    noise_sid = next(s for s in q["haystack_session_ids"] if s not in gold)
    results = [
        {"memory": {"tags": [gold[0]], "content": f"[LME {q['question_id']}/{gold[0]}]\n…"}},
        {"memory": {"tags": [noise_sid], "content": "…"}},
        {"memory": {"content": f"[LME {q['question_id']}/{gold[0]}]"}},  # 无 tags 走前缀回落
    ]
    sids = result_session_ids(results)
    assert sids == [gold[0], noise_sid, gold[0]]
    assert session_recall_hit(q, sids, 2) is True
    assert session_recall_hit(q, [noise_sid], 5) is False

    audit = coverage_audit(q, q["haystack_session_ids"])
    assert audit["coverage_rate"] == 1.0 and not audit["missing"]
    audit_bad = coverage_audit(q, [s for s in q["haystack_session_ids"] if s != gold[0]])
    assert audit_bad["coverage_rate"] < 1.0

    assert half_question_detect(50, 49) is True
    assert half_question_detect(50, 50) is False

    records = [
        {
            "question_id": "a",
            "question_type": "multi-session",
            "gate_skipped": False,
            "hit_at_2": True,
            "hit_at_5": True,
            "coverage_full": True,
            "abstention": False,
        },
        {
            "question_id": "b",
            "question_type": "temporal-reasoning",
            "gate_skipped": True,
            "hit_at_2": False,
            "hit_at_5": False,
            "coverage_full": True,
            "abstention": False,
        },
        {"question_id": "c", "question_type": "abstention", "abstention": True},
    ]
    m = aggregate_metrics(records)
    assert m["n"] == 3 and m["abstention_n"] == 1
    assert m["recall_at_2_all"] == 0.5  # 跳过=miss 计入分母（1/2 非 abstention）
    assert m["recall_at_2_conditional_gate_pass"] == 1.0  # 条件召回 1/1
    assert m["by_type"]["multi-session"]["descriptive_only"] is True


def test_config_hash_stable():
    c = {"revision": "r1", "config": "vector-only", "env": {"A": 1}}
    assert config_hash(c) == config_hash(dict(reversed(list(c.items()))))
    assert config_hash(c) != config_hash({**c, "config": "bm25-only"})
    assert len(config_hash(c)) == 16


def test_prereg_file_in_repo_matches():
    """入仓预注册清单与种子重算一致 + ⊆ 真实数据集（运行时断言同款）。"""
    p = Path("docs/benchmarks/longmemeval-2026-10/preregistration.json")
    if not p.exists():
        pytest.skip("预注册未生成")
    prereg = json.loads(p.read_text(encoding="utf-8"))
    data = load_dataset(DATASET)
    # 缩题只减不换：question_ids 保留全量清单（嵌套前缀），n 取前缀生效集
    assert prereg["question_ids"] == preregistered_ids(
        data, seed=prereg["seed"], n=len(prereg["question_ids"])
    )
    assert len(prereg["question_ids"]) >= prereg["n"]
    assert prereg["question_ids"][: prereg["n"]] == preregistered_ids(
        data, seed=prereg["seed"], n=prereg["n"]
    )
    assert_prereg_subset(prereg["question_ids"], data)
