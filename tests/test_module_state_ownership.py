"""模块级生产状态「有主纳管」的判据（票 05 续23，验收底线 G2 的后续排期）。

续22 的兜底护栏 `_isolate_module_state` 在全量结束时点名：`telemetry._writer` 366 例、
`metrics._collector` 356 例、`prefilter._PATTERN_CACHE` 86 例、`_KEYWORD_WARNED` 83 例、
`scheduler.WORKER_LAST_RUN` 68 例。这五条都是**真实生产代码被执行到时的正常副作用**
（懒单例首次使用、惰性编译缓存、`record_run()` 完成戳），本票给它们配了专门的主人
fixture（见 `tests/conftest.py` 的 `_own_*`），让兜底台账只剩真正需要排期的条目。

判据是双向的，任何一侧失灵都是红：
  1. 主人**确实**把生产路径的副作用释放回出厂值（不 mock：直调真实的
     `_self_reference_pattern()` / `record_run()` / `get_collector().record()` /
     `get_writer().offer()`，以及经 `TelemetryMiddleware` 的真实 HTTP 请求）；
  2. 主人**不认领**它证明不了来历的东西——测试塞进去的外来实例、植入的伪缓存、
     空串占位——这些必须原样留给兜底护栏点名（护栏不被削弱）；
  3. 真实子会话端到端：带主人时台账不再出现这五条，而外来泄漏仍被点名；
     拿掉主人（对照组）这五条立刻回到台账——证明是主人在起作用，不是护栏变瞎。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

OWNED_CHANNELS = (
    "lantai.observability.telemetry._writer",
    "lantai.observability.metrics._collector",
    "lantai.gate.prefilter._PATTERN_CACHE",
    "lantai.gate.prefilter._KEYWORD_WARNED",
    "lantai.core.scheduler.WORKER_LAST_RUN",
)

OWNER_FIXTURES = (
    "_own_observability_singletons",
    "_own_prefilter_keyword_cache",
    "_own_worker_last_run",
)


@pytest.fixture
def cft(request):
    """pytest **实际装配**的那份 conftest 模块（含护栏抓到的出厂快照）。

    不能 `import tests.conftest`：那会另建一份模块对象，`_MODULE_STATE_PRISTINE`
    是空的，释放/点名函数全都会空转（本票起草时实测踩到）。
    """
    target = (REPO_ROOT / "tests" / "conftest.py").resolve()
    for plugin in request.config.pluginmanager.get_plugins():
        f = getattr(plugin, "__file__", None)
        if f and Path(f).resolve() == target:
            assert plugin._MODULE_STATE_PRISTINE, "护栏出厂快照为空：判据前提不成立"
            return plugin
    pytest.fail("找不到 pytest 装配的 tests/conftest.py")


def test_owner_fixtures_are_autouse_and_ordered_inside_the_guard(request):
    """主人 fixture 必须逐例自动生效，且排在兜底护栏之内（晚 setup、早撕卸）。"""
    names = list(request.fixturenames)
    assert "_isolate_module_state" in names, "兜底护栏没挂上：本判据前提不成立"
    guard_at = names.index("_isolate_module_state")
    for owner in OWNER_FIXTURES:
        assert owner in names, f"{owner} 未 autouse 生效：通道无主"
        assert names.index(owner) > guard_at, f"{owner} 早于护栏 setup：撕卸会晚于护栏检查"


def test_owned_channels_stay_in_guard_table(cft):
    """纳管不等于摘除：五条通道仍在兜底护栏表里，主人漏了照样被点名。"""
    table = {f"{m}.{a}" for m, a in cft._GUARDED_MODULE_STATE}
    missing = [c for c in OWNED_CHANNELS if c not in table]
    assert missing == [], f"纳管通道被移出兜底护栏：{missing}"


# ── 观测懒单例 ──────────────────────────────────────────────────────────


def test_observability_owner_releases_its_own_instances_smoke(cft):
    """不 mock：真实 record/offer 写进主人预装的实例，释放后回到出厂值 None。"""
    import lantai.observability.metrics as metrics
    import lantai.observability.telemetry as telemetry

    collector = metrics.get_collector()
    writer = telemetry.get_writer()
    assert type(collector) is metrics.MetricsCollector
    assert type(writer) is telemetry.TelemetryWriter
    assert collector._records == type(collector._records)(), "预装实例不是全新空实例"

    collector.record(method="GET", route="/smoke", status=200, latency_ms=1.0, user_id="u")
    writer.offer(method="GET", route="/smoke", status=500, latency_ms=1.0, user_id="u")
    assert len(collector._records) == 1
    assert writer.stats()["pending"] == 1, "5xx 必落缓冲：offer 没写进主人预装的实例"

    assert cft._release_owned_singleton("lantai.observability.metrics", "_collector", collector)
    assert cft._release_owned_singleton("lantai.observability.telemetry", "_writer", writer)
    assert metrics._collector is None and telemetry._writer is None
    dirty = cft._dirty_module_channels()
    assert "lantai.observability.metrics._collector" not in dirty
    assert "lantai.observability.telemetry._writer" not in dirty


def test_observability_owner_ignores_foreign_instance(cft):
    """测试自己塞进去的实例不归主人管：释放拒绝，兜底护栏仍看得见。"""
    import lantai.observability.metrics as metrics

    owned = metrics.get_collector()
    foreign = metrics.MetricsCollector()
    metrics.set_collector(foreign)
    try:
        assert not cft._release_owned_singleton("lantai.observability.metrics", "_collector", owned)
        assert metrics._collector is foreign, "主人越权回滚了外来实例"
        assert "lantai.observability.metrics._collector" in cft._dirty_module_channels()
    finally:
        metrics.set_collector(owned)


def test_real_http_request_through_middleware_is_released(cft):
    """经 TelemetryMiddleware 的真实请求（全量台账里这两条的主来源）落进主人实例。"""
    from fastapi.testclient import TestClient

    import lantai.observability.metrics as metrics
    from lantai.api.app import app

    owned = metrics.get_collector()
    with TestClient(app) as c:
        # 不存在的路由：照样经过中间件计数，且不碰 `/health` 的 git_sha 缓存这类别的通道
        assert c.get("/__ownership_smoke__").status_code == 404
    assert metrics._collector is owned, "中间件没有用主人预装的实例（单例被换掉了）"
    assert any(r.status == 404 for r in owned._records), "请求没落进主人预装的实例"
    assert cft._release_owned_singleton("lantai.observability.metrics", "_collector", owned)


# ── 闸门关键词惰性编译缓存 ──────────────────────────────────────────────


def test_prefilter_owner_releases_real_compiled_cache_smoke(monkeypatch, cft):
    """不 mock：真实惰性编译（含未配置关键词时的一次性告警位）被释放回出厂值，且原地回滚。"""
    import lantai.gate.prefilter as pf

    monkeypatch.delenv("REMEMBRANCE_ENTITY_KEYWORDS", raising=False)
    monkeypatch.delenv("ENTITY_KEYWORDS", raising=False)
    identity = pf._PATTERN_CACHE
    compiled = pf._self_reference_pattern()
    assert pf._PATTERN_CACHE["pattern"] is compiled
    assert pf._KEYWORD_WARNED is True

    assert cft._release_prefilter_keyword_cache()
    assert pf._PATTERN_CACHE is identity, "缓存容器被换了对象：生产持有的旧引用没回滚"
    assert pf._PATTERN_CACHE == {"key": None, "pattern": None}
    assert pf._KEYWORD_WARNED is False
    dirty = cft._dirty_module_channels()
    assert "lantai.gate.prefilter._PATTERN_CACHE" not in dirty
    assert "lantai.gate.prefilter._KEYWORD_WARNED" not in dirty


def test_prefilter_owner_refuses_planted_cache(cft):
    """植入的伪缓存（字符串冒充正则）不是生产产物：拒绝释放，交兜底护栏点名。"""
    import lantai.gate.prefilter as pf

    original = dict(pf._PATTERN_CACHE)
    pf._PATTERN_CACHE.update(key="planted-key", pattern="planted-pattern")
    try:
        assert not cft._release_prefilter_keyword_cache()
        assert pf._PATTERN_CACHE["key"] == "planted-key", "主人越权回滚了植入值"
        assert "lantai.gate.prefilter._PATTERN_CACHE" in cft._dirty_module_channels()
    finally:
        pf._PATTERN_CACHE.clear()
        pf._PATTERN_CACHE.update(original)


# ── worker 完成戳 ───────────────────────────────────────────────────────


def test_worker_owner_releases_real_record_run_smoke(cft):
    """不 mock：真实 `record_run()`（内存 + 隔离库落库）写下的戳被摘掉。"""
    import lantai.core.scheduler as scheduler

    scheduler.record_run("ownership-smoke")
    assert scheduler.WORKER_LAST_RUN.get("ownership-smoke")
    assert scheduler.get_last_run("ownership-smoke")

    identity = scheduler.WORKER_LAST_RUN
    assert cft._release_worker_stamps() == ["ownership-smoke"]
    assert scheduler.WORKER_LAST_RUN is identity
    assert "lantai.core.scheduler.WORKER_LAST_RUN" not in cft._dirty_module_channels()


@pytest.mark.parametrize("planted", ["", "not-a-stamp", None, 123])
def test_worker_owner_refuses_non_stamp_values(planted, cft):
    """空串占位、乱码、非字符串都不是 `record_run()` 的产物：留给兜底护栏。"""
    import lantai.core.scheduler as scheduler

    scheduler.WORKER_LAST_RUN["planted"] = planted
    try:
        assert cft._release_worker_stamps() == []
        assert "planted" in scheduler.WORKER_LAST_RUN
        assert "lantai.core.scheduler.WORKER_LAST_RUN" in cft._dirty_module_channels()
    finally:
        scheduler.WORKER_LAST_RUN.pop("planted", None)


# ── 真实子会话端到端（含对照组）─────────────────────────────────────────

CHILD_CONFTEST_WITH_OWNERS = """
from tests.conftest import (  # noqa: F401
    _isolate_module_state,
    _own_observability_singletons,
    _own_prefilter_keyword_cache,
    _own_worker_last_run,
    pytest_terminal_summary,
)
"""

CHILD_CONFTEST_GUARD_ONLY = """
from tests.conftest import _isolate_module_state, pytest_terminal_summary  # noqa: F401
"""

CHILD_PRODUCTION_SIDE_EFFECTS = """
import lantai.core.scheduler as scheduler
import lantai.gate.prefilter as pf
import lantai.observability.metrics as metrics
import lantai.observability.telemetry as telemetry


