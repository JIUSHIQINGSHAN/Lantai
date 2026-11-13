"""票 27（v0.68-next-window）回归锁：MCP 约束面与描述面四处失真。

票面四病灶（schema 承诺了 handler 没做 / handler 做了 schema 没承诺）：

1. `triage_auto_pilot` schema 承诺 limit [1,500]、conf [0.0,1.0]
   （mcp.py TOOLS["triage_auto_pilot"]），handler `float()/int()` 强转零范围
   校验（handle_triage_auto_pilot）——limit=0/99999 直通 DB 查询、conf=9 全收；
   同族 `triage_analyze` 有校验，同名 limit 两工具行为不一致。
2. `candidate_review` / `crystal_decide` reject 分支服务层强制必填 reason
   （candidate_service.py:139 / crystal_service.py:226），schema 却默认 ""
   非必填——忠实按 schema 调用 {approve:false} 必吃 -32602（描述面补声明）。
3. `qijuzhu_chronicle` days>1 转投 get_timeline_service(days, limit)，
   date/detail_level 被静默丢弃（handle_qijuzhu_chronicle），schema 描述
   「默认今日」/枚举无分支注记（description 改案：注明仅单日分支生效）。
4. 弱承诺面：conflicts_list handler 校验 status 枚举 + limit [1,500]
   schema 零枚举零范围；search/verbatim_search top_k 校验 [1,100]
   schema 无范围；检索族 domain 消费端接受 "all"（hybrid.py `domain != "all"`
   哨兵）而 graph_expand_search/shiyi_remedy/shiyi_pipeline 声明面缺枚举。

两臂零内部 mock：
- 子进程真 stdio（test_mcp_stdio_entry_smoke.py 同形态）：校验臂发真实
  tools/call 帧断言 -32602，黑盒覆盖 handle→handler 全链。
- 帧级 handle() + TOOLS 静态对账：真 import，不 mock，声明面与 handler
  校验口径逐项相等。
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = ROOT / "lantai" / "cli" / "mcp.py"

pytestmark = pytest.mark.mcp_full_surface()


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server_schema_contract", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(mod, name, arguments):
    return mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


# ---------------------------------------------------------------------------
# 臂 1：真子进程 stdio——triage_auto_pilot 范围校验（修复前现状红：成功返回）
# ---------------------------------------------------------------------------


def test_stdio_triage_auto_pilot_rejects_out_of_range(tmp_path):
    """真子进程：limit=0/99999、min_approve_conf=9 必须 -32602（schema 承诺兑现）。"""
    env = dict(os.environ)
    home = tmp_path / "home"
    home.mkdir()
    env["LANTAI_HOME"] = str(home)
    env["REMEMBRANCE_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env["LANTAI_MCP_PROFILE"] = "full"  # 核心集（6 工具）不含 triage_auto_pilot
    env.pop("MCP_TOOL_PROFILE", None)

    frames = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"triage_auto_pilot","arguments":{"limit":0}}}',
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"triage_auto_pilot","arguments":{"limit":99999}}}',
        '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"triage_auto_pilot","arguments":{"min_approve_conf":9}}}',
    ]
    proc = subprocess.run(
        [sys.executable, "-c", "from lantai.cli.mcp import main; main()"],
        input=("\n".join(frames) + "\n").encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]

    by_id = {}
    for raw in proc.stdout.decode("utf-8").splitlines():
        resp = json.loads(raw)
        by_id[resp["id"]] = resp

    assert set(by_id) == {1, 2, 3, 4}, sorted(by_id)
    for bad_id, what in [(2, "limit=0"), (3, "limit=99999"), (4, "min_approve_conf=9")]:
        assert "error" in by_id[bad_id], f"{what} 应被拒，现状: {by_id[bad_id]}"
        assert by_id[bad_id]["error"]["code"] == -32602, f"{what}: {by_id[bad_id]['error']}"


# ---------------------------------------------------------------------------
# 臂 2：帧级参数矩阵 + 服务层 reject 必填现状锁
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_args",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 99999},
        {"limit": "50"},  # str 强转直通的旧形态必须拒
        {"limit": True},  # bool 是 int 子类，同族 triage_analyze 已拒
        {"min_approve_conf": 9},
        {"min_approve_conf": -0.5},
        {"min_approve_conf": "0.9"},
        {"max_reject_conf": 1.5},
    ],
)
def test_triage_auto_pilot_param_matrix_rejected(bad_args):
    mod = _load_mcp()
    resp = _call(mod, "triage_auto_pilot", bad_args)
    assert "error" in resp, f"{bad_args} 应 -32602，现状: {resp}"
    assert resp["error"]["code"] == -32602, resp["error"]


def test_triage_auto_pilot_valid_bounds_pass_validation():
    """合法边界值必须过校验（不因校验拒绝）；dry_run 空库返回报告不写库。"""
    mod = _load_mcp()
    resp = _call(
        mod,
        "triage_auto_pilot",
        {"limit": 500, "min_approve_conf": 0.0, "max_reject_conf": 1.0, "dry_run": True},
    )
    assert "error" not in resp, resp.get("error")
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "scanned" in payload, payload


def test_candidate_review_reject_requires_reason_current_lock(param_env):
    """现状锁：真实 pending 候选 + {approve:false} 无 reason 必失败且信息提及 reason。

    服务层先查库后验 reason（candidate_service.review_candidate），不存在的 id
    只会撞 "candidate not found"——必须造真行才走得到必填校验。
    """
    from lantai.models.tables import MemoryCandidate

    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id="t27-cand-reject",
                document_id="doc-t27",
                status="pending_review",
            )
        )
        s.commit()

    mod = _load_mcp()
    resp = _call(mod, "candidate_review", {"candidate_id": "t27-cand-reject", "approve": False})
    assert "error" in resp, "reject 无 reason 必须失败"
    assert "reason" in resp["error"]["message"], resp["error"]


def test_crystal_decide_reject_requires_reason_current_lock(param_env):
    """现状锁：真实结晶候选 + reject 无 reason 必失败且信息提及 reason。"""
    from lantai.models.tables import SkillCrystal

    session_factory, _ = param_env
    with session_factory() as s:
        s.add(SkillCrystal(id="t27-crystal-reject", skill_name="t27_lock_skill"))
        s.commit()

    mod = _load_mcp()
    resp = _call(mod, "crystal_decide", {"crystal_id": "t27-crystal-reject", "approve": False})
    assert "error" in resp, "reject 无 reason 必须失败"
    assert "reason" in resp["error"]["message"], resp["error"]


# ---------------------------------------------------------------------------
# 臂 3：TOOLS 声明面与 handler 校验口径静态对账（真 import，不 mock）
# ---------------------------------------------------------------------------


def test_conflicts_list_schema_matches_handler_validation():
    """handler 校验 status 枚举 + limit [1,500]，schema 必须如数承诺。"""
    mod = _load_mcp()
    schema = mod.TOOLS["conflicts_list"]["inputSchema"]["properties"]
    assert schema["status"].get("enum") == ["open", "resolved", "dismissed", "all"], schema[
        "status"
    ]
    assert "[1, 500]" in schema["limit"].get("description", ""), schema["limit"]


@pytest.mark.parametrize("tool", ["search", "verbatim_search"])
def test_search_family_top_k_schema_declares_bound(tool):
    """top_k handler 校验 [1,100]，schema description 必须声明边界。"""
    mod = _load_mcp()
    top_k = mod.TOOLS[tool]["inputSchema"]["properties"]["top_k"]
    assert "[1, 100]" in top_k.get("description", ""), (tool, top_k)


@pytest.mark.parametrize("tool", ["graph_expand_search", "shiyi_remedy", "shiyi_pipeline"])
def test_retrieval_domain_enum_unified_with_all(tool):
    """检索族 domain 消费端接受 "all"（hybrid 哨兵），schema 枚举必须如实声明。"""
    mod = _load_mcp()
    domain = mod.TOOLS[tool]["inputSchema"]["properties"]["domain"]
    assert domain.get("enum") == ["user", "session", "agent", "all"], (tool, domain)


def test_search_domain_enum_baseline_locked():
    """基线锚：search 的 domain 枚举含 "all"（统一口径的基准线）。"""
    mod = _load_mcp()
    domain = mod.TOOLS["search"]["inputSchema"]["properties"]["domain"]
    assert domain.get("enum") == ["user", "session", "agent", "all"], domain


@pytest.mark.parametrize("tool", ["candidate_review", "crystal_decide"])
def test_reason_description_declares_reject_required(tool):
    """reject 分支必填 reason 必须写进 schema description（忠实调用不吃 -32602）。"""
    mod = _load_mcp()
    reason = mod.TOOLS[tool]["inputSchema"]["properties"]["reason"]
    assert "必填" in reason.get("description", ""), (tool, reason)


def test_qijuzhu_chronicle_branch_scoped_params_declared():
    """date/detail_level 仅单日分支生效必须写进 schema description（不再暗示全程生效）。"""
    mod = _load_mcp()
    props = mod.TOOLS["qijuzhu_chronicle"]["inputSchema"]["properties"]
    assert "days=1" in props["date"].get("description", ""), props["date"]
    assert "days=1" in props["detail_level"].get("description", ""), props["detail_level"]


def test_qijuzhu_chronicle_multi_day_ignores_single_day_params_current_lock():
    """现状锁：days>1 走 timeline 分支，date/detail_level 不影响返回形态。"""
    mod = _load_mcp()
    base = json.loads(_call(mod, "qijuzhu_chronicle", {"days": 3})["result"]["content"][0]["text"])
    noisy = json.loads(
        _call(mod, "qijuzhu_chronicle", {"days": 3, "date": "2026-01-01", "detail_level": "full"})[
            "result"
        ]["content"][0]["text"]
    )
    # 同库同窗下两次多日查询的键形态一致（date/detail_level 均不参与多日分支）
    assert set(base) == set(noisy), (sorted(base), sorted(noisy))


@pytest.mark.parametrize(
    ("tool", "param", "bound"),
    [
        ("scenes_list", "limit", "[1, 500]"),
        ("candidates_pending", "limit", "[1, 500]"),
    ],
)
def test_list_tools_limit_bound_declared(tool, param, bound):
    """搭车声明（票 27 病灶 4）：handler 校验 [1,500] 的列表工具 schema 必须声明。"""
    mod = _load_mcp()
    prop = mod.TOOLS[tool]["inputSchema"]["properties"][param]
    assert bound in prop.get("description", ""), (tool, prop)


@pytest.mark.parametrize(
    ("tool", "param", "bound"),
    [
        ("recall_report", "days", "[1, 365]"),
        ("autodream_report", "limit", "[1, 5000]"),
    ],
)
def test_optional_param_bound_declared(tool, param, bound):
    """搭车声明（票 27 病灶 4）：可选参数 handler 校验 [1,N]，schema 必须声明边界。"""
    mod = _load_mcp()
    prop = mod.TOOLS[tool]["inputSchema"]["properties"][param]
    assert bound in prop.get("description", ""), (tool, prop)


def test_add_content_min_length_declared():
    """搭车声明（票 27 病灶 4）：content ≥10 字符（schemas.py AddMemoryReq 校验）必须声明。"""
    mod = _load_mcp()
    content = mod.TOOLS["add"]["inputSchema"]["properties"]["content"]
    assert "10" in content.get("description", ""), content


def test_triage_auto_pilot_no_op_cast_removed():
    """守卫：limit 校验后不得残留 int() 强转（isinstance 已保证，no-op 误导读者）。"""
    import inspect

    mod = _load_mcp()
    src = inspect.getsource(mod.handle_triage_auto_pilot)
    assert "int(limit)" not in src, "校验后的 int(limit) 是 no-op，应删除"
