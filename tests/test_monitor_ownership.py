"""Monitor 面板归属与宿主机脱敏：票 `.scratch/readside-gaps/05`

`GET /monitor/*` 五个 handler 一个身份都不取，`build_monitor_snapshot`
全链没有 `principal` 形参——于是任意持 key 者一次请求拿到：

- 宿主机绝对路径（含操作系统用户名）、python 完整版本串、pid、OS 指纹；
- 全库记忆/候选/提案计数（A 看见 B 制造了多少记忆、多少待审）；
- `operation_logs` 全表（A 看见 B 打过哪些端点）。

比票 01/02/03 低一个量级：不涉及他人 content、不涉及 id oracle。
修法两路：宿主机指纹按 admin 分档 + 计数/日志按归属收窄。

口径同票 04（`_digest_scope`）：`user_id == viewer OR IS NULL`。
真实库 memoryitem 650 行里 629 行 NULL——单人部署，判「NULL 不可见」
会让报表归零，那不是收窄是修废。

测试纪律（AGENTS.md）：核心函数一律不 mock 内部逻辑——
`build_monitor_snapshot` 直传真实建表的内存 SQLite session，聚合走真 SQL；
路由层用真实 TestClient 打真实 HTTP 链路。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.time import utcnow
from lantai.models.tables import (
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    OperationLog,
    RetrievalEvent,
)


def _principal(user_id: str, *, role: str = "user"):
    from lantai.core.acl import Principal

    return Principal(
        tenant_id=None,
        user_id=user_id,
        agent_id=None,
        session_id=None,
        role=role,
        allowed_lanes=None,
    )


A = _principal("user-A")
ADMIN = _principal("admin-1", role="admin")


def _mem(
    mid: str,
    user_id: str | None,
    *,
    prompt: str | None = None,
    session_id: str | None = None,
) -> MemoryItem:
    now = utcnow()
    return MemoryItem(
        id=mid,
        memory_type="semantic",
        namespace="default",
        key=f"key-{mid}",
        content=f"{mid} 的正文",
        lane="general",
        status="active",
        importance=0.5,
        created_at=now,
        updated_at=now,
        user_id=user_id,
        # `collect_ingest_liveness` 的 `writes_session` 只数带 session 的行
        # （与 `writes_background` 的分母拆开，避免"后台在写"被误读成
        # "宿主在写"）。不种 session_id 它恒为 0，M8 的判据永远平凡。
        session_id=session_id,
        # provenance 是 `build_overview` 按 prompt 聚合的那一列；不带它
        # `provenance_by_prompt` 恒为 {}，M4（provenance 去归属）杀不掉。
        provenance={"prompt": prompt} if prompt else None,
    )


def _cand(cid: str, user_id: str | None, *, age_hours: int = 0) -> MemoryCandidate:
    now = utcnow()
    return MemoryCandidate(
        id=cid,
        tenant_id=None,
        user_id=user_id,
        document_id="doc-1",
        summary=f"{cid} 的候选",
        status="pending_review",
        extractor_confidence=0.9,
        lane="general",
        created_at=now - timedelta(hours=age_hours),
        updated_at=now - timedelta(hours=age_hours),
        review_due_at=now,
    )


def _prop(pid: str, user_id: str | None) -> MemoryProposal:
    now = utcnow()
    return MemoryProposal(
        id=pid,
        proposal_type="merge",
        candidate_id=None,
        evidence_ids=["e1"],
        reason="probe05",
        proposed_patch={"key": pid, "content": "x", "memory_type": "semantic", "lane": "general"},
        confidence=0.9,
        conflict_ids=[],
        status="pending",
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


def _log(lid: str, user_id: str, endpoint: str) -> OperationLog:
    return OperationLog(
        id=lid,
        user_id=user_id,
        endpoint=endpoint,
        latency_ms=12.0,
        status_code=200,
        created_at=utcnow(),
    )


def _retrieval(
    rid: str,
    user_id: str | None,
    *,
    session_id: str | None = None,
    zero_result: bool = False,
) -> RetrievalEvent:
    """检索事件（票 05 的 M8 / M11 判据都靠它）。

    `session_id` 非空才算"真实会话读"——`collect_ingest_liveness` 与
    recall_report 各自要不同形状：前者只数带 session 的，后者数全部
    （含 session 为 NULL 的老行）。两个形状都种，缺一个就有一个
    判据点永远不成立。
    """
    return RetrievalEvent(
        id=rid,
        user_id=user_id,
        trace_id=f"trace-{rid}",
        query_text=f"{rid} 的问题",
        query_norm_hash=f"hash-{rid}",
        lane="general",
        session_id=session_id,
        param_snapshot_hash="ph",
        latency_ms=8,
        zero_result=zero_result,
        estimated_tokens=10,
        is_system_noise=False,
    )


@pytest.fixture()
def monitor_owned_env(monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session + 独立指标收集器。

    自包含：不 import `test_monitor.py` 的 `monitor_env`（测试模块之间
    不共享 fixture，那是隐式依赖）。建表 / patch 方式与那个夹具同款。
    """
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module
    from lantai.observability import metrics as metrics_module
    from lantai.observability.metrics import MetricsCollector
    from lantai.observability.telemetry import TelemetryWriter, set_writer

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    metrics_module.set_collector(MetricsCollector(buffer=200, bucket_minutes=30))
    set_writer(TelemetryWriter(flush_seconds=3600))

    # A 一条 + B 一条 + NULL 老行一条（口径：NULL 可见）
    # provenance 分人给不同 prompt：A 见 "pa"，B 见 "pb"，混了就说明去归属。
    with session_factory() as s:
        s.add(_mem("mem-A", "user-A", prompt="pa", session_id="sess-A"))
        s.add(_mem("mem-B", "user-B", prompt="pb", session_id="sess-B"))
        s.add(_mem("mem-null", None, prompt="pnull", session_id="sess-null"))
        s.add(_cand("cand-A", "user-A"))
        s.add(_cand("cand-B", "user-B"))
        s.add(_cand("cand-stale-B", "user-B", age_hours=48))
        s.add(_prop("prop-A", "user-A"))
        s.add(_prop("prop-B", "user-B"))
        s.add(_log("log-A", "user-A", "GET /memory/"))
        s.add(_log("log-B", "user-B", "GET /dialogue/"))
        # 检索事件：A / B 各一条带 session 的会话读（ingest_liveness 的
        # 分子），外加一条 B 的 NULL session（recall_report 数它、
        # ingest_liveness 不数——两个函数对 session 的口径不同，都要有判据）。
        # 全给零召回：`zero_recall_rate` 才不是 0，M11 才有非平凡输入。
        s.add(_retrieval("ev-A", "user-A", session_id="sess-A", zero_result=True))
        s.add(_retrieval("ev-B", "user-B", session_id="sess-B", zero_result=True))
        s.add(_retrieval("ev-B-nosession", "user-B", zero_result=True))
        s.commit()

    yield session_factory

    metrics_module.set_collector(None)
    set_writer(None)


