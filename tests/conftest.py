"""
测试共享入口：
1. otel stub——chromadb 依赖的 OTLP gRPC exporter 在当前开发环境版本错配，
   测试环境打桩绕过（生产环境与本测试无关）。
2. 内存 SQLite fixture——参数建议模块测试复用。
3. LLM 假 key——lantai.llm.client 模块级实例化 OpenAI client，CI 无真实 key；
   测试全部 mock chat_json/embed，假 key 仅保证 import 不炸，不会真调 API。
"""
import os
import sys
import types

# 必须在任何 lantai.* import 之前生效（client.py 模块级 OpenAI() 需要非空 key）
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.settings import settings

settings.FEATURE_OBSIDIAN = True
settings.FEATURE_WIKI = True
settings.FEATURE_WORK_ITEMS = True
settings.FEATURE_VISION = True
settings.FEATURE_TERMINAL = True


def _install_otel_stub() -> None:
    """chromadb 会 import OTLPSpanExporter；本地环境该依赖破损，测试时打桩。"""
    try:
        import opentelemetry.exporter.otlp.proto.grpc.trace_exporter  # noqa: F401
        return  # 环境正常，不干预
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


@pytest.fixture(scope="function")
def param_env():
    """
    内存 SQLite + 真实建表 + patch db.get_session。
    返回 (session_factory, engine)；测试用 session_factory() 开新会话。
    teardown 恢复 settings 白名单参数（审批测试会原位修改单例）。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.parameters.trust_models  # noqa: F401  注册信号/矛盾表
    import lantai.storage.db as db_module
    from lantai.core.settings import settings
    from lantai.parameters.registry import get_adjustable_names

    names = get_adjustable_names()
    saved = {n: getattr(settings, n) for n in names}

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", session_factory)
        
        # Mock vector store to avoid hitting network/ChromaDB in all DB tests
        import lantai.retrieval.hybrid as hybrid_module
        import lantai.services.memory_service as memory_service
        import lantai.storage.vector_store as vector_store_module
        class DummyVS:
            def search(self, *args, **kwargs): return []
            def search_batch(self, *args, **kwargs): return []
            def add(self, *args, **kwargs): pass
            def update(self, *args, **kwargs): pass
            def delete(self, *args, **kwargs): pass
        dummy_vs = DummyVS()
        mp.setattr(vector_store_module, "get_vector_store", lambda: dummy_vs)
        mp.setattr(hybrid_module, "get_vector_store", lambda: dummy_vs)
        mp.setattr(memory_service, "get_vector_store", lambda: dummy_vs)
        
        import lantai.llm.client as llm_client
        mp.setattr(llm_client, "embed", lambda texts: [[0.1] * 1536 for _ in texts])
        mp.setattr(hybrid_module, "embed", lambda texts: [[0.1] * 1536 for _ in texts])
        mp.setattr(llm_client, "chat_json", lambda *args, **kwargs: {"candidate_n": 5, "lanes": []})
        import lantai.retrieval.intent as intent_module
        mp.setattr(intent_module, "chat_json", lambda *args, **kwargs: {"candidate_n": 5, "lanes": []})
        
        import lantai.retrieval.reranker as reranker_module
        def dummy_rerank(q, docs, k):
            return [{"index": i, "score": 0.9, "document": d} for i, d in enumerate(docs)]
        mp.setattr(reranker_module, "rerank", dummy_rerank)
        mp.setattr(hybrid_module, "rerank", dummy_rerank)
        
        yield session_factory, engine

    # 恢复 settings 白名单参数
    for n, v in saved.items():
        setattr(settings, n, v)

@pytest.fixture(autouse=True)
def _no_background_scheduler(monkeypatch):
    """全量顺序污染防护：测试进程内关闭真实后台调度器。

    api_server 的 lifespan 会 start_scheduler() 启动 BackgroundScheduler，
    其 evolve/ingest/forget 等 worker 会对真实库做真实 LLM 调用（拖慢全量、
    写脏真实库），且 stop_scheduler(wait=False) 不等待在跑任务，会留下僵尸
    线程干扰后续测试——全量顺序偶发失败的主要污染源。测试只验证 HTTP/业务
    行为，不依赖调度器，因此统一置空 start_scheduler。
    """
    import api_server
    monkeypatch.setattr(api_server, "start_scheduler", lambda: None)
