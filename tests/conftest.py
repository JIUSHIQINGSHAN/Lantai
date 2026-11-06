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

import sys
import types

# 必须在任何 lantai.* import 之前生效（client.py 模块级 OpenAI() 需要非空 key）。
# v0.66 票 05 续14（G2）：这里不再裸写 os.environ——全部经 tests/support/harness_state.py
# 这个唯一登记式写入器：写入前登记原值，会话结束由 pytest_unconfigure 复原。
# 且用强制覆盖而非 setdefault：宿主已配真 key 时 setdefault 静默放行，测试就会带着真凭据
# 指向 https://api.openai.com/v1（该 host 在 ALLOWED_API_HOSTS 白名单里，连 validate_api_url
# 都不拦），替身网一漏一个绑定就是真连网、真外传记忆全文。
from tests.support import harness_state

harness_state.apply_env_policy()

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.settings import settings

# 特性开关：测试要五条线全开，但这属于装配期改写生产单例，必须登记并在会话结束复原
# （G2）——否则「全量跑绿」悄悄替出厂默认（FEATURE_OBSIDIAN/FEATURE_VISION 都是 False）
# 背了书，评审看见的配置面是被改写过的。
harness_state.apply_feature_flag_policy(settings)


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

    v0.66 票 01 试过把这里的 patch 改挂到函数级 `monkeypatch` 上（与测试体内
    的 patch 共用一个撤销栈），结果**更糟**：仓库里 14 个 fixture 手写
    `db.get_session = ...` + try/finally 自还（test_injection_guard /
    test_idempotency_receipt / test_runwu_api_smoke / test_tanyi_api_smoke …），
    它们的「原值」是在 param_env 已经打过补丁之后抓的，于是撤销顺序反过来把
    param_env 的替身当原值写回——基线红 20 例当场变 1452 例 setup error。
    保持现状（自带 context，param_env 的撤销发生在这些手写 fixture 之前），
    跨层错位由下面的「规范绑定复位」兜底并在总结里点名。
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

        mp.setattr(llm_client, "embed", _fake_embed)
        mp.setattr(hybrid_module, "embed", _fake_embed)
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
    config.addinivalue_line(
        "markers",
        "mcp_full_surface: 本模块需要 MCP 全量注册表，显式声明 full 档（票 05 续31：默认档已收窄为核心集）",
    )
    import lantai.storage.db as dbm

    _ORIG_GET_SESSION = dbm.get_session
    _ORIG_ENGINE = dbm.engine
    _capture_canonical_bindings()


@pytest.fixture(autouse=True)
def _mcp_full_surface_opt_in(request):
    """`mcp_full_surface` 标记的模块临时拿全量注册表（票 05 续31）。

    默认档收窄后，任何想验证「非核心工具能被广告/执行」的测试都必须**显式声明**
    这一档，而不是靠没人配置时的惯性全量。写入器是登记式 `env_context`（离开 with
    即精确复原，不裸写 os.environ——G2/AST 扫描锁盯着 tests/），所以会话里没打标的
    文件看到的始终是默认核心集；打了标的模块内的 handle() 调用拿到全量面。
    """
    if request.node.get_closest_marker("mcp_full_surface"):
        with harness_state.env_context("LANTAI_MCP_PROFILE", "full"):
            yield
    else:
        yield


def _report_module_state(terminalreporter) -> None:
    if not (_MODULE_STATE_RESIDUE or _MODULE_STATE_RESTORE_FAILURES or _MODULE_STATE_UNCAPTURED):
        return
    blocking = bool(_MODULE_STATE_RESTORE_FAILURES or _MODULE_STATE_UNCAPTURED)
    title = (
        "模块级生产状态：回滚失败或初值缺失（护栏失效，会话已判红）"
        if blocking
        else "模块级生产状态残留（已由护栏逐例回滚，点名供排期）"
    )
    terminalreporter.write_sep("=", title)
    for note in _MODULE_STATE_UNCAPTURED[:10]:
        terminalreporter.write_line("[初值缺失] " + note)
    for nodeid, notes in _MODULE_STATE_RESTORE_FAILURES[:10]:
        terminalreporter.write_line(f"[回滚失败] {nodeid}")
        for note in notes:
            terminalreporter.write_line("    " + note)
    rows = sorted(_MODULE_STATE_RESIDUE.items(), key=lambda kv: -kv[1]["count"])
    for name, row in rows:
        terminalreporter.write_line(
            f"[已回滚] {name} 被 {row['count']} 例造脏，例：{'; '.join(row['examples'])}"
        )


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


