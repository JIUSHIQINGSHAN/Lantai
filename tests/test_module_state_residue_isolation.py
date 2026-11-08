"""模块级生产状态的跨测试隔离护栏（票 05 续22，验收底线 G2）。

实测病灶（同一进程内「会话开始快照 vs 会话结束快照」差分，探针与原始输出见
`.scratch/gate/r22/`：`suite_main.log` 2857 passed、`smain.start.json` /
`smain.finish.json`、`self_diff.log`）：一轮全量绿跑结束时，以下生产模块级状态
仍带着测试造出来的内容——

  * `lantai.cognition.cehou._SHADOW_WINDOWS` 0 → 9 条，键名就是
    `win-svc-api-test-01` / `win-rest-test-01` 这类测试自用 id，且 cezhou.py
    会遍历全表做扫描聚合（:264）；
  * `lantai.core.scheduler.WORKER_LAST_RUN` 0 → 6 条，而 `get_last_run()`
    （scheduler.py:64）把它当「DB 查不到时的兜底真值」读；
  * `lantai.gate.prefilter._PATTERN_CACHE` 键没变、**值被原地改掉**（只比对
    对象身份的检查抓不到这一类）；同文件 `_KEYWORD_WARNED` 是「只吼一次」的
    抑制位，先行的测试把它置真，后续期望看到告警的断言就永远看不到；
  * `lantai.cli.mcp._guard_instance._states` 留着 3 个测试调用指纹，
    mcp.py:3777 复用该单例——断路状态（`open_until`）会跨测试放行 -32005；
  * `lantai.observability.metrics._collector._records` /
    `lantai.observability.telemetry._writer._pending`/`_seen` 累计了前序测试的
    请求记录（`user_id=default`、`/cehou/windows` 等）；
  * `lantai.cognition.kaogong._LATEST_KAOGONG_REPORT`、
    `lantai.services.consolidation_service._LAST_CONSOLIDATION_REPORT` 带着
    测试跑出来的时间戳与计数（kaogong.py:338 会读 `evaluated_at` 做判重）。

既有护栏为什么没拦住：`tests/conftest.py` 的绑定绊线只比对**函数身份**
（`hybrid.embed` 那类），`harness_state` 只登记 **env 与 settings 字段**；
两者都不看模块级容器的内容，也不看懒单例里攒下的状态——这是 G2 的门禁盲区。

本文件的判据是双向的（票 05 续21 自纠教训：只做子串比对的检查会被自己的变异
实验证明是空转的）：既要求护栏**确实挂在会话生命周期上并生效**，也要求植入的
污染**真的没能传到下一个测试**。任何一侧失灵都是红。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 本文件与 conftest 各自声明一遍同一张表：漂移即红（护栏被人删项、改名、
# 加了不存在的属性，都在这里被抓住，而不是悄悄变成空转）。
EXPECTED_CHANNELS = (
    # ("lantai.cognition.cehou", "_SHADOW_WINDOWS") 票 60 退役：影子窗落库
    # CehouWindow 表，进程内注册表删除——通道随实体一起消失，见 conftest 同注。
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


def _conftest():
    import tests.conftest as cft

    return cft


def test_guarded_channel_table_is_declared_and_matches():
    """护栏必须存在、非空，且与本文件登记的实测清单逐条一致。"""
    table = getattr(_conftest(), "_GUARDED_MODULE_STATE", None)
    assert table is not None, "conftest 未声明 _GUARDED_MODULE_STATE：模块级状态无人管"
    assert len(table) >= len(EXPECTED_CHANNELS), f"护栏项偏少：{table}"
    assert tuple(table) == EXPECTED_CHANNELS, (
        f"护栏清单与本文件的实测证据不一致：\nconftest={tuple(table)}\nexpected={EXPECTED_CHANNELS}"
    )


@pytest.mark.parametrize("module_name,attr", EXPECTED_CHANNELS)
def test_every_guarded_channel_actually_exists_in_production(module_name, attr):
    """防「护栏指向不存在的属性」= 静默空转。

    探针起草时就把 `lantai.cli.mcp._active_profile_cache` 猜成了一个不存在的名字，
    差分里表现为 MISSING——如果复位表里写着这样的名字，它会永远「复位成功」，
    而真实泄漏一条都没管。
    """
    import importlib

    mod = importlib.import_module(module_name)
    assert hasattr(mod, attr), f"{module_name}.{attr} 不存在：护栏项已失效"


def test_reset_fixture_is_autouse_and_roundtrips_content():
    """复位必须逐例自动生效，且回滚的是**内容**而不是对象身份。

    `_PATTERN_CACHE` 那类是 `dict.update()` 原地改写：对象从头到尾没换过，
    只把变量指回旧对象的写法在这里完全无效。
    """
    import lantai.gate.prefilter as pf

    cft = _conftest()
    fixture = getattr(cft, "_isolate_module_state", None)
    assert fixture is not None, "conftest 未提供 _isolate_module_state"
    # pytest 7/8 叫 _pytestfixturefunction，9.x 改成 _fixture_function_marker：
    # 两个名字都认，避免这个判据本身变成「换版本就空转」的假检查。
    marker = getattr(fixture, "_pytestfixturefunction", None) or getattr(
        fixture, "_fixture_function_marker", None
    )
    assert marker is not None and marker.autouse, "_isolate_module_state 不是 autouse：不会逐例生效"

    snapshot = cft._snapshot_module_state()
    original = dict(pf._PATTERN_CACHE)
    container_identity = pf._PATTERN_CACHE
    try:
        pf._PATTERN_CACHE.update(key="planted-key", pattern="planted-pattern")
        assert pf._PATTERN_CACHE.get("key") == "planted-key"
        cft._restore_module_state(snapshot)
        assert original == pf._PATTERN_CACHE, f"原地改值没被回滚：{pf._PATTERN_CACHE} != {original}"
        # 必须是「原地回滚」而不是「换一个新 dict 挂回去」：生产代码可能正持有旧引用，
        # 只改变量指向会让它继续读脏值，而快照比对看起来却是干净的（空转型修复）。
        assert pf._PATTERN_CACHE is container_identity, (
            "回滚换了对象身份：护栏必须原地回滚，否则持有旧引用的生产代码收不到复位"
        )
    finally:
        pf._PATTERN_CACHE.clear()
        pf._PATTERN_CACHE.update(original)


CHILD_CONFTEST = """
import os
from pathlib import Path

