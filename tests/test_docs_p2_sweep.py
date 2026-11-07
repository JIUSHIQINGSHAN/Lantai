"""P2 文档清仓回归锁（v0.68 工单 09）。

流 C 审计 P2×20 的清仓锁（19 项 agent 修 + 1 项 ready-for-human 不锁）；
防复发哨兵本体在 tests/test_release_check.py（判据级红白样）。
断言按内容模式不按行号；每项修法以代码现值为准。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_readme_adr_count_never_rots():
    readme = _read("README.md")
    assert "0001-0053" not in readme, "ADR 计数硬编码会再过期"
    assert "持续增补" in readme


def test_plugin_version_bumped_with_behavior_change():
    yaml_text = _read("hermes-plugin/lantai-hook/plugin.yaml")
    assert re.search(r"^version: 1\.2\.0$", yaml_text, re.M), (
        "行为变更（PR #33 回执延后 + 票 01 挂载解耦）后插件版本应提升 1.2.0"
    )


def test_adr_0042_0043_0046_have_status_lines():
    for num in ("0042", "0043", "0046"):
        matches = list((REPO / "docs" / "adr").glob(f"{num}-*.md"))
        assert len(matches) == 1, f"ADR {num} 文件名不唯一：{matches}"
        text = _read(f"docs/adr/{matches[0].name}")
        assert re.search(r"\*\*状态\*\*[:：]\s*Accepted", text), f"ADR {num} 缺状态行"


def test_changelog_plugin_receipt_defer_count():
    changelog = _read("CHANGELOG.md")
    assert "test_plugin_receipt_defer.py`（11 例）" in changelog, "回归锁例数应与现值一致（11）"


def test_context_fastpath_path_prefix():
    context = _read("CONTEXT.md")
    assert "lantai/parsing/fastpath.py" in context, "词汇表路径应带 lantai/ 前缀"
    assert (
        re.search(r"(?<!/)parsing/fastpath\.py", context.replace("lantai/parsing/fastpath.py", ""))
        is None
    )


def test_context_registers_new_concepts():
    """ADR-0013 先登记再使用：格物功能条 + ADR-0084 新协议状态。"""
    context = _read("CONTEXT.md")
    assert "lantai/cognition/causal.py" in context, "格物功能名应有词汇表条目（现只有版本代号条）"
    assert "retrieval_veto" in context and "mark_vetoed" in context, (
        "ADR-0084 新协议状态应入技术字段段"
    )


def test_future_roadmap_archived_banner():
    head = _read("docs/future-roadmap.md")[:600]
    assert "已全部落地" in head, "四方向均已落地，应加归档横幅防误导"


def test_key_rotation_runbook_no_machine_paths():
    doc = _read("docs/key-rotation-runbook.md")
    assert "C:\\Users\\Asus" not in doc, "旧机硬编码路径"


def test_whitepaper_snapshot_banner():
    head = _read("docs/WHITEPAPER.md")[:600]
    assert "历史快照" in head, "WHITEPAPER 自钉 v0.15.2，应加历史快照横幅"


def test_matrix_data_dir_fallback_chain():
    matrix = _read("docs/mcp-client-matrix.md")
    for token in ("LANTAI_HOME", "REMEMBRANCE_HOME", "remembrance-data"):
        assert token in matrix, f"数据目录回落顺序缺 {token}（settings.py:507-523 实际四段）"


def test_manual_tool_prefix_annotation():
    manual = _read("docs/lantai-manual-call.md")
    line = next((ln for ln in manual.splitlines() if "mcp__remembrance__" in ln), None)
    assert line is not None, "前缀行应保留并加注"
    assert "前缀" in line and ("key" in line or "配置" in line), (
        f"前缀源自 Hermes 配置 key（与 serverInfo.name=lantai 是两层），应加注：{line}"
    )


def test_protocol_query_fallback_condition():
    doc = _read("docs/host-hook-protocol.md")
    assert "且带查询字段" not in doc, (
        "无 type/未匹配即回落，无『带查询字段』前置条件（host_protocol.py:126-135）"
    )


def test_daily_digest_has_reflection_row():
    doc = _read("docs/daily-digest.md")
    assert "反思提案" in doc, "统计表缺第七项（digest_worker._aggregate_reflection）"


def test_development_workflow_no_stale_venv_verify():
    doc = _read("docs/development-workflow.md")
    assert ".venv-verify" not in doc, "pyproject 已配 pythonpath 直跑，.venv-verify 口径过时"


def test_agents_domain_tree_uses_repo_root():
    doc = _read("docs/agents/domain.md")
    # 只锁示例树的包根（└── src/ 树形）；正文里多仓通用示意（src/<context>/…）不算
    assert re.search(r"└──\s*src/", doc) is None, "示例树包根应为 lantai/"
    assert "└── lantai/" in doc


def test_precommit_ruff_rev_pinned_to_project_pin():
    cfg = _read(".pre-commit-config.yaml")
    assert re.search(r"rev:\s*v0\.16", cfg), (
        "pre-commit ruff rev 应与 pyproject 钉值（>=0.16,<0.17）对齐"
    )


def test_test_count_claims_not_hardcoded_stale():
    assert "700+" not in _read("CONTRIBUTING.md"), "测试数硬编码过时"
    assert "1200+" not in _read("docs/glossary.md"), "测试数硬编码过时"


def test_design_no_build_step_claim():
    design = _read("DESIGN.md")
    assert "零外部庞大框架依赖" not in design, "悬镜实载 D3，该断言不实"
    assert "无构建步骤" in design


def test_release_gate_list_has_docs_reference_check():
    """哨兵上线连锁：门禁 13 项清单含新项（与 p1 锁的计数断言互为表里）。"""
    doc = _read("docs/release-process.md")
    section = doc.split("## 上传前门禁", 1)[1].split("## ", 1)[0]
    assert "文档引用对账" in section, "新哨兵应登记进门禁清单"
    assert "13 项" in section