def _snapshot(factory, principal, **kw):
    from lantai.ops.monitor import build_monitor_snapshot

    kw.setdefault("include_quality", False)
    return build_monitor_snapshot(factory(), principal=principal, **kw)


# ── Red 1（决定性）：宿主机指纹脱敏 ─────────────────────────────────


class TestHostFingerprintMaskedForNonAdmin:
    def test_paths_are_filename_not_absolute(self, monitor_owned_env):
        """绝对路径把操作系统用户名与部署位置一次交出。

        判据打在**响应体**上（不是"库里没有"）：只要 `\\` 或 `/` 出现
        在 path 字段里就是泄漏。`C:` 兜底——Windows 盘符是绝对路径的
        另一种写法。
        """
        snap = _snapshot(monitor_owned_env, A)
        for where, value in (
            ("storage.database.path", snap["storage"]["database"]["path"]),
            ("storage.vector_store.path", snap["storage"]["vector_store"]["path"]),
        ):
            assert "\\" not in value and "/" not in value, f"{where} 泄漏绝对路径：{value!r}"
            assert "C:" not in value, f"{where} 泄漏盘符：{value!r}"

    def test_python_version_loses_patch_and_build(self, monitor_owned_env):
        """`3.13.14` 能直接查已知 CVE；只留 `3.13` 就查不了具体版本。"""
        snap = _snapshot(monitor_owned_env, A)
        version = snap["process"]["python"]
        assert version.count(".") == 1, f"python 版本串精度太高（能查具体 CVE）：{version!r}"
        assert version.startswith("3."), f"python 版本串形状不对：{version!r}"

    def test_pid_and_resource_counters_withheld(self, monitor_owned_env):
        """pid / open_fds / cpu_seconds 是进程枚举与侧信道素材。

        `threads` 与 `rss_mb` **保留**——面板要显示"进程内存 MB"与
        "线程 / FD"，且它们是粗粒度画像，不指向具体进程。
        """
        snap = _snapshot(monitor_owned_env, A)
        process = snap["process"]
        for field in ("pid", "open_fds", "cpu_seconds", "uptime_seconds"):
            assert process.get(field) is None, (
                f"非 admin 不该拿到 process.{field}：{process.get(field)!r}"
            )
        assert process.get("threads") is not None, "面板的线程数没了（修废）"
        assert process.get("rss_mb") is not None, "面板的内存数没了（修废）"

    def test_security_topology_withheld(self, monitor_owned_env):
        """鉴权拓扑：知道有几把钥匙、是不是 dev fallback，就是踩点素材。

        **键必须保留、值置 None**——前端 `monitor.js:157` 直接模板串
        `${security.host}:${security.port}`，删键会让 JS 抛异常。
        """
        snap = _snapshot(monitor_owned_env, A)
        security = snap["security"]
        for field in ("host", "port", "api_keys_total", "api_keys_active", "effective_auth"):
            assert field in security, f"security.{field} 键被删了——前端会抛异常"
            assert security[field] is None, (
                f"非 admin 不该拿到 security.{field}：{security[field]!r}"
            )
        # loopback 布尔保留：告警规则 auth_dev_fallback 判据就是它，
        # 且布尔不含部署位置
        assert security["loopback"] is True

    def test_platform_and_base_urls_withheld(self, monitor_owned_env):
        """OS 指纹与 LLM 端点地址：前者查漏洞，后者是内网拓扑。"""
        snap = _snapshot(monitor_owned_env, A)
        assert snap["dependency"].get("platform") is None, (
            f"非 admin 不该拿到 OS 指纹：{snap['dependency'].get('platform')!r}"
        )
        assert snap["dependency"]["llm"].get("base_url") is None, "LLM 端点地址泄漏了"
        assert snap["dependency"]["reranker"].get("base_url") is None, "精排端点地址泄漏了"
        # 模型名与开关保留：面板要显示，且不含部署位置
        assert snap["dependency"]["llm"].get("model")

    def test_response_body_has_no_host_fingerprint_anywhere(self, monitor_owned_env):
        """整棵树扫一遍——脱敏漏一个子字段（如 storage.vector_store.path）
        上面的逐字段断言看不见。"""
        snap = _snapshot(monitor_owned_env, A)
        text = str(snap)
        assert "C:\\" not in text and "C:/" not in text, f"响应体里有绝对路径：{text[:400]!r}"
        import sys

        assert sys.version.split()[0] not in text, "响应体里有完整 python 版本串"
        assert str(__import__("os").getpid()) not in text, "响应体里有 pid"