# ── 规范绑定复位（v0.66 票 01）───────────────────────────────────
# 背景：`param_env` 用的是自带 `MonkeyPatch.context()`（不能改成共享 monkeypatch，
# 理由见它自己的 docstring），而测试体内的 `monkeypatch.setattr(hybrid, ...)` 挂在
# **另一个撤销栈**上。两层叠加时后撤销的那层会把「另一层的替身」当作原值写回，
# 并把替身永久留在进程里——实证：`test_fence.py` 两例之后
# `lantai.retrieval.hybrid.get_vector_store` 一直是假向量库，检索静默走空替身，
# staged-eval 家族 8 例连坐（单跑永远绿，全量必红）。
# 对策：`pytest_configure`（早于任何 fixture 打补丁）抓一次规范绑定快照，
# 每个测试的**全部 fixture 撕卸之后**（hookwrapper + tryfirst）比对并复位；
# 谁留的脏值记进台账，terminal summary 点名——复位是兜底，不是藏污。
_GUARDED_BINDINGS = (
    ("lantai.retrieval.hybrid", "get_vector_store"),
    ("lantai.retrieval.hybrid", "embed"),
    ("lantai.retrieval.hybrid", "rerank"),
    ("lantai.storage.vector_store", "get_vector_store"),
    ("lantai.services.memory_service", "get_vector_store"),
    ("lantai.retrieval.reranker", "rerank"),
    ("lantai.gate.decision", "embed"),
    ("lantai.gate.scorer", "embed"),
    ("lantai.llm.client", "embed"),
    ("lantai.llm.client", "chat_json"),
)
_CANONICAL_BINDINGS = {}
_BINDING_RESIDUE = []

# 申报过的「污染哨兵」：test_isolation_shared_state.py::test_3a 故意造出两层撤销栈错位
# （由 test_3b 验证受害者不受损），用来证明本护栏真的有效。除它以外的残留一律判红——
# 护栏复位是兜底，不能因为横幅每次都出现就被当成噪音（票 05 续8）。
DECLARED_POLLUTION_SENTINELS = frozenset(
    {"tests/test_isolation_shared_state.py::test_3a_overlay_patch_on_param_env_target"}
)


def unexpected_binding_residue(residue):
    """筛出未申报的模块绑定残留：返回空列表才意味着没人依赖兜底复位。"""
    return [
        (nodeid, notes) for nodeid, notes in residue if nodeid not in DECLARED_POLLUTION_SENTINELS
    ]


def _capture_canonical_bindings():
    """抓规范绑定快照：此刻还没有任何 fixture/测试改过这些属性。"""
    import importlib

    for mod_name, attr in _GUARDED_BINDINGS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, attr):
            _CANONICAL_BINDINGS[(mod_name, attr)] = getattr(mod, attr)


def _repair_canonical_bindings(nodeid):
    """把被测试遗留的模块绑定复位到规范实现，并记台账（只记不改断言）。"""
    if not _CANONICAL_BINDINGS:
        return
    dirty = []
    for (mod_name, attr), canonical in _CANONICAL_BINDINGS.items():
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        current = getattr(mod, attr, None)
        if current is canonical:
            continue
        setattr(mod, attr, canonical)
        dirty.append(f"{mod_name}.{attr}: 遗留 {current!r} 已复位为 {canonical!r}")
    if dirty:
        _BINDING_RESIDUE.append((nodeid, dirty))


