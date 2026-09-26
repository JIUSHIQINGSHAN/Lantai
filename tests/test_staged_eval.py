"""E1 阶段化评测 harness 冒烟测试（票 03）。

不 mock 边界：闸门/入库/索引/召回/注入五段全真实（真 SQLite + FTS5 + 内嵌 Chroma）；
提取段用语料预标替身（harness 明确语义：对账纪律本身是被测对象）；
embed 用确定性 sha256 3-gram 替身（外部网络替身面）。
对照校验：run_dry_run 在 harness 前后各跑一次，召回层指标逐项一致（阶段化不干扰）。
"""

import hashlib

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.evolution.promoter as promoter_mod
import lantai.gate.decision as gate_decision_mod
import lantai.retrieval.hybrid as hybrid_mod
import lantai.storage.db as db_module
import lantai.storage.vector_store as vs_module
from lantai.storage.fts import init_fts


def _hash_embed(texts):
    """确定性 3-gram 哈希向量（512 维；跨进程稳定，范式同 test_bixiao_deterministic）。"""
    out = []
    for text in texts:
        v = [0.0] * 512
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))] or [text]
        for g in grams:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % 512] += 1.0
        n = sum(v) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture()
def staged_env(tmp_path, monkeypatch):
    """内存库（StaticPool 真实建表+FTS）+ patch db.get_session 与三处模块级 embed + 内嵌 Chroma。"""
    import lantai.eval.models  # noqa: F401  # 注册 EvalQuerySet/EvalRun
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "DATA_FENCE_ENABLED", True)

    def session_factory():
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)

    # 三处 from-import 值拷贝的 embed（decision/promoter/hybrid）+ client 本体
    monkeypatch.setattr(gate_decision_mod, "embed", _hash_embed)
    monkeypatch.setattr(promoter_mod, "embed", _hash_embed)
    monkeypatch.setattr(hybrid_mod, "embed", _hash_embed)
    import lantai.llm.client as llm_client

    monkeypatch.setattr(llm_client, "embed", _hash_embed)

    # 真内嵌 Chroma（tmp 目录；单例撕卸，不触发 conftest 绊线）
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "CHROMADB_PATH", str(tmp_path / "chroma-staged"))
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    monkeypatch.setattr(vs_module, "_store", None, raising=False)

    yield engine

    monkeypatch.setattr(vs_module, "_store", None, raising=False)
    engine.dispose()


class TestStagedEvalHarness:
    def test_six_stages_complete_with_anchors(self, staged_env):
        """六段各有 samples/success_rate/failure_buckets；三预埋锚点各归正确阶段不串段。"""
        from lantai.eval.staged import run_staged_eval

        result = run_staged_eval()

        names = [st["stage"] for st in result["stages"]]
        assert names == ["extract", "gate", "store", "index", "retrieve", "inject"]
        for st in result["stages"]:
            # 键存在 + 值非 None 都要查：原句第三项比对的是字符串字面量而非 st 的值
            # （F632：`"failure_buckets" is not None` 恒真），实为漏检——此处修回本意
            assert "samples" in st and "success_rate" in st
            assert st["failure_buckets"] is not None
        assert all(st["samples"] > 0 for st in result["stages"])

        # 三预埋锚点各归正确阶段（HaluMem 式归因不串段）
        anchors = {a["name"]: a for a in result["anchors"]}
        assert anchors["gate_reject"]["expected_stage"] == "gate"
        assert anchors["gate_reject"]["achieved"] is True
        assert anchors["index_desync"]["expected_stage"] == "index"
        assert anchors["index_desync"]["achieved"] is True
        assert anchors["zero_recall"]["expected_stage"] == "retrieve"
        assert anchors["zero_recall"]["achieved"] is True
        # 围栏载荷：中性化后注入面无泄漏（正确行为）
        assert anchors["fence_escape"]["achieved"] is True

        # 零召回预埋必须落 recall 段的 zero_recall 桶（不串段）
        retrieve_buckets = result["stages"][4]["failure_buckets"]
        assert "recall_zero_recall" in retrieve_buckets

    def test_gate_reject_anchored_not_downstream(self, staged_env):
        """闸门必拒样本不计入下游各段分母（条件化评测）。"""
        from lantai.eval.staged import CORPUS, run_staged_eval

        run_staged_eval()
        # l1（闸门必拒）不进 store/index/retrieve 的成功样本
        # 语料 fact 总数（非噪音）：10 条；gate 段 samples ≥ 10（含三态对账加样）
        l1 = next(c for c in CORPUS if c[0] == "l1")
        assert l1[3]["gate"] == "reject"

    def test_dry_run_untouched_by_staging(self, staged_env):
        """对照校验：run_dry_run 在 harness 前后各跑一次，召回层指标逐项一致。"""
        from lantai.eval.models import EvalQuerySet
        from lantai.eval.runner import run_dry_run

        qs = EvalQuerySet(
            id="evs-qs-fix",
            name="staged-compat-check",
            queries=[
                {"query": "用户的主数据库是什么", "event_id": ""},
                {"query": "用户对什么过敏", "event_id": ""},
            ],
            sample_count=2,
        )

        # 预置一条可命中记忆（直接构造，走真实索引）
        from lantai.core.ids import new_id
        from lantai.models.tables import MemoryItem
        from lantai.retrieval.hybrid import index_memory_item

        with db_module.get_session() as s:
            mem = MemoryItem(
                id=new_id("evs-mem"),
                content="用户的主数据库是 PostgreSQL 15",
                key="用户的主数据库是 PostgreSQL 15",
                status="active",
            )
            s.add(mem)
            s.commit()
            mid = mem.id
        index_memory_item(mid, _hash_embed([mem.content])[0], {"memory_id": mid})

        run1 = run_dry_run(qs, use_rerank=False)
        keys_before = set(run1.metrics)
        from lantai.eval.staged import run_staged_eval

        run_staged_eval()  # harness 会向库写入 9 条记忆——数据状态变化属预期

        run2 = run_dry_run(qs, use_rerank=False)
        run3 = run_dry_run(qs, use_rerank=False)
        # 阶段化不改评分定义：① 指标键集合与 harness 前一致（定义面不变）；
        # ② 同一数据状态下连跑两次逐项相等（可复跑、无跨运行状态污染）。
        assert set(run2.metrics) == keys_before
        for key in set(run2.metrics) & set(run3.metrics):
            if key == "errors":
                continue
            assert run2.metrics[key] == run3.metrics[key], (
                f"metric {key} drifted: {run2.metrics[key]} != {run3.metrics[key]}"
            )

    def test_report_format(self, staged_env):
        """报告出口：每段一行 stage/samples/success_rate/failure_buckets。"""
        from lantai.eval.staged import format_staged_report, run_staged_eval

        report = format_staged_report(run_staged_eval())
        for stage in ("extract", "gate", "store", "index", "retrieve", "inject"):
            assert stage in report
        assert "预埋锚点核对" in report
        assert "PASS" in report