try:
    from tests.conftest import _isolate_module_state  # noqa: F401  autouse 护栏

    Path(os.environ["CHILD_GUARD_FILE"]).write_text("present", encoding="utf-8")
except Exception as exc:  # noqa: BLE001
    Path(os.environ["CHILD_GUARD_FILE"]).write_text("absent:" + str(exc)[:200], encoding="utf-8")
"""

CHILD_DIRTY = """
import os
from pathlib import Path

import lantai.cognition.kaogong as kaogong
import lantai.cli.mcp as mcp_mod
import lantai.gate.prefilter as pf
import lantai.observability.metrics as metrics


def test_a_plant_pollution():
    kaogong._LATEST_KAOGONG_REPORT = {"child": "planted-report"}
    pf._PATTERN_CACHE.update(key="child-planted-key", pattern="child-planted-pattern")
    mcp_mod._guard_instance = mcp_mod.LoopGuard()
    mcp_mod._guard_instance.begin("child-planted-key")
    metrics._collector = metrics.MetricsCollector()
    metrics._collector.record(
        method="POST", route="/child-planted", status=200, latency_ms=1.0, user_id="child"
    )
    Path(os.environ["CHILD_DIRTY_FILE"]).write_text("dirtied", encoding="utf-8")
"""

CHILD_CHECK = """
import json
import os
from pathlib import Path

import lantai.cognition.kaogong as kaogong
import lantai.cli.mcp as mcp_mod
import lantai.gate.prefilter as pf
import lantai.observability.metrics as metrics


def test_b_look_for_leakage():
    leaked = []
    if kaogong._LATEST_KAOGONG_REPORT.get("child") == "planted-report":
        leaked.append("kaogong._LATEST_KAOGONG_REPORT")
    if pf._PATTERN_CACHE.get("key") == "child-planted-key":
        leaked.append("prefilter._PATTERN_CACHE")
    if getattr(mcp_mod._guard_instance, "_states", None):
        leaked.append("mcp._guard_instance._states")
    if getattr(metrics._collector, "_records", None):
        leaked.append("metrics._collector._records")
    Path(os.environ["CHILD_LEAK_FILE"]).write_text(json.dumps(leaked), encoding="utf-8")
"""


def _read(path: Path, default: str) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else default


def test_guard_blocks_planted_pollution_from_reaching_next_test(tmp_path):
    """真实子会话证明：A 例造脏，B 例必须看不见（不是靠断言写法迁就）。"""
    child = tmp_path / "child"
    child.mkdir()
    (child / "conftest.py").write_text(CHILD_CONFTEST, encoding="utf-8")
    (child / "test_a_plant.py").write_text(CHILD_DIRTY, encoding="utf-8")
    (child / "test_b_check.py").write_text(CHILD_CHECK, encoding="utf-8")

    guard_file = tmp_path / "guard.txt"
    dirty_file = tmp_path / "dirty.txt"
    leak_file = tmp_path / "leak.txt"
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
    ):
        env.pop(k, None)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "LANTAI_HOME": str(home),
            "REMEMBRANCE_HOME": str(home),
            "PYTHONIOENCODING": "utf-8",
            "CHILD_GUARD_FILE": str(guard_file),
            "CHILD_DIRTY_FILE": str(dirty_file),
            "CHILD_LEAK_FILE": str(leak_file),
        }
    )
    env.pop("VIRTUAL_ENV", None)

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

    guard = _read(guard_file, "MISSING")
    assert guard == "present", f"子会话里护栏没有被装配（autouse 未生效或导入失败）：{guard}"
    assert _read(dirty_file, "MISSING") == "dirtied", "A 例根本没造脏：本判据空转"

    raw = _read(leak_file, "MISSING")
    assert raw != "MISSING", "B 例没有落检查结论：本判据空转"
    leaked = json.loads(raw)
    assert leaked == [], f"造脏的测试把模块级生产状态漏给了下一个测试：{leaked}"
