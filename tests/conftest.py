"""
测试共享入口：
1. otel stub——chromadb 依赖的 OTLP gRPC exporter 在当前开发环境版本错配，
   测试环境打桩绕过（生产环境与本测试无关）。
2. 内存 SQLite fixture——参数建议模块测试复用。
"""
import sys
import types

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine


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
    import remembrance.models.tables  # noqa: F401  注册全部表
    import remembrance.storage.db as db_module
    from remembrance.core.settings import settings
    from remembrance.parameters.registry import get_adjustable_names

    names = get_adjustable_names()
    saved = {n: getattr(settings, n) for n in names}

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", session_factory)
        yield session_factory, engine

    # 恢复 settings 白名单参数
    for n, v in saved.items():
        setattr(settings, n, v)