# ── 模块级生产状态逐例隔离（票 05 续22，G2 的门禁盲区）───────────────────
# 上面的绑定绊线只看「函数对象有没有被换掉」，harness_state 只登记 env 与
# settings 字段：两者都不看**模块级容器的内容**，也不看**懒单例里攒下的状态**。
# 实测证据（同一进程会话首/尾快照差分：`.scratch/gate/r22/smain.start.json` 与
# `smain.finish.json`，那一轮 2857 passed 全绿）——结束时下面这些生产模块级状态
# 仍带着测试造出来的内容，而生产代码会把它们当决策输入读：
#   * `cehou._SHADOW_WINDOWS` 0→9 条，键名就是 `win-svc-api-test-01` 这类测试自
#     用 id，而 cezhou 聚合会遍历全表；
#   * `scheduler.WORKER_LAST_RUN` 0→6 条，`get_last_run()` 拿它当「DB 查不到时
#     的兜底真值」——前序测试的 stamp 会让后续判定「刚跑过」而静默跳过；
#   * `prefilter._PATTERN_CACHE` 键没变、**值被原地改掉**（只比对象身份的检查抓
#     不到），`_KEYWORD_WARNED` 是「只吼一次」的抑制位；
#   * `mcp._guard_instance._states` 留着 3 个测试调用指纹（mcp.py:3777 复用该单例，
#     断路状态 `open_until` 会跨测试放行 -32005）；
#   * `metrics._collector._records` / `telemetry._writer._pending`/`_seen` 攒着前序
#     测试的请求记录；
#   * `kaogong._LATEST_KAOGONG_REPORT` / `consolidation_service._LAST_CONSOLIDATION_REPORT`
#     带着测试跑出来的时间戳与计数——`test_consolidation.py:838` 名义上验「初值契
#     约」，全量顺序里读到的却是上一个测试运行后的报告，顺序 A/B 不等价。
# 口径与 `_reset_gate_hot_cache`、`_isolate_app_dependency_overrides` 一致：每例
# 从出厂初值冷启动，需要热态语义的用例自行预填；容器**原地**回滚（生产代码可能
# 持有同一个 dict 引用，只改变量指向等于没回滚）。回滚不生效或初值抓不到就判红，
# 不做「看着像护栏、实际在空转」的假隔离。

_GUARDED_MODULE_STATE = (
    ("lantai.cognition.cehou", "_SHADOW_WINDOWS"),
    ("lantai.core.scheduler", "WORKER_LAST_RUN"),
    ("lantai.gate.prefilter", "_PATTERN_CACHE"),
    ("lantai.gate.prefilter", "_KEYWORD_WARNED"),
    ("lantai.cognition.kaogong", "_LATEST_KAOGONG_REPORT"),
    ("lantai.services.consolidation_service", "_LAST_CONSOLIDATION_REPORT"),
    ("lantai.api.routes_health", "_git_sha_cache"),
    ("lantai.parameters.runtime", "_last_applied_revision"),
    ("lantai.cli.mcp", "_guard_instance"),
    ("lantai.llm.client", "_llm_gate"),
    ("lantai.llm.client", "_gate_init_args"),
    ("lantai.observability.metrics", "_collector"),
    ("lantai.observability.telemetry", "_writer"),
)

_MODULE_STATE_PRISTINE: dict[tuple[str, str], object] = {}
_MODULE_STATE_RESIDUE: dict[str, dict] = {}
_MODULE_STATE_RESTORE_FAILURES: list[tuple[str, list[str]]] = []
_MODULE_STATE_UNCAPTURED: list[str] = []


