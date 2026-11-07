"""usage docs P0 收口回归锁（v0.68 工单 06）。

七组「照做必败」文档病灶的文档-代码一致性锁：
- grep 型：病灶文本必须缺席、目标口径必须在场；
- 契约型：文档示例直过真实 pydantic 校验、文档清单与真实注册表/门禁锚点集合相等。

不 mock 任何被测链路（pydantic 模型、MCP 注册表、路由表均为真实导入）；
文档断言按内容模式而非行号（行号随编辑漂移，模式不漂）。
工单 09 拥有全局「文档引用路径防复发哨兵」，本文件只锁工单 06 六组范围。
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _active_script_refs(text: str) -> list[str]:
    """文档里的 scripts/*.py 活引用；「已删除/不要再引用」反指注记行不算。"""
    return [
        ref
        for line in text.splitlines()
        if "删除" not in line and "不要再引用" not in line
        for ref in re.findall(r"scripts/[\w_]+\.py", line)
    ]


def _load_release_check():
    spec = importlib.util.spec_from_file_location(
        "release_check_doclock", REPO / "scripts" / "release_check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 组 1：README as_of 时态查询写错位置（query 参数被静默丢弃）─────────────


def test_readme_as_of_is_body_field_not_query_param():
    readme = _read("README.md")
    assert "/search?as_of" not in readme, "README 仍教人把 as_of 写在 URL 上（会被静默忽略）"
    assert "请求体" in readme and "as_of_recorded" in readme
    assert "静默忽略" in readme, "应保留「写在 URL 上会被静默忽略」的警示"

    from lantai.models.schemas import SearchReq

    for field in ("as_of", "as_of_recorded", "time_from", "time_to"):
        assert field in SearchReq.model_fields, f"SearchReq 缺 {field}"
    # 路由函数签名（query 参数唯一住址）不得出现 as_of 族（req.as_of 的请求体处理合法）
    routes_src = _read("lantai/api/routes_search.py")
    assert re.search(r"def[^\n]*as_of", routes_src) is None, "路由签名出现了 as_of 族 query 参数"


# ── 组 2：README MCP 工具口径钉死「三工具」───────────────────────────────


def test_readme_mcp_tools_two_tier_matches_registry():
    readme = _read("README.md")
    assert "三工具" not in readme
    assert "核心 6 工具" in readme
    assert "LANTAI_MCP_PROFILE=full" in readme and "96" in readme

    from lantai.cli.mcp import HERMES_CORE_TOOLS, TOOL_HANDLERS, TOOLS

    assert len(TOOLS) == 96 and len(TOOL_HANDLERS) == 96
    # README 点名的核心工具集合必须与注册表逐字相等（防文档自造工具名）
    line = next(ln for ln in readme.splitlines() if "核心 6 工具" in ln)
    named = set(re.findall(r"`([a-z_]+)`", line))
    assert "LANTAI_MCP_PROFILE" not in line.replace("`LANTAI_MCP_PROFILE=full`", "")
    named.discard("LANTAI_MCP_PROFILE")
    assert named == set(HERMES_CORE_TOOLS), (
        f"README 工具名 {named} ≠ HERMES_CORE_TOOLS {set(HERMES_CORE_TOOLS)}"
    )


# ── 组 3：mcp-client-matrix 钉死「40 工具」、默认档收窄零记录──────────────


def test_matrix_two_tier_no_static_40():
    matrix = _read("docs/mcp-client-matrix.md")
    assert "工具清单（40）" not in matrix
    assert "返回 40 个工具" not in matrix
    assert "LANTAI_MCP_PROFILE=full" in matrix
    assert "96" in matrix
    assert "需 full 档" in matrix, "档外工具应标注「需 full 档」"


def test_matrix_registry_count_matches_code():
    from lantai.cli.mcp import TOOLS

    matrix = _read("docs/mcp-client-matrix.md")
    m = re.search(r"全量\s*(\d+)\s*项", matrix)
    assert m, "矩阵应写明全量档工具数（如「全量 96 项」）"
    assert int(m.group(1)) == len(TOOLS), "矩阵声称的全量工具数与注册表不符"


# ── 组 4：手册示例照做即失败（端口 / add 示例 / 速查表）──────────────────


def _tool_call_args(manual: str, tool: str) -> dict:
    for line in manual.splitlines():
        if '"method":"tools/call"' in line and f'"name":"{tool}"' in line:
            return json.loads(line)["params"]["arguments"]
    raise AssertionError(f"manual 中找不到 {tool} 的 tools/call 示例行")


def test_manual_port_and_env_paths():
    manual = _read("docs/lantai-manual-call.md")
    assert "默认 8000" not in manual and "127.0.0.1:8000" not in manual
    assert "8767" in manual
    assert ".venv-audit" not in manual, "旧审计 venv 路径已废弃"
    assert "C:/Users/Asus" not in manual, "旧机硬编码路径"


def test_manual_add_example_passes_real_validation():
    manual = _read("docs/lantai-manual-call.md")
    from lantai.models.schemas import AddMemoryReq

    args = _tool_call_args(manual, "add")
    req = AddMemoryReq(**args)  # 照文档构造必须能过真实校验（title 必填、content ≥10）
    assert req.title.strip()
    assert len(req.content) >= 10


def test_manual_search_example_passes_real_validation():
    manual = _read("docs/lantai-manual-call.md")
    from lantai.models.schemas import SearchReq

    args = _tool_call_args(manual, "search")
    SearchReq(**args)


def test_manual_quickref_title_required():
    manual = _read("docs/lantai-manual-call.md")
    row = next(ln for ln in manual.splitlines() if ln.startswith("| `add` |"))
    assert re.search(r"`title`（必填", row), f"速查表 add 行应标注 title 必填：{row}"


def test_manual_no_signal_example_matches_gate():
    """R-1：文档的 no_signal 反例必须真的被 gate 判为 no_signal。"""
    manual = _read("docs/lantai-manual-call.md")
    # 反例行（⚠️ 标记）不得再用会被 gate 放行的「大哥的电脑配置」
    # （自指+技术词双通道，实测 needs_memory=True——检索示例里当通过例用是合法的）
    bad = [ln for ln in manual.splitlines() if "⚠️" in ln and "大哥的电脑配置" in ln]
    assert not bad, f"no_signal 反例行仍在用会被放行的例句：{bad}"

    from lantai.gate import prefilter

    m = re.search(r"孤立短[^。\n]*?「([^」]+)」", manual)
    assert m, "手册应给出「孤立短查询」反例（引号内为例句）"
    # 清 15s 热缓存再判：否则同进程内前序命中查询会把反例带成 session_followup_hot
    # （prefilter.py:193-195 单向短路——注释明示该常量可按测试口径重置）
    prefilter._LAST_GATE_DECISION.update({"time": 0.0, "query": "", "needs_memory": False})
    verdict = prefilter.relevance_check(m.group(1))
    reason = verdict["reason"] if isinstance(verdict, dict) else getattr(verdict, "name", verdict)
    assert reason == "no_signal", f"文档反例 {m.group(1)!r} 实测判 {reason}，与文档口径不符"


# ── 组 5：ops-runbook 两条命令不可用──────────────────────────────────────


def test_runbook_no_dead_commands():
    runbook = _read("docs/ops-runbook.md")
    assert "run_digest" not in runbook
    assert "docker compose" not in runbook
    assert "POST /monitor/workers/digest/run" in runbook
    assert "GET /digest/today" in runbook

    from lantai.api.routes_monitor import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/monitor/workers/{worker_name}/run" in paths


def test_runbook_docker_pointer_names_real_readme_section():
    runbook = _read("docs/ops-runbook.md")
    readme = _read("README.md")
    assert "### 方式二：Docker 容器运行" in readme, "README 的 Docker 段标题（指路目标）"
    assert "方式二：Docker 容器运行" in runbook, "runbook 指路名必须与 README 实际标题一致"
    assert "docker compose" not in runbook


def test_runbook_referenced_scripts_exist():
    runbook = _read("docs/ops-runbook.md")
    for ref in sorted(set(_active_script_refs(runbook))):
        assert (REPO / ref).exists(), f"runbook 引用的脚本不存在：{ref}"


# ── 组 6：插件文档与 plugin.yaml 谎报闲聊去向 + 品牌──────────────────────


def test_plugin_doc_chitchat_sata():
    doc = _read("docs/lantai-hermes-plugin.md")
    assert "候选进待审队列（不静默丢弃）" not in doc
    assert "沙汰" in doc
    assert "预期行为而非部署失败" in doc, "验证段应注明纯闲聊被沙汰不是部署失败"


def test_plugin_yaml_chitchat_and_brand():
    yaml_text = _read("hermes-plugin/lantai-hook/plugin.yaml")
    assert "闲聊入待审队列" not in yaml_text
    assert "沙汰" in yaml_text or "rejected" in yaml_text
    assert "Remembrance" not in yaml_text, "随插件分发的 manifest 品牌必须已改 lantai"


# ── 组 7：release 文档版本同步步骤指向真实门禁锚点────────────────────────


def test_release_docs_version_anchors_match_gate():
    rc = _load_release_check()
    spec_files = {rel for _, rel, _ in rc.VERSION_REF_SPECS}
    assert {"lantai/api/app.py", "lantai/cli/mcp.py", "uv.lock"} <= spec_files

    for rel in ("docs/release-process.md", "docs/development-workflow.md"):
        text = _read(rel)
        assert "scripts/mcp_server" not in text, f"{rel} 仍指向已删除的 scripts/mcp_server.py"
        for anchor in ("lantai/api/app.py", "lantai/cli/mcp.py", "uv.lock"):
            assert anchor in text, f"{rel} 版本步骤缺门禁真锚点 {anchor}"

    rp = _read("docs/release-process.md")
    assert "`api_server.py`：`FastAPI(" not in rp, (
        "api_server.py 是无版本薄壳，不得列为版本同步目标"
    )
    assert "VERSION_REF_SPECS" in rp
    assert "CHANGELOG 最新发布段" in rp, "门禁仍核对 CHANGELOG 最新发布段，版本步骤不得漏列"


def test_release_gate_item_title_paired_with_script():
    """R-2 成对锁：文档门禁项标题与 release_check.py 指路串必须同改同在。"""
    doc = _read("docs/release-process.md")
    script = _read("scripts/release_check.py")
    assert "MCP 默认最小暴露" not in doc, "门禁输出名为「MCP 默认暴露」，文档项标题应一致"
    assert "MCP 默认最小暴露" not in script, "指路串引用的文档标题应与文档实际标题一致"


# ── P0-3 文档侧：MCP 入口统一现行入口（handoff 残句随行清理）──────────────


def test_handoff_entry_and_leftover():
    handoff = _read("docs/hermes-install-handoff.md")
    # 「已删除」反指注记是合法形态；活引用必须真实存在（旧 mcp_server 引用已在反指行内）
    for ref in sorted(set(_active_script_refs(handoff))):
        assert (REPO / ref).exists(), f"handoff 引用的脚本不存在：{ref}"
    assert "lantai.cli.mcp" in handoff or "lantai-mcp" in handoff
    assert "已内置 sys.path" not in handoff, "残句与新入口自相矛盾（0ba98c25 漏改 :76）"
    assert "import remembrance" not in handoff, "包已更名 lantai"


def test_manual_and_matrix_referenced_scripts_exist():
    for rel in ("docs/lantai-manual-call.md", "docs/mcp-client-matrix.md"):
        for ref in sorted(set(_active_script_refs(_read(rel)))):
            assert (REPO / ref).exists(), f"{rel} 引用的脚本不存在：{ref}"


# ── 契约来源真实性守卫（防断言对象本身漂移）──────────────────────────────


def test_contract_sources_are_real():
    """守卫：本文件引用的代码事实必须仍然成立，否则本文件自身失效。"""
    from lantai.cli.mcp import HERMES_CORE_TOOLS

    assert len(HERMES_CORE_TOOLS) == 6, "HERMES_CORE_TOOLS 规模变了——先更新本锁与两档文档口径"
    rc = _load_release_check()
    assert len(rc.VERSION_REF_SPECS) >= 6