def test_a_exercise_real_production_paths():
    metrics.get_collector().record(
        method="GET", route="/child", status=200, latency_ms=1.0, user_id="child"
    )
    telemetry.get_writer()
    pf._self_reference_pattern()
    scheduler.record_run("child-worker")
"""

CHILD_FOREIGN_LEAK = """
import lantai.cognition.cehou as cehou
import lantai.observability.metrics as metrics


def test_b_leak_foreign_state():
    metrics.set_collector(metrics.MetricsCollector())
    cehou._SHADOW_WINDOWS["child-planted-window"] = {"window_id": "child-planted-window"}
"""


def _run_child(tmp_path: Path, conftest_src: str) -> str:
    child = tmp_path / "child"
    child.mkdir()
    (child / "conftest.py").write_text(conftest_src, encoding="utf-8")
    (child / "test_a_production.py").write_text(CHILD_PRODUCTION_SIDE_EFFECTS, encoding="utf-8")
    (child / "test_b_foreign.py").write_text(CHILD_FOREIGN_LEAK, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    env = dict(os.environ)
    for k in (
        "API_KEY",
        "OPENAI_API_KEY",
        "RERANKER_API_KEY",
        "LANTAI_MCP_PROFILE",
        "MCP_TOOL_PROFILE",
        "OPENAI_BASE_URL",
        "DATABASE_URL",
        "CHROMADB_PATH",
        "REMEMBRANCE_ENTITY_KEYWORDS",
        "ENTITY_KEYWORDS",
        "VIRTUAL_ENV",
    ):
        env.pop(k, None)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "LANTAI_HOME": str(home),
            "REMEMBRANCE_HOME": str(home),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(child),
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        timeout=600,
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, f"子会话没跑成：exit={proc.returncode}\n{stdout[-3000:]}"
    assert "2 passed" in stdout, f"子会话用例没全跑：\n{stdout[-3000:]}"
    return stdout


def _residue_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.splitlines() if ln.startswith("[已回滚] ")]


def test_child_session_with_owners_names_only_foreign_leaks(tmp_path):
    """带主人：生产副作用不进台账，外来泄漏（外来实例、植入窗口）照样被点名。"""
    lines = _residue_lines(_run_child(tmp_path, CHILD_CONFTEST_WITH_OWNERS))
    joined = "\n".join(lines)
    assert any(
        ln.startswith("[已回滚] lantai.observability.metrics._collector 被 1 例") for ln in lines
    ), f"外来 collector 泄漏没被点名（护栏被削弱）：\n{joined}"
    assert "test_b_foreign.py" in joined
    assert any(ln.startswith("[已回滚] lantai.cognition.cehou._SHADOW_WINDOWS") for ln in lines), (
        joined
    )
    assert "test_a_production.py" not in joined, f"生产副作用仍落进兜底台账：\n{joined}"
    for name in (
        "lantai.observability.telemetry._writer",
        "lantai.gate.prefilter._PATTERN_CACHE",
        "lantai.gate.prefilter._KEYWORD_WARNED",
        "lantai.core.scheduler.WORKER_LAST_RUN",
    ):
        assert name not in joined, f"{name} 仍在台账：\n{joined}"


def test_child_session_guard_only_control_still_names_owned_channels(tmp_path):
    """对照组（拿掉主人）：同样的生产副作用立刻回到台账——证明是主人在起作用。"""
    lines = _residue_lines(_run_child(tmp_path, CHILD_CONFTEST_GUARD_ONLY))
    joined = "\n".join(lines)
    for name in OWNED_CHANNELS:
        assert any(ln.startswith(f"[已回滚] {name} ") for ln in lines), (
            f"对照组里 {name} 没被点名：判据空转\n{joined}"
        )