# ── Red 2：admin 全量（不能把运维修废）─────────────────────────────


class TestAdminSeesEverything:
    def test_admin_gets_full_fingerprint(self, monitor_owned_env):
        snap = _snapshot(monitor_owned_env, ADMIN)
        assert snap["process"]["pid"] > 0, "admin 拿不到 pid——运维修废"
        assert snap["process"]["open_fds"] is not None
        assert snap["process"]["cpu_seconds"] is not None
        assert snap["process"]["uptime_seconds"] is not None
        assert snap["process"]["python"].count(".") == 2, (
            f"admin 的 python 版本被降精度了：{snap['process']['python']!r}"
        )

    def test_admin_gets_paths_and_topology(self, monitor_owned_env):
        snap = _snapshot(monitor_owned_env, ADMIN)
        assert snap["storage"]["database"]["path"], "admin 拿不到库路径"
        assert snap["security"]["host"] is not None
        assert snap["security"]["port"] is not None
        assert snap["security"]["api_keys_total"] is not None
        assert snap["dependency"]["platform"] is not None, "admin 的 OS 指纹没了"

    def test_admin_counts_are_full(self, monitor_owned_env):
        snap = _snapshot(monitor_owned_env, ADMIN)
        assert snap["memories"]["total"] == 3, f"admin 的 total 该是 3：{snap['memories']}"
        assert snap["review"]["candidates_pending_review"] == 3
        assert snap["review"]["proposals_pending"] == 2

    def test_internal_call_principal_none_is_unfiltered(self, monitor_owned_env):
        """`principal=None`（内部 worker / MCP / 脚本）不过滤——口径同前 17 票。

        票面验收口径第 4 条原文：「`principal=None`（内部入口 / worker /
        脚本）→ 不过滤」。**不要把它改成收敛到 `"default"`**：定时任务与
        CLI 不带身份，一过滤就空转，那是把内部工具修废
        （`test_monitor.py::test_build_monitor_snapshot_aggregates_real_db`
        的 `pid > 0` 断言就是这条口径的既有护栏）。

        宿主机指纹对 `principal=None` 也全量。这不是 fail-open：**HTTP
        路径上 `get_current_user` 永远返回 Principal**，`None` 在这条路上
        产生不出来；能传 `None` 的只有内部代码，那本来就是运维侧的。
        """
        snap = _snapshot(monitor_owned_env, None)
        assert snap["memories"]["total"] == 3, (
            f"principal=None 该全量，实际 {snap['memories']['total']}"
        )
        assert snap["review"]["candidates_pending_review"] == 3
        assert snap["process"]["pid"] > 0, "principal=None 该拿全量指纹"

    def test_dev_mode_fallback_principal_still_masked(self, monitor_owned_env):
        """DEV MODE 回落出的 `user_id="default"` **不是** `principal=None`。

        两个入口必须分开：无凭证的本机请求是"任意持 key 者"，内部 worker
        是运维自己的代码。混同就等于给 DEV MODE 开一个指纹全量的后门
        （`get_current_user` 的 C 分支回落的是 `role="user"`）。
        """
        snap = _snapshot(monitor_owned_env, _principal("default"))
        assert snap["memories"]["total"] == 1, (
            f"DEV MODE 的 default 只见 NULL 老行，实际 {snap['memories']['total']}"
        )
        assert snap["process"]["pid"] is None, "DEV MODE 不该拿到 pid"
        assert snap["security"]["host"] is None, "DEV MODE 不该拿到鉴权拓扑"


