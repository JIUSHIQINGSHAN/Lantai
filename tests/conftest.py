"""
测试共享入口：
1. otel stub——chromadb 依赖的 OTLP gRPC exporter 在当前开发环境版本错配，
   测试环境打桩绕过（生产环境与本测试无关）。
2. 内存 SQLite fixture——参数建议模块测试复用。
3. LLM 假 key——lantai.llm.client 模块级实例化 OpenAI client，CI 无真实 key；
   假 key 仅保证 import 不炸。
4. 数据目录隔离（`_isolate_data_dir`）——`db.engine` 在 import 时就按
   `settings.DATABASE_URL` 建好，而本地 `.env` 的 `LANTAI_HOME` 指向**真实开发库**；
   不隔离则测试读写宿主真实 SQLite/ChromaDB（实证：跑全量会改真实库 mtime，
   且偶发顺序污染）。详见 `.scratch/test-env-parity/issues/02-*.md`。
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
            def search(self, *args, **kwargs):
                return []

            def search_batch(self, *args, **kwargs):
                return []

            def add(self, *args, **kwargs):
                pass

            def update(self, *args, **kwargs):
                pass

            def delete(self, *args, **kwargs):
                pass

            def has(self, *args, **kwargs):
                return True

        dummy_vs = DummyVS()
        mp.setattr(vector_store_module, "get_vector_store", lambda: dummy_vs)
        mp.setattr(hybrid_module, "get_vector_store", lambda: dummy_vs)
        mp.setattr(memory_service, "get_vector_store", lambda: dummy_vs)

        import lantai.llm.client as llm_client

        mp.setattr(llm_client, "embed", lambda texts: [[0.1] * 1536 for _ in texts])
        mp.setattr(hybrid_module, "embed", lambda texts: [[0.1] * 1536 for _ in texts])
        mp.setattr(llm_client, "chat_json", lambda *args, **kwargs: {"candidate_n": 5, "lanes": []})
        import lantai.retrieval.intent as intent_module

        mp.setattr(
            intent_module, "chat_json", lambda *args, **kwargs: {"candidate_n": 5, "lanes": []}
        )

        import lantai.retrieval.reranker as reranker_module

        def dummy_rerank(q, docs, k):
            return [{"index": i, "score": 0.9, "document": d} for i, d in enumerate(docs)]

        mp.setattr(reranker_module, "rerank", dummy_rerank)
        mp.setattr(hybrid_module, "rerank", dummy_rerank)

        yield session_factory, engine

    # 恢复 settings 白名单参数
    for n, v in saved.items():
        setattr(settings, n, v)


# ── 模块级状态绊线（常驻护栏）──────────────────────────────────
# 背景：db.get_session 曾被测试"还原"成 None（episode_credit finally 置 None）、
# 多个 fixture 手写 try/finally 与测试内 monkeypatch 叠加时因撕卸顺序把陈旧值
# 回写泄漏（test_scene 实证，139 例连坐）——均造成下游测试 TypeError 顺序污染。
# 本绊线在每个测试 setup 时校验 lantai.storage.db 模块级 get_session/engine 身份，
# 被改脏则在下一个测试点名前置测试。hook 时机在全部 fixture 撕卸之后，无顺序竞争。
_ORIG_GET_SESSION = None
_ORIG_ENGINE = None
_PREV_TEST = {"id": ""}


def pytest_configure(config):
    global _ORIG_GET_SESSION, _ORIG_ENGINE
    import lantai.storage.db as dbm

    _ORIG_GET_SESSION = dbm.get_session
    _ORIG_ENGINE = dbm.engine


def pytest_runtest_setup(item):
    import lantai.storage.db as dbm

    if dbm.get_session is not _ORIG_GET_SESSION:
        raise AssertionError(
            f"前置测试 {_PREV_TEST['id']} 污染了 lantai.storage.db.get_session"
            f"（未还原：{_ORIG_GET_SESSION!r} → {dbm.get_session!r}）"
        )
    if dbm.engine is not _ORIG_ENGINE:
        raise AssertionError(
            f"前置测试 {_PREV_TEST['id']} 污染了 lantai.storage.db.engine"
            f"（未还原：{_ORIG_ENGINE!r} → {dbm.engine!r}）"
        )


# ── 数据目录隔离（票 02）───────────────────────────────────────
# 背景：本地 .env 的 LANTAI_HOME 指向真实开发库，而 db.engine 在 import 时就按
# settings.DATABASE_URL 建好——测试因此读写宿主真实 SQLite/ChromaDB。
# 实证：跑全量会改真实 .chromadb 目录 mtime；且偶发顺序污染（1276 passed 1 failed
# → 隔离后 1277 passed 0 failed）。
# 隔离后 db.engine 换成临时库，故绊线的基线须随之更新为「隔离后的 engine」，
# 否则每个测试都会误报「engine 被污染」。


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir(tmp_path_factory):
    """测试数据目录隔离：SQLite + ChromaDB 全落 pytest 临时区，不碰宿主真实库。

    - `settings.DATABASE_URL` / `CHROMADB_PATH` / `LANTAI_HOME` 重指到临时目录
      （`LANTAI_HOME` 也一起改，防止别处再从它推导路径）。
    - `db.engine` 重指到临时库（它在 import 时已按旧 URL 建好，只改 settings 无效）。
    - session 级：一个测试进程共用一个隔离库，跑完即随 tmp 清理。

    目录必须**先建好**再指过去——SQLite 不会自动建父目录，指向不存在的目录会
    `unable to open database file`（实测 45 例 setup 连坐）。
    """
    global _ORIG_ENGINE

    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module

    home = tmp_path_factory.mktemp("lantai_test_home")
    home.mkdir(parents=True, exist_ok=True)
    db_file = home / "remembrance.db"
    db_file.touch()  # SQLite 不会自动建父目录/文件

    settings.LANTAI_HOME = str(home)
    settings.DATABASE_URL = f"sqlite:///{db_file}"
    settings.CHROMADB_PATH = str(home / ".chromadb")

    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    db_module.engine = engine
    # 绊线基线随之更新：隔离后的 engine 即「合法身份」。
    # ⚠ 这两行必须成对出现：只更新 _ORIG_ENGINE 而不重定向 db_module.engine
    # （或反之）会让每个测试被绊线误报「前置测试污染了 db.engine」
    # ——变异验证时实测如此（10 例连坐）。改动此处请连同验证。
    _ORIG_ENGINE = engine

    yield engine

    engine.dispose()


# ── 外部 LLM 调用统一替身（票 01，方案甲）──────────────────────
# 背景：本地 .env 有真 OPENAI_API_KEY，CI 只有 conftest 的假 key "test-key"。
# 未 patch 的 embed/chat_json 绑定因此**真连 API**：本地成功走主路径，CI 拿
# AuthenticationError 被 except 吞掉走降级路径——两个环境跑的不是同一条路。
# spy 实证（2026-09-27 全量）：11 个产品代码调用点、162 次真实调用。
#
# 方案甲：在 conftest 统一替身（而非逐测试补 patch）——一处收口，新测试自动继承。
# 只 patch 源模块不够：`from lantai.llm.client import embed` 会在各模块**各存一份
# 绑定**，必须连模块级 import 点一起替（函数内 import 则自动跟随源 patch，无需列举）。
_LLM_MODULE_BINDINGS = (
    "lantai.eval.answer_quality",
    "lantai.evolution.promoter",
    "lantai.evolution.proposer",
    "lantai.evolution.reflector",
    "lantai.gate.contradiction",
    "lantai.gate.decision",
    "lantai.gate.scorer",
    "lantai.parameters.advisor",
    "lantai.parsing.extractor",
    "lantai.retrieval.hybrid",
    "lantai.retrieval.intent",
    "lantai.services.auto_triage_service",
    "lantai.services.consolidation_service",
    "lantai.services.distill_service",
    "lantai.services.memory_service",
    "lantai.services.mem_command",
    "lantai.services.record_ops_service",
    "lantai.services.refine_service",
    "lantai.services.vision_service",
)

# 假向量维度：与真实 EMBED_MODEL(bge-m3)=1024 一致，避免「维度不符」把调用点
# 提前踢进 except（那会让替身失去意义——见 test_env_isolation 的相关冒烟）。
_FAKE_EMBED_DIM = 1024


def _fake_embed(texts):
    return [[0.1] * _FAKE_EMBED_DIM for _ in texts]


def _fake_chat_json(*args, **kwargs):
    """LLM 结构化输出替身：返回空 dict，让调用方走各自的「无内容」分支。

    刻意不给业务字段（summary/claims/...）：那会让测试依赖替身的返回值，
    把「LLM 恰好返回什么」变成隐式契约。需要特定返回值的测试自行 patch
    （晚于本 fixture 生效，撕卸后自动还原）——既有测试已是这个范式。
    """
    return {}


def _fake_vision_caption(media_url: str) -> str:
    return "vision stub"


@pytest.fixture(autouse=True)
def _stub_external_llm(monkeypatch):
    """统一替身全部外部 LLM 调用点（源 + 模块级绑定），测试不再真连 API。

    需要特定 LLM 返回值的测试在测试体内自行 patch 对应模块的属性——晚于本
    fixture 生效，撕卸后自动还原（既有 `test_distill` / `test_e2e` 等即此范式）。
    """
    import lantai.llm.client as client_mod

    monkeypatch.setattr(client_mod, "embed", _fake_embed)
    monkeypatch.setattr(client_mod, "chat_json", _fake_chat_json)
    monkeypatch.setattr(client_mod, "vision_caption", _fake_vision_caption)

    import importlib

    for mod_name in _LLM_MODULE_BINDINGS:
        mod = importlib.import_module(mod_name)
        for attr in ("embed", "chat_json", "vision_caption"):
            if hasattr(mod, attr):
                fake = {
                    "embed": _fake_embed,
                    "chat_json": _fake_chat_json,
                    "vision_caption": _fake_vision_caption,
                }[attr]
                monkeypatch.setattr(mod, attr, fake)

    # 两个「import as」形态的模块：属性名是 llm_client 而非 embed/chat_json
    for mod_name in ("lantai.services.auto_triage_service", "lantai.services.refine_service"):
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "llm_client"):
            monkeypatch.setattr(mod.llm_client, "embed", _fake_embed)
            monkeypatch.setattr(mod.llm_client, "chat_json", _fake_chat_json)


def pytest_runtest_teardown(item, nextitem):
    _PREV_TEST["id"] = item.nodeid


@pytest.fixture(autouse=True)
def _no_background_scheduler(monkeypatch):
    """全量顺序污染防护：测试进程内关闭真实后台调度器。

    api_server 的 lifespan 会 start_scheduler() 启动 BackgroundScheduler，
    其 evolve/ingest/forget 等 worker 会对真实库做真实 LLM 调用（拖慢全量、
    写脏真实库），且 stop_scheduler(wait=False) 不等待在跑任务，会留下僵尸
    线程干扰后续测试——全量顺序偶发失败的主要污染源。测试只验证 HTTP/业务
    行为，不依赖调度器，因此统一置空 start_scheduler。
    """
    from lantai.api import app as api_server

    monkeypatch.setattr(api_server, "start_scheduler", lambda: None)
    # 司天遥测（ADR-0044）落库线程同理不进测试：offer/flush 保持真实（测试可显式
    # flush_telemetry() 验证），只掐掉后台线程——它会跨线程复用同一个 SQLite 连接。
    import lantai.observability.telemetry as telemetry_module

    monkeypatch.setattr(telemetry_module.TelemetryWriter, "start", lambda self: None)


@pytest.fixture(autouse=True)
def _sanitize_api_key(monkeypatch):
    """DEV MODE 免认证测试类与部署配置互斥：清空 settings.API_KEY。

    仓库根 .env（部署用，gitignored）经 settings 的 env_file 加载会把 API_KEY
    带进测试进程，dev_mode_allowed() 随即拒绝 DEV MODE，全量 29 文件 70 例 401
    （2026-09-20 实证）。需要非空 API_KEY 的用例在测试体内自行 monkeypatch
    覆盖（晚于本 fixture 生效，撕卸后自动还原）。
    """
    monkeypatch.setattr(settings, "API_KEY", "")


@pytest.fixture(autouse=True)
def _disable_reranker(monkeypatch):
    """纵深防御：宿主 .env 的 RERANKER_ENABLED 与代码默认值不一致，测试内一律关闭。

    背景：`RERANKER_ENABLED` 的代码默认是 **True**（settings.py:176），而本地
    `.env` 写 `false`。此前各用例靠手写 `use_rerank=False` 或显式
    `patch.object(settings, "RERANKER_ENABLED", ...)` 单独兜底——任何新用例
    漏写一次，CI（无 .env）上精排就会按代码默认启用，经 httpx 发**真实网络
    请求**（这也是 09-26「本地绿 CI 红」那一类病根：本地靠 .env 恰好关掉了，
    CI 用默认值打开了）。

    需要真实启用精排的用例在测试体内自行 monkeypatch 覆盖（晚于本 fixture
    生效，撕卸后自动还原），与 `_sanitize_api_key` 同一口径。
    """
    monkeypatch.setattr(settings, "RERANKER_ENABLED", False)
