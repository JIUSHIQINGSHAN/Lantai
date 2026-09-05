"""遗忘质量离线门禁（CI / 发布自证用）。

把「最严格基准」做成可重复命令：临时 SQLite + 真实 FTS5 建表 + 仅 mock 外部
依赖（embedding / 向量存储 / 意图 LLM），真实执行 种子→遗忘→检索→指标→清理。
与 pytest 端到端测试同构但独立可运行，供 `scripts/run_forgetting_quality.py --check`
与发布稿复现使用（测试纪律：mock 仅限外部依赖，内部逻辑全真实）。
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.settings import settings as _settings
from lantai.storage.fts import init_fts


def _install_otel_stub() -> None:
    """chromadb 的 OTLP exporter 在部分 venv 破损：幂等打桩（同 tests/conftest）。"""
    try:
        import opentelemetry.exporter.otlp.proto.grpc.trace_exporter  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    m = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    m.OTLPSpanExporter = object
    grpc_pkg = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc")
    grpc_pkg.trace_exporter = m
    proto_pkg = types.ModuleType("opentelemetry.exporter.otlp.proto")
    proto_pkg.grpc = grpc_pkg
    exporter_pkg = types.ModuleType("opentelemetry.exporter.otlp")
    exporter_pkg.proto = proto_pkg
    otel_pkg = types.ModuleType("opentelemetry.exporter")
    otel_pkg.otlp = exporter_pkg
    for name, mod in [
        ("opentelemetry.exporter", otel_pkg),
        ("opentelemetry.exporter.otlp", exporter_pkg),
        ("opentelemetry.exporter.otlp.proto", proto_pkg),
        ("opentelemetry.exporter.otlp.proto.grpc", grpc_pkg),
        ("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", m),
    ]:
        sys.modules.setdefault(name, mod)


_install_otel_stub()


def run_offline_eval(dataset: dict | None = None, top_k: int = 5) -> dict:
    """临时库 + 仅外部依赖 mock 的确定性评测运行。

    返回 evaluate_forgetting_quality 的完整结果（metrics + per_query）。
    每次调用独立临时 DB，finally 清理种子，不污染真实库。
    """
    from lantai.eval.chinese_memory_cases import build_chinese_dataset
    from lantai.eval.forgetting_quality import evaluate_forgetting_quality
    from lantai.retrieval.hybrid import hybrid_search

    ds = dataset or build_chinese_dataset()
    tmp = Path(tempfile.mkdtemp(prefix="lantai-fq-check-"))
    engine = create_engine(
        f"sqlite:///{tmp / 'eval.db'}",
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
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.retrieval.hybrid.classify_intent",
               return_value={"intent": _settings.DEFAULT_INTENT,
                             "candidate_n": _settings.INTENT_CANDIDATE_SIZES
                             .get(_settings.DEFAULT_INTENT, 10)}):
        return evaluate_forgetting_quality(ds, search=hybrid_search, top_k=top_k)


# 评测集 v1 契约门槛（发布稿同源）：FTS 兜底最严格基准下的确定性底线。
# 改数据集时同步更新；门槛是「可复现自证」主张，不是可调系统参数（不进 settings）。
GATES: dict[str, float] = {
    "stale_hit_rate": 0.0,              # 归档零残留
    "typo_recall_rate": 1.0,            # 中文错别字全命中（FTS trigram）
    "fresh_recall_rate": 1.0,           # 对照组管道自检
    "temporal_order_accuracy": 1.0,     # Chronos 时效排序
    "superseded_order_accuracy": 1.0,   # supersedes 降权后新值在前
}


def check_gates(result: dict) -> tuple[bool, dict[str, float]]:
    """断言门槛；返回 (是否达标, 各指标实际值)。

    superseded_residual_rate 为诚实测量（降权不删旧值），只报告不设门槛。
    """
    metrics = result["metrics"]
    actual = {k: metrics.get(k) for k in GATES}
    ok = all(actual[k] == expected for k, expected in GATES.items())
    return ok, actual