# ── Red 3：计数按归属收窄 ──────────────────────────────────────────


class TestCountsNarrowedByOwnership:
    def test_memory_counts_exclude_other_users(self, monitor_owned_env):
        """A 只见自己的 + NULL 老行（逐项断言具体数字）。

        第一版只断言 total 与 active 两项，变异验证的
        "by_lane 去归属"/"by_decay_class 去归属" 因此全 MISSED——
        那几项根本没被看。**判据必须覆盖每一个会变的量。**
        """
        snap = _snapshot(monitor_owned_env, A)
        assert snap["memories"]["total"] == 2, (
            f"A 的 total 该是 2，实际 {snap['memories']['total']}"
        )
        assert snap["memories"]["active"] == 2
        assert snap["memories"]["archived"] == 0
        assert snap["memories"]["by_lane"] == {"general": 2}, (
            f"A 的 by_lane 混入了 B 的行：{snap['memories']['by_lane']}"
        )
        assert snap["memories"]["by_decay_class"] == {"episodic": 2}, (
            f"A 的 by_decay_class 混入了 B 的行：{snap['memories']['by_decay_class']}"
        )
        assert snap["summary"]["memories_total"] == 2

    def test_review_and_pipeline_counts_exclude_other_users(self, monitor_owned_env):
        snap = _snapshot(monitor_owned_env, A)
        assert snap["review"]["candidates_pending_review"] == 1, (
            f"A 的待审候选该是 1（A 自己），实际 {snap['review']['candidates_pending_review']}"
        )
        assert snap["review"]["proposals_pending"] == 1
        assert snap["pipeline"]["candidates_pending_review"] == 1
        assert snap["pipeline"]["candidates_pending_over_24h"] == 0, (
            "A 不该看见 B 那条超 24h 的候选"
        )
        assert snap["pipeline"]["proposals_pending"] == 1
        assert snap["pipeline"]["conflicts_open"] == 0

    def test_null_owner_rows_still_counted(self, monitor_owned_env):
        """NULL 老行仍计入——单人部署下这是 629/650 行的真实数据。

        没有这条，"按归属收窄"会被实现成 `user_id == viewer`，
        单人部署的报表于是归零。
        """
        snap = _snapshot(monitor_owned_env, A)
        assert snap["memories"]["total"] == 2, "NULL 老行被藏了（报表会归零）"

    def test_ingest_liveness_narrowed_by_ownership(self, monitor_owned_env):
        """写线/读线活性也按人分（M8：夹具原先一条 RetrievalEvent 都没种，
        `ingest_conv_reads_24h` 恒为 0 → 判据永远平凡，杀不掉变异）。

        夹具种了 A / B 各一条**带 session** 的会话读，另有一条 B 的
        不带 session——后者 recall_report 数、ingest_liveness 不数。
        """
        snap = _snapshot(monitor_owned_env, A)
        live = snap["ingest_liveness"]
        assert live["ingest_conv_reads_24h"] == 1, (
            f"A 的会话读该是 1，实际 {live['ingest_conv_reads_24h']}"
        )
        assert live["writes_session_24h"] == 2, (
            f"A 的会话写该是 2（A 自己 + NULL 老行），实际 {live['writes_session_24h']}"
        )
        assert live["writes_background_24h"] == 0
        assert live["judgment"] == "ok", f"A 有读有写，判据该是 ok，实际 {live['judgment']}"
        # 反向确认夹具非平凡：admin 视角 B 的行全在
        snap_admin = _snapshot(monitor_owned_env, ADMIN)
        live_admin = snap_admin["ingest_liveness"]
        assert live_admin["ingest_conv_reads_24h"] == 2, (
            f"admin 该看见两条会话读，实际 {live_admin['ingest_conv_reads_24h']}"
        )
        assert live_admin["writes_session_24h"] == 3

    def test_recall_report_narrowed_by_ownership(self, monitor_owned_env):
        """零召回率是 `build_monitor_snapshot` 复用 recall_report 的一块。

        M11 判据：夹具种的检索事件全是零召回，A 该看见自己的 1 条
        （零召回率 1.0），B 的 2 条不该进来。原先 `include_quality=False`
        且一条事件都没种 → `quality` 恒为空 dict，变异杀不掉。
        """
        snap = _snapshot(monitor_owned_env, A, include_quality=True)
        quality = snap["quality"]
        assert quality["total"] == 1, f"A 的检索事件该是 1，实际 {quality['total']}"
        assert quality["zero"] == 1
        assert quality["zero_recall_rate"] == 1.0, (
            f"A 的零召回率该是 1.0，实际 {quality['zero_recall_rate']}"
        )
        assert quality["by_lane"] == {"general": {"total": 1, "zero": 1}}
        snap_admin = _snapshot(monitor_owned_env, ADMIN, include_quality=True)
        assert snap_admin["quality"]["total"] == 3, (
            f"admin 该看见全部 3 条，实际 {snap_admin['quality']['total']}"
        )
        assert snap_admin["quality"]["zero_recall_rate"] == 1.0

    def test_provenance_distribution_narrowed(self, monitor_owned_env):
        """`provenance_by_prompt` 也是全表扫的——它读 MemoryItem.provenance。

        夹具给每个属主种了**不同 prompt**（A=pa / B=pb / NULL=pnull）：
        A 该看见 {"pa": 1, "pnull": 1}——`pnull` 那条是 NULL 属主老行，
        按口径**可见**（单人部署 629/650 行 NULL）。若断言写成 `== {}`，
        它同时被"去归属"和"根本没种 provenance"满足——M4 因此杀不掉。
        **判据要在没有泄漏时也非平凡。**
        """
        snap = _snapshot(monitor_owned_env, A)
        assert snap["review"]["provenance_by_prompt"] == {"pa": 1, "pnull": 1}, (
            f"A 的 provenance 分布混入了 B 的行：{snap['review']['provenance_by_prompt']}"
        )
        # 反向确认夹具本身非平凡：admin 视角确实是全表聚合
        snap_admin = _snapshot(monitor_owned_env, ADMIN)
        assert snap_admin["review"]["provenance_by_prompt"] == {
            "pa": 1,
            "pb": 1,
            "pnull": 1,
        }, f"admin 该看见全部三个 prompt：{snap_admin['review']['provenance_by_prompt']}"