def _copy_module_state(value):
    """浅拷贝容器内容：懒单例/标量按引用即可（它们的隔离靠「写回 None」）。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, set):
        return set(value)
    if isinstance(value, frozenset):
        return frozenset(value)
    if isinstance(value, tuple):
        return tuple(value)
    return value


def _ensure_module_state_baseline() -> None:
    """在第一个测试体执行之前抓齐 12 个通道的出厂初值。

    抓不到不静默跳过：记进 `_MODULE_STATE_UNCAPTURED` 并由会话判红——否则「护栏
    指向一个改过名的属性」会永远「复位成功」而真实泄漏一条都没管（本票探针起草时
    就把 `mcp._active_profile_cache` 猜成了不存在的名字，实测 MISSING）。
    """
    import importlib

    for mod_name, attr in _GUARDED_MODULE_STATE:
        key = (mod_name, attr)
        if key in _MODULE_STATE_PRISTINE:
            continue
        try:
            mod = importlib.import_module(mod_name)
            _MODULE_STATE_PRISTINE[key] = _copy_module_state(getattr(mod, attr))
        except Exception as exc:  # noqa: BLE001
            note = f"{mod_name}.{attr}: {type(exc).__name__}: {str(exc)[:120]}"
            if note not in _MODULE_STATE_UNCAPTURED:  # 每个测试都会重试，只留一条
                _MODULE_STATE_UNCAPTURED.append(note)


def _write_module_state(key, value):
    """把单个通道写回目标值（容器原地回滚）；返回 None 表示确实写干净了。"""
    mod_name, attr = key
    mod = sys.modules.get(mod_name)
    if mod is None:
        return f"{mod_name} 未加载"
    try:
        current = getattr(mod, attr)
    except AttributeError:
        return f"{mod_name}.{attr} 已不存在（改名/删除使护栏失效）"
    if isinstance(value, dict) and isinstance(current, dict):
        current.clear()
        current.update(value)
    elif isinstance(value, list) and isinstance(current, list):
        del current[:]
        current.extend(value)
    elif isinstance(value, set) and isinstance(current, set):
        current.clear()
        current.update(value)
    else:
        setattr(mod, attr, value)
    after = getattr(mod, attr, None)
    if isinstance(value, (dict, list, set)) and isinstance(after, type(value)):
        ok = after == value
    else:
        ok = after is value or after == value
    return None if ok else f"{mod_name}.{attr} 回滚后仍不一致：{after!r} != {value!r}"


def _snapshot_module_state() -> dict:
    """当前值快照（护栏与回归测试共用同一份读法，避免两边口径漂移）。"""
    out = {}
    for key in _GUARDED_MODULE_STATE:
        mod = sys.modules.get(key[0])
        if mod is None:
            continue
        out[key] = _copy_module_state(getattr(mod, key[1], None))
    return out


def _restore_module_state(snapshot: dict) -> list[str]:
    problems = []
    for key, value in snapshot.items():
        err = _write_module_state(key, _copy_module_state(value))
        if err:
            problems.append(err)
    return problems


def _dirty_module_channels() -> list[str]:
    dirty = []
    for key, pristine in _MODULE_STATE_PRISTINE.items():
        mod = sys.modules.get(key[0])
        if mod is None:
            continue
        if getattr(mod, key[1], None) != pristine:
            dirty.append(f"{key[0]}.{key[1]}")
    return dirty


def pytest_terminal_summary(terminalreporter, exitstatus=0, config=None):
    _report_module_state(terminalreporter)
    if not _BINDING_RESIDUE:
        return
    bad = unexpected_binding_residue(_BINDING_RESIDUE)
    title = (
        "跨测试模块绑定残留（存在未申报污染源：会话已判红）"
        if bad
        else "跨测试模块绑定残留（仅申报哨兵 test_3a，预期内）"
    )
    terminalreporter.write_sep("=", title)
    for nodeid, notes in _BINDING_RESIDUE[:20]:
        tag = "申报哨兵" if nodeid in DECLARED_POLLUTION_SENTINELS else "未申报"
        terminalreporter.write_line(f"[{tag}] {nodeid}")
        for note in notes:
            terminalreporter.write_line("    " + note)
    if len(_BINDING_RESIDUE) > 20:
        terminalreporter.write_line(f"...另有 {len(_BINDING_RESIDUE) - 20} 例")


def pytest_sessionfinish(session, exitstatus=0):
    """未申报的绑定残留把会话判红：复位过不等于测试干净（票 05 续8）。

    只在「本来全绿」时升级为 1，不覆盖已经红的会话，也不新增 skip/xfail。
    """
    if int(exitstatus) == 0 and (
        unexpected_binding_residue(_BINDING_RESIDUE)
        or _MODULE_STATE_RESTORE_FAILURES
        or _MODULE_STATE_UNCAPTURED
    ):
        session.exitstatus = 1


def pytest_unconfigure(config):
    """会话结束复原装配期写过的全局环境/配置（票 05 续14，验收底线 G2）。

    `tests/support/harness_state.py` 是 tests/ 内唯一允许改全局的地方，且每次写入都登记
    原值；这里在 pytest 卸载插件前把 `OPENAI_API_KEY` / `OPENAI_BASE_URL`、
    `settings.FEATURE_*`、隔离数据目录三项一律还原——复原不改变任何测试结果，它只保证
    「本会话的装配改写」不会被下一个进程或子会话继承，也不再给「宿主真凭据 + 公网端点」
    留出网通道。判据见 `tests/test_harness_global_state_isolation.py`：AST 静态扫描 +
    真实子会话（ambient 真凭据被覆盖 → 会话退出被精确复原）。
    """
    harness_state.restore()


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

    harness_state.set_settings_fields(
        settings,
        LANTAI_HOME=str(home),
        DATABASE_URL=f"sqlite:///{db_file}",
        CHROMADB_PATH=str(home / ".chromadb"),
    )

    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    db_module.engine = engine
    global _SESSION_DB_ENGINE
    _SESSION_DB_ENGINE = engine
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


def _fake_embed(texts, timeout=None):
    # timeout：D-04 预算传导（票 `.scratch/v0.65-next-window-2026-11/01`）——
    # embed 新签名，替身同步收下（快速替身永不超时，忽略之）
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


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    # tryfirst + wrapper：post-yield 发生在默认实现（全部 fixture 撕卸）之后，
    # 与 conftest 里其他 fixture 的撕卸顺序无关，因此无竞争。
    yield
    _PREV_TEST["id"] = item.nodeid
    _repair_canonical_bindings(item.nodeid)


# ── 常驻注入面复位（v0.66 票 01）─────────────────────────────────────
# SQLModel 默认表名；这些行会被 shell_hook._build_resident_persona 无条件
# 置顶注入，api_keys 还会把 DEV MODE 鉴权整体翻面。
_RESIDENT_TABLES = (
    "corememoryblock",  # 核心记忆块
    "persona_profile",  # 器识人格基座
    "session_checkpoint",  # 会话检查点
    "prompttemplate",  # 提示词模板覆写
    "api_keys",  # Bearer key 表（DEV MODE 开关的数据源）
)


_SESSION_DB_ENGINE = None


def _wipe_resident_channels():
    """清空**会话级共享测试库**上的常驻注入面（真 DELETE，不用替身糊断言）。

    只碰 `_isolate_data_dir` 那一个 engine：不少用例会把 `db.engine` 临时换成
    自己的最小/早期 schema 引擎（test_document_cascade_ownership、test_offload、
    test_hybrid_none_principal 等迁移形态冒烟），对它们乱 DELETE 会炸出
    `no such table`——隔离护栏没有资格去动别人的实验台。
    """
    from sqlalchemy import text
    from sqlmodel import SQLModel

    engine = _SESSION_DB_ENGINE
    if engine is None:
        return
    available = set(SQLModel.metadata.tables)
    with engine.connect() as conn:
        for table in _RESIDENT_TABLES:
            if table not in available:
                continue
            conn.execute(text(f"DELETE FROM {table}"))
        conn.commit()


@pytest.fixture(autouse=True)
def _reset_resident_channels(_isolate_data_dir):
    """常驻注入面每例前后各复位一次（v0.66 票 01 的正解）。

    取代旧 `_isolate_resident_persona`——那个 fixture 从文件 exec 一份 shell_hook
    **副本**再 patch 副本里的 `_build_resident_persona`，而被测模块是各测试自己
    用别的模块名加载的，patch 落在一次性对象上，等于「看着像护栏、实际在空转」
    的假隔离；更糟的是它每个测试 exec 一次 shell_hook，把 import 期的
    `sys.path.insert` 放大成全量 +2574 条重复项。

    真根因在数据：`_isolate_data_dir` 是 **session 级共享库**，写库用例
    （test_console_api_contracts 的核心记忆+模板、test_diben_api/engine_smoke
    的 41 行会话检查点、test_shishi_api_smoke 的核心记忆）把数据留在里面，
    后续 `build_context("你好") == {}` 一类断言读出脏数据——单跑永远绿，
    全量必红（v0.65 基线 6 例）。

    口径：需要常驻注入内容的用例在自己的测试体内造数据（既有
    test_persona / test_checkpoint_* / test_mcp 全是这个形态），不依赖前序残留。
    """
    _wipe_resident_channels()
    yield
    _wipe_resident_channels()


@pytest.fixture(autouse=True)
def _isolate_app_dependency_overrides():
    """`app.dependency_overrides` 是进程级生产全局，逐例回收（v0.66 票 01）。

    测试体内裸赋值 `app.dependency_overrides[get_current_user] = ...` 而不还原，
    后续 API 用例就带着别人的 user_id/tenant_id 打路由——行级 ACL 把目标行滤空，
    表现为与业务无关的 404 / 零召回（实证：test_xuanjing_topology_smoke:223
    连坐 test_zuozheng_api_smoke::test_seam2 与 test_zero_recall_remediation
    ::test_search_with_force_bypasses_gate）。

    只回收「测试体自己加的那层」：先快照，撕卸时若表已变则整体回滚到快照，
    因此 module/class 级 fixture 合法预设的覆盖（如
    tests/cognitive/test_cognitive_routes.py 对 get_session 的覆盖）原样保留。
    """
    from lantai.api.app import app

    saved = dict(app.dependency_overrides)
    yield
    if app.dependency_overrides != saved:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


@pytest.fixture(autouse=True)
def _reset_gate_hot_cache():
    """闸门热缓存跨测试复位（v0.64 票 05）。

    `relevance_check` 的追问热缓存（`_LAST_GATE_DECISION` / `_USER_GATE_DECISIONS`，
    `GATE_CACHE_TTL` 默认 1h）是模块级状态：先行的测试用带信号查询触发
    `needs_memory=True` 后，后行测试的单字短查询会命中「追问沿用」分支
    （`session_followup_hot`）——`test_zero_recall_remediation::force`
    断言 `needs_memory is False` 被连坐，单跑绿全量红。测试隔离口径：
    每个测试从冷缓存开始，需要热缓存语义的用例自行预填。
    """
    import lantai.gate.prefilter as pf

    pf._LAST_GATE_DECISION.update({"time": 0.0, "query": "", "needs_memory": False})
    if hasattr(pf, "_USER_GATE_DECISIONS"):
        pf._USER_GATE_DECISIONS.clear()
    yield


@pytest.fixture(autouse=True)
def _isolate_module_state(request):
    """模块级生产状态每例冷启动 + 撕卸回滚（票 05 续22）。

    冷启动放在 setup：即使上一个测试的 teardown 因异常没跑完，下一个测试也不会
    继承脏值；撕卸时再回滚一次并把造脏者点名。回滚不生效是护栏自己的缺陷，
    记红并由 `pytest_sessionfinish` 升级会话退出码——不靠「复位过」冒充干净。
    """
    _ensure_module_state_baseline()
    cold_problems = _restore_module_state(_MODULE_STATE_PRISTINE)
    if cold_problems:
        _MODULE_STATE_RESTORE_FAILURES.append(("<setup>", cold_problems))
    yield
    dirty = _dirty_module_channels()
    if not dirty:
        return
    nodeid = request.node.nodeid
    for name in dirty:
        row = _MODULE_STATE_RESIDUE.setdefault(name, {"count": 0, "examples": []})
        row["count"] += 1
        if len(row["examples"]) < 3 and nodeid not in row["examples"]:
            row["examples"].append(nodeid)
    problems = _restore_module_state(_MODULE_STATE_PRISTINE)
    if problems:
        _MODULE_STATE_RESTORE_FAILURES.append((request.node.nodeid, problems))


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
