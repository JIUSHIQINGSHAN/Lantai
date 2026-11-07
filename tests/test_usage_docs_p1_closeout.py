"""usage docs P1 收口回归锁（v0.68 工单 08）。

11 项 P1（组 A-G）的文档-代码一致性锁；与票 06 的 `test_usage_docs_p0_closeout.py`
分工：本文件锁「表述/口径/参数过时」面，且把门禁清单与 `release_check.py`
实际检查序列绑定（唯一事实源，防再漂移）。

不 mock：settings 值、prefilter gate、release_check 模块、CI 配置均为真实导入/读取。
文档断言按内容模式不按行号。历史归档文档（handoff/WHITEPAPER/future-roadmap）
归票 09 横幅处置，本文件不锁。
"""

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _load_release_check():
    spec = importlib.util.spec_from_file_location(
        "release_check_p1lock", REPO / "scripts" / "release_check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 组 A：host-hook-protocol（D-04 画像兜底未回写 + 票 01 零命中语义融合）──


def test_protocol_persona_fallback_wording():
    doc = _read("docs/host-hook-protocol.md")
    assert "无命中/超短查询 → 无此键" not in doc, (
        "空命中/超短实际走画像兜底帧，不是『无 context 键』"
    )
    assert "画像" in doc, "应写明空命中/超短/异常三路的 D-04 画像兜底帧"
    # 票 01 语义融合：零命中（事件落库）仍带 event_id——不得被画像帧表述覆盖掉
    assert "零命中且事件落库成功时同样带出" in doc


def test_protocol_query_timeout_fallback():
    doc = _read("docs/host-hook-protocol.md")
    assert "超时只返回空帧" not in doc, "query 通道超时走 _query_fallback 降级画像帧，不是空帧"
    assert "_query_fallback" in doc or ("超时" in doc and "画像" in doc)


# ── 组 B：lantai-hermes-plugin 回执段（PR #33 / ADR-0084 回执延后）─────────


def test_plugin_receipt_deferred():
    doc = _read("docs/lantai-hermes-plugin.md")
    assert "检索注入成功后按响应" not in doc, (
        "回执已延后到 on_session_end，不再是注入成功后立即回执"
    )
    assert "on_session_end" in doc and "retrieval_veto" in doc
    assert "延后" in doc


# ── 组 C：used-ids-backfill-guide（档位前提 + 旧包名）─────────────────────


def test_usedids_no_old_package_import():
    doc = _read("docs/used-ids-backfill-guide.md")
    assert "from remembrance" not in doc, "包已更名 lantai，旧 import 一跑即炸"
    assert "from lantai." in doc, "验证代码应使用现行包名"


def test_usedids_backfill_channel_and_profile():
    doc = _read("docs/used-ids-backfill-guide.md")
    assert "POST /retrieval/backfill" in doc, "REST backfill 端点是主力通道"
    assert "LANTAI_MCP_PROFILE=full" in doc, "MCP backfill 不在默认档 6 工具，须写明 full 档前提"
    assert "4 个" not in doc, "工具清单口径已改两档，『4 个』假口径须清"


# ── 组 D：release-process（镜像名 + 门禁清单=唯一事实源）──────────────────


def test_release_mirror_name_matches_ci():
    doc = _read("docs/release-process.md")
    ci = _read(".github/workflows/ci.yml")
    assert "remembrance:vX" not in doc, "CI 实际产物是 lantai 镜像，照文档 pull 必失败"
    assert "remembrance" not in doc, "release 现役文档不得残留旧品牌镜像名"
    assert "lantai:vX" in doc or "lantai:" in doc
    # 与 CI 产物互证：CI 里构建的镜像名
    assert re.search(r"lantai", ci) and "remembrance" not in ci


def test_release_gate_list_matches_real_check_sequence():
    """门禁清单与 release_check.py 实际 check 序列逐项对表（唯一事实源）。"""
    rc_src = _read("scripts/release_check.py")
    real_names = sorted(set(re.findall(r'check\("([^"]+)"', rc_src)))
    assert len(real_names) >= 13, f"门禁检查项变了（现 {len(real_names)}）——先更新本锁与文档清单"

    doc = _read("docs/release-process.md")
    section = doc.split("## 上传前门禁", 1)[1].split("## ", 1)[0]
    for name in real_names:
        assert name in section, f"门禁清单缺真实检查项「{name}」——照单全收，不凭记忆"
    assert "13 项" in section or "十三项" in section, "清单应如实标注项数"


def test_release_pytest_command_matches_gate():
    rc = _load_release_check()
    cmd = " ".join(rc.suite_pytest_command())
    doc = _read("docs/release-process.md")
    section = doc.split("## 上传前门禁", 1)[1].split("## ", 1)[0]
    # 文档的 pytest 口径必须含门禁真命令的判别性片段（覆盖率插桩 + addopts 覆写）
    assert "-o" in cmd and "addopts=" in cmd, "门禁命令形态变了——先更新本锁"
    assert "-o addopts=" in section, "文档 pytest 命令须与门禁口径一致（-o addopts=）"
    assert "--cov" in cmd
    assert "--cov" in section or "覆盖率" in section, "文档应写明覆盖率插桩口径"


# ── 组 E：ADR-0013 §7 版本代号表补登───────────────────────────────────────


def test_naming_table_has_latest_versions():
    doc = _read("docs/adr/0013-naming-system.md")
    context = _read("CONTEXT.md")
    for ver, name in (("v0.65.0", "钩玄"), ("v0.66.0", "探赜")):
        assert re.search(rf"\|\s*{ver}\s*\|\s*{name}\s*\|", doc), f"§7 缺 {ver} {name} 行"
        assert name in context, f"{name} 应已登记 CONTEXT.md（双向登记）"


# ── 组 F：CONTRIBUTING 过时示例────────────────────────────────────────────


def test_contributing_example_tests_exist():
    doc = _read("CONTRIBUTING.md")
    assert "test_gate.py" not in doc, "tests/ 下无 test_gate*，示例照跑必败"
    for ref in sorted(set(re.findall(r"tests/[\w/]+\.py", doc))):
        assert (REPO / ref).exists(), f"CONTRIBUTING 示例引用的测试文件不存在：{ref}"


# ── 组 G：gate 口径与运维参数（三处过时 + 两处假数字）─────────────────────


def test_manual_gate_rules_current():
    from lantai.gate import prefilter

    manual = _read("docs/lantai-manual-call.md")
    # ADR-0028 放宽通道必须入文（技术词/问句），且不得再把「帮我查」写成触发词
    assert "技术词" in manual and ("问句" in manual or "疑问" in manual), (
        "gate 放宽口径（ADR-0028）未回写"
    )
    assert "命中 gate 规则" not in manual, "「帮我查」不在任何触发正则，不得写成命中规则"
    assert "force" in manual, "search 的 force 绕闸参数应登记"
    # 真实 gate 行为锚：文档推荐的查询形态必须真能触发检索（先清 15s 热缓存防串味）
    prefilter._LAST_GATE_DECISION.update({"time": 0.0, "query": "", "needs_memory": False})
    assert prefilter.relevance_check("帮我查一下大哥的电脑配置是什么呀")["needs_memory"] is True


def test_manual_no_signal_example_still_real():
    """R-1/R-3 随行保护：票 06 的真实反例不得被重写顶掉；短追问沿用须写明单向。"""
    manual = _read("docs/lantai-manual-call.md")
    bad = [ln for ln in manual.splitlines() if "⚠️" in ln and "大哥的电脑配置" in ln]
    assert not bad, f"no_signal 反例行不得回退为会被放行的例句：{bad}"
    assert "物业费在哪交" in manual
    if "短追问" in manual:
        assert re.search(r"短追问[^。\n]*命中", manual), (
            "短追问沿用是单向的（仅上轮命中时），须写明「命中」限定"
        )


def test_runbook_observation_window_matches_settings():
    from lantai.core.settings import settings

    runbook = _read("docs/ops-runbook.md")
    assert "7 天中至少 3 天" not in runbook, "观察期假门槛：实际 14 天窗口内至少 7 次运行"
    assert str(settings.REFLECT_OBSERVATION_WINDOW_DAYS) in runbook, (
        "窗口天数须与 settings 现值一致"
    )
    assert str(settings.REFLECT_OBSERVATION_REQUIRED_RUNS) in runbook, (
        "要求运行次数须与 settings 现值一致"
    )


def test_runbook_candidate_confidence_matches_settings():
    from lantai.core.settings import settings

    runbook = _read("docs/ops-runbook.md")
    assert "当前 0.0" not in runbook, "CANDIDATE_MIN_CONFIDENCE 默认已是 0.15，不是 0.0"
    assert str(settings.CANDIDATE_MIN_CONFIDENCE) in runbook, "文档建议值须与 settings 现值一致"