# ── Red 4：Prometheus 不炸 ────────────────────────────────────────


class TestPrometheusStillRenders:
    def test_non_admin_snapshot_renders_valid_text(self, monitor_owned_env):
        """非 admin 快照里 uptime/rss 都被撤了，`render_prometheus` 不能炸
        （外部抓取依赖这个文本格式；指标名一个都不能改）。

        撤值的指标（`lantai_process_uptime_seconds` / `rss_bytes`）会**整行
        消失**——`gauge()` 对 `None` 直接 return。这是对的：一个值为空的
         gauge 比没有这个 gauge 更糟（抓取方会读到 `nan` 或把空串当 0）。
        断言因此分两类：仍在的指标名必须在，已撤的必须不在。
        """
        from lantai.ops.monitor import render_prometheus

        snap = _snapshot(monitor_owned_env, A)
        text = render_prometheus(snap)
        assert "# HELP lantai_up" in text
        assert "lantai_up 1" in text
        assert "# TYPE lantai_memories_total gauge" in text
        assert "lantai_memories_total 2" in text
        # 仍应存在的指标（抓取方的既有看板不能空）。
        # `lantai_zero_recall_rate` 不在其列：本夹具 include_quality=False，
        # quality 块本就是空的——与归属无关。
        for name in (
            "lantai_process_threads",
            "lantai_process_rss_bytes",
            "lantai_http_requests_total",
            "lantai_database_bytes",
            "lantai_table_rows",
            "lantai_scheduler_running",
        ):
            assert f"# HELP {name}" in text, f"指标 {name} 该在却没了（抓取看板会空）"
        # 已脱敏的指标必须整行消失（不能留一个空值 gauge）
        assert "# HELP lantai_process_uptime_seconds" not in text, (
            "uptime 已撤值却仍输出 gauge 行——抓取方会读到空值"
        )
        # 文本仍然合法：每行非空或注释
        assert all(line.strip() for line in text.splitlines()[:-1]), "渲染出了空行"

    def test_route_returns_prometheus_for_non_admin(self, monitor_owned_env):
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: A
        try:
            with TestClient(app) as c:
                resp = c.get("/monitor/prometheus")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert resp.status_code == 200, resp.text
        assert "text/plain" in resp.headers["content-type"]
        assert "lantai_up 1" in resp.text
        assert "lantai_memories_total 2" in resp.text


