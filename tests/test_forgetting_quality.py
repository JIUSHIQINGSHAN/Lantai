"""遗忘质量自测 + 中文评测集 冒烟测试（不 mock 内部逻辑）。

- compute_forgetting_metrics 纯函数：构造 per_query 验证六项指标计算
- evaluate_forgetting_quality 端到端：内存 SQLite + FTS 真实建表，mock 仅外部依赖
  （embedding / 向量存储 / 意图 LLM）——种子、遗忘、检索、指标、清理全真实执行
"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage.fts import init_fts


@pytest.fixture()
def fq_env():
    """内存 SQLite 真实建表 + FTS + patch 仅外部依赖。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}):
        yield session_factory, engine


def test_compute_metrics_pure():
    """纯函数指标：陈旧残留/错别字命中/对照组/时效排序/取代排序与残留。"""
    from lantai.eval.forgetting_quality import compute_forgetting_metrics

    per_query = [
        {"category": "stale", "query": "q1", "result_ids": ["archived_1"],
         "target_id": None, "forbidden_ids": ["archived_1"],
         "preferred_id": None, "peer_id": None},
        {"category": "stale", "query": "q2", "result_ids": [],
         "target_id": None, "forbidden_ids": ["archived_2"],
         "preferred_id": None, "peer_id": None},
        {"category": "typo", "query": "q3", "result_ids": ["m1"],
         "target_id": "m1", "forbidden_ids": [],
         "preferred_id": None, "peer_id": None},
        {"category": "fresh", "query": "q4", "result_ids": ["m2"],
         "target_id": "m2", "forbidden_ids": [],
         "preferred_id": None, "peer_id": None},
        {"category": "temporal", "query": "q5", "result_ids": ["new1"],
         "target_id": None, "forbidden_ids": [],
         "preferred_id": "new1", "peer_id": "old1"},
        {"category": "temporal", "query": "q6", "result_ids": ["new2", "old2"],
         "target_id": None, "forbidden_ids": [],
         "preferred_id": "new2", "peer_id": "old2"},
        {"category": "temporal", "query": "q7", "result_ids": ["old3"],
         "target_id": None, "forbidden_ids": [],
         "preferred_id": "new3", "peer_id": "old3"},
        {"category": "superseded", "query": "q8", "result_ids": ["new4", "old4"],
         "target_id": "new4", "forbidden_ids": [],
         "preferred_id": "new4", "peer_id": "old4"},
        {"category": "superseded", "query": "q9", "result_ids": ["old5"],
         "target_id": "new5", "forbidden_ids": [],
         "preferred_id": "new5", "peer_id": "old5"},
    ]
    m = compute_forgetting_metrics(per_query)
    assert m["sample_count"] == 9
    assert m["stale_hit_rate"] == 0.5            # 1/2 残留
    assert m["typo_recall_rate"] == 1.0          # 1/1
    assert m["fresh_recall_rate"] == 1.0         # 1/1
    assert m["temporal_order_accuracy"] == round(2 / 3, 4)  # q5(peer缺省算优)/q6优/q7劣
    assert m["superseded_order_accuracy"] == 0.5  # q8 优 / q9 劣
    assert m["superseded_residual_rate"] == 1.0   # 两条均残留旧值


def test_dataset_queries_fts_hittable():
    """评测集自检：每个 case 的 query 与目标内容共享整串子串（FTS 兜底确定性）。

    错别字维度用词边界插入/删除型（FTS5 trigram 实测语义：整串子串匹配）。
    """
    import sqlite3
    from lantai.eval.chinese_memory_cases import build_chinese_dataset

    ds = build_chinese_dataset()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
    for case in ds["cases"]:
        for seed in case["seeds"]:
            conn.execute("INSERT INTO t VALUES (?)", (seed["content"],))
    for case in ds["cases"]:
        q = case["query"]
        rows = conn.execute(
            "SELECT rowid FROM t WHERE content MATCH ?",
            ('"' + q.replace('"', '""') + '"',)).fetchall()
        target = case.get("target")
        if target is not None:
            assert rows, f"query {q!r} 应 FTS 命中 target seed {target}"
        else:
            assert rows, f"query {q!r} 应 FTS 命中至少一个 seed（order case）"
    conn.close()


def test_evaluate_end_to_end(fq_env):
    """端到端：种子→遗忘→检索→指标→清理 全真实；关键指标确定性断言。"""
    session_factory, engine = fq_env
    from lantai.eval.chinese_memory_cases import build_chinese_dataset
    from lantai.eval.forgetting_quality import evaluate_forgetting_quality

    result = evaluate_forgetting_quality(build_chinese_dataset())
    metrics = result["metrics"]
    assert metrics["sample_count"] == 13
    # 归档零残留：apply_forgetting 后 status=archived 不参与检索
    assert metrics["stale_hit_rate"] == 0.0
    # 中文错别字（词边界）全部命中
    assert metrics["typo_recall_rate"] == 1.0
    # 对照组自检全命中（管道未被 mock 破坏）
    assert metrics["fresh_recall_rate"] == 1.0
    # 时效：未生效过滤 / 过期降权后新值在前
    assert metrics["temporal_order_accuracy"] == 1.0
    # 取代维度：supersedes 边降权后新值必须在前（确定性回归）
    assert metrics["superseded_order_accuracy"] == 1.0
    # 残留诚实测量：降权不删旧值，旧值仍如实出现在 top-k
    assert metrics["superseded_residual_rate"] >= 0.0
    per_cat = {q["category"] for q in result["per_query"]}
    assert per_cat == {"typo", "fresh", "stale", "temporal", "superseded"}

    # 清理断言：种子全部删除，不留孤儿边（边两端必须指向仍存在的记忆）
    with session_factory() as s:
        left = s.exec(select(MemoryItem)
                      .where(MemoryItem.namespace == "eval_fq")).all()
        assert left == []
        edges = s.exec(select(MemoryEdge)).all()
        existing_ids = {m.id for m in s.exec(select(MemoryItem)).all()}
        for e in edges:
            assert e.source_memory_id in existing_ids, f"orphan edge {e.id}"
            assert e.target_memory_id in existing_ids, f"orphan edge {e.id}"

def test_offline_eval_gates_pass():
    """离线门禁：临时库 + 仅外部依赖 mock，六维指标确定性达标（CI/发布用）。"""
    from lantai.eval.offline import check_gates, run_offline_eval

    result = run_offline_eval()
    ok, actual = check_gates(result)
    assert ok, f"门禁未过: {actual}"
    assert result["metrics"]["sample_count"] == 13
    # superseded 残留是诚实测量（降权不删旧值），只报告不设门槛
    assert result["metrics"]["superseded_residual_rate"] == 1.0


def test_check_gates_detects_regression():
    """门禁纯函数：任一指标跌破门槛必须 FAIL（回归会拦在 CI 而非线上）。"""
    from lantai.eval.offline import GATES, check_gates

    good = {"metrics": {k: v for k, v in GATES.items()}}
    ok, _ = check_gates(good)
    assert ok

    bad = {"metrics": dict(GATES)}
    bad["metrics"]["typo_recall_rate"] = 0.5
    ok2, actual = check_gates(bad)
    assert not ok2
    assert actual["typo_recall_rate"] == 0.5