# ── Red 5：/monitor/logs 按 user_id 收窄 ──────────────────────────


class TestOperationLogsNarrowed:
    def test_non_admin_sees_only_own_logs(self, monitor_owned_env):
        """A 不该看见 B 打过哪些端点（不含正文，但是踩点素材）。"""
        from lantai.ops.monitor import list_operation_logs

        rows = list_operation_logs(100, principal=A)
        assert [r["user_id"] for r in rows] == ["user-A"], (
            f"A 看见了别人的请求日志：{[r['user_id'] for r in rows]}"
        )

    def test_identity_less_logs_still_visible(self, monitor_owned_env):
        """无身份埋下的日志仍可见——`anonymous` 哨兵，真实库 171 行。

        遥测写的是 `row["user_id"] or "anonymous"`
        （`telemetry.py:103`），**空串在这张表里不存在**。若只按
        `OR IS NULL` 兜老行，这 171 行对任何非 admin 都看不见——
        而那恰是扫描器踩点留下的痕迹，藏它比露它更危险。
        """
        from lantai.ops.monitor import list_operation_logs

        with monitor_owned_env() as s:
            s.add(_log("log-anon", "anonymous", "GET /definitely-missing"))
            s.add(_log("log-null", "", "GET /health/"))
            s.commit()

        rows = list_operation_logs(100, principal=A)
        assert {r["user_id"] for r in rows} == {"user-A", "anonymous", ""}, (
            f"无身份日志被藏了：{[r['user_id'] for r in rows]}"
        )

    def test_admin_and_none_see_all_logs(self, monitor_owned_env):
        """admin 与 `principal=None` 都全量（口径同前 17 票）。"""
        from lantai.ops.monitor import list_operation_logs

        assert len(list_operation_logs(100, principal=ADMIN)) == 2
        assert len(list_operation_logs(100, principal=None)) == 2

    def test_route_forwards_identity_to_logs(self, monitor_owned_env):
        """REST 入口必须把身份传下去（路由忘传 principal 时前面几条照样绿）。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: A
        try:
            with TestClient(app) as c:
                resp = c.get("/monitor/logs")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert resp.status_code == 200, resp.text
        rows = resp.json()["items"]
        assert [r["user_id"] for r in rows] == ["user-A"], (
            f"路由没把身份传给日志查询：{[r['user_id'] for r in rows]}"
        )


# ── 路由层：五个端点全部带身份 ─────────────────────────────────────


class TestRoutesCarryPrincipal:
    def _client(self, principal):
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: principal
        client = TestClient(app)
        return client, app

    def _cleanup(self, app):
        from lantai.core.auth import get_current_user

        app.dependency_overrides.pop(get_current_user, None)

    def test_overview_route_narrows_for_non_admin(self, monitor_owned_env):
        """决定性：非 admin 打真实 HTTP → 计数收窄 + 指纹脱敏。

        单列一条走真实 TestClient 的用例：前面几条都直调
        `build_monitor_snapshot`，绕过路由层——路由忘加
        `Depends(get_current_user)` 时它们照样绿。
        """
        client, app = self._client(A)
        try:
            with client:
                resp = client.get("/monitor/overview?quality=false")
        finally:
            self._cleanup(app)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["memories"]["total"] == 2, (
            f"路由没把身份传下去：A 看见了 {body['memories']['total']} 条记忆"
        )
        assert body["security"]["host"] is None, "路由层没脱敏：鉴权拓扑泄漏"
        assert "\\" not in body["storage"]["database"]["path"], (
            f"路由层泄漏绝对路径：{body['storage']['database']['path']!r}"
        )

    def test_config_route_has_no_path_leak(self, monitor_owned_env):
        """`/monitor/config` 已有 `_mask_path` 护着 DATABASE_URL——本票只补
        `LANTAI_HOME`（同款绝对路径），不拆掉既有打码。"""
        client, app = self._client(A)
        try:
            with client:
                resp = client.get("/monitor/config")
        finally:
            self._cleanup(app)

        assert resp.status_code == 200, resp.text
        text = str(resp.json())
        assert "C:\\" not in text and "C:/" not in text, f"配置视图泄漏绝对路径：{text[:300]!r}"

    def test_admin_route_keeps_full_view(self, monitor_owned_env):
        """不能把运维修废：admin 打真实 HTTP 仍拿全量。"""
        client, app = self._client(ADMIN)
        try:
            with client:
                resp = client.get("/monitor/overview?quality=false")
        finally:
            self._cleanup(app)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["memories"]["total"] == 3, f"admin 看不见全库：{body['memories']}"
        assert body["security"]["host"] is not None
        assert body["process"]["pid"] > 0

    def test_series_route_still_works(self, monitor_owned_env):
        """`/monitor/series` 是内存指标（无归属列），只需确认不炸。"""
        client, app = self._client(A)
        try:
            with client:
                resp = client.get("/monitor/series?minutes=5")
        finally:
            self._cleanup(app)

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["series"]) == 5


# ── 回归：既有面板测试的口径 ───────────────────────────────────────


class TestNoRegressionOnExistingBehaviour:
    def test_alerts_still_evaluate_on_masked_snapshot(self, monitor_owned_env):
        """告警规则要能在脱敏快照上跑（admin 全量快照亦然）。

        `evaluate_alerts` 读 `security.loopback` / `api_key_configured` /
        `api_keys_total`——后两个对非 admin 是 None。**规则不能因为
        None 就崩**，`None` 在布尔上下文里是 falsy，与 0 同向。
        """
        from lantai.ops.monitor import evaluate_alerts

        snap_a = _snapshot(monitor_owned_env, A)
        alerts = evaluate_alerts(snap_a)
        assert isinstance(alerts, list)
        snap_admin = _snapshot(monitor_owned_env, ADMIN)
        assert isinstance(evaluate_alerts(snap_admin), list)

    def test_snapshot_keys_unchanged(self, monitor_owned_env):
        """返回形状不能动（前端与 Prometheus 都在消费这些键）。"""
        snap_a = _snapshot(monitor_owned_env, A)
        snap_admin = _snapshot(monitor_owned_env, ADMIN)
        expected = {
            "generated_at",
            "version",
            "process",
            "storage",
            "memories",
            "review",
            "pipeline",
            "scheduler",
            "requests",
            "quality",
            "ingest_liveness",
            "security",
            "dependency",
            "alerts",
            "summary",
        }
        assert set(snap_a) == expected, f"顶层键变了：{set(snap_a) ^ expected}"
        assert set(snap_admin) == expected
        assert set(snap_a["process"]) == set(snap_admin["process"]), (
            f"process 的键在两种身份下不一致：{set(snap_a['process']) ^ set(snap_admin['process'])}"
        )
        assert set(snap_a["security"]) == set(snap_admin["security"])
        assert set(snap_a["storage"]["database"]) == set(snap_admin["storage"]["database"])
