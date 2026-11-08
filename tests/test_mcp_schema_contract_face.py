"""票 27：MCP 约束面与描述面失真——schema 承诺了没做 / 做了没承诺。

病灶（立案轮 5 普查，HEAD `dc7d86b5` 逐处亲核）：
1. `triage_auto_pilot` schema 承诺 limit [1,500]、conf [0.0,1.0]，handler 裸
   int()/float() 强转零范围检查（同族 `triage_analyze` 有 1..500 校验，同名
   limit 两工具行为不一致）——虚广告方向；
2. `candidate_review`/`crystal_decide` 的 reject 分支服务层强制 reason 必填，
   schema 广告为可选且无描述——描述面欠账（对照 `memory_unarchive` 真 可选
   reason 的认真范例）；
3. `qijuzhu_chronicle` 的 date/detail_level 在 days>1 分支被静默丢弃（票 07
   「假承诺」轻量版：参数收到、不消费）；
4. 弱广告搭车面：conflicts_list.status 枚举 / 各工具 limit·top_k·days 数值
   界 / add.content 最短长度——handler 强校验、schema 零承诺，按 schema 写的
   调用方靠试错发现契约。

哨兵形态同票 26：AST 结构化导航提取（绝不 ast.walk——嵌套 items.properties
覆写陷阱）+ 真子进程 dispatch 冒烟（-I -X utf8 + 隔离 LANTAI_HOME + 真
init_db，零 mock）。**不可修项如实登记**：`domain` 枚举跨工具不一致不是失真
——search 的 "all"=不过滤（hybrid 语义），muci/shiyi_pipeline 的 domain 是
`MemoryItem.domain` 列等值过滤（"all"=零行静默空结果），语义不同源，见
test_domain_enum_semantics_difference_is_not_a_defect。
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.cli.mcp import handle, handle_qijuzhu_chronicle
from lantai.models.tables import MemoryCandidate

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_PATH = REPO_ROOT / "lantai" / "cli" / "mcp.py"
PY = sys.executable


def _dict_literal(node):
    if not isinstance(node, ast.Dict):
        return {}
    return {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}


def _desc_text(node):
    d = _dict_literal(node)
    v = d.get("description")
    return v.value if isinstance(v, ast.Constant) else ""


def _json_value(node):
    """声明节点的 JSON 值（Constant→标量、List→列表）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_json_value(e) for e in node.elts]
    return None


def _load_props():
    """结构化提取：{工具名: {参数名: 参数声明节点}}（逐层导航，不走 walk）。"""
    tree = ast.parse(MCP_PATH.read_text(encoding="utf-8"))
    assigns = {
        n.targets[0].id: n
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    tools = _dict_literal(assigns["TOOLS"].value)
    out = {}
    for tname, schema_node in tools.items():
        ins = _dict_literal(_dict_literal(schema_node).get("inputSchema"))
        props_node = ins.get("properties")
        out[tname] = _dict_literal(props_node) if props_node else {}
    return out


def _call_tool(tool: str, arguments: dict) -> dict:
    """真 dispatch 一跳（ValueError → -32602 的映射走生产路径）。"""
    return handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )


def _ok(resp: dict) -> bool:
    return "result" in resp


def _err(resp: dict) -> tuple[int, str]:
    e = resp.get("error") or {}
    return int(e.get("code", 0)), str(e.get("message", ""))


# ── schema 声明面（AST，红：承诺缺关键词/缺描述）────────────────────


def test_triage_auto_pilot_ranges_machine_readable():
    """schema 的数值范围必须是机器可读关键词（minimum/maximum），不只靠
    description 文案——handler 校验与 schema 承诺才有可比对的锚。"""
    props = _load_props()["triage_auto_pilot"]
    limit = _dict_literal(props["limit"])
    assert _json_value(limit.get("minimum")) == 1 and _json_value(limit.get("maximum")) == 500, (
        f"limit 范围承诺非机器可读（票 27 病灶 1）：{limit}"
    )
    for name in ("min_approve_conf", "max_reject_conf"):
        node = _dict_literal(props[name])
        assert (
            _json_value(node.get("minimum")) == 0.0 and _json_value(node.get("maximum")) == 1.0
        ), f"{name} 范围承诺非机器可读（票 27 病灶 1）：{node}"


def test_conflicts_list_status_enum_and_limit_bounds():
    """conflicts_list：handler 已强校验 status 枚举与 limit [1,500]，schema
    零承诺——做了没承诺（票 27 病灶 4）。"""
    props = _load_props()["conflicts_list"]
    status = _dict_literal(props["status"])
    enum = _json_value(status.get("enum"))
    assert enum == ["open", "resolved", "dismissed", "all"], (
        f"status 枚举未广告（票 27 病灶 4）：{enum}"
    )
    limit = _dict_literal(props["limit"])
    assert _json_value(limit.get("minimum")) == 1 and _json_value(limit.get("maximum")) == 500


def test_conditional_required_reason_described():
    """candidate_review / crystal_decide：reject 分支服务层强制 reason 必填，
    schema 必须注明条件必填（描述层说明，与 revive_consolidated 范例同风格）。"""
    props = _load_props()
    for tool in ("candidate_review", "crystal_decide"):
        desc = _desc_text(props[tool]["reason"])
        assert "reject" in desc and "必填" in desc, (
            f"{tool}.reason 缺条件必填注记（票 27 病灶 2）：{desc!r}"
        )


def test_qijuzhu_branch_semantics_described():
    """qijuzhu_chronicle：date/detail_level 仅单日分支生效——description 必须
    注明分支语义（票 27 病灶 3 描述面）。"""
    props = _load_props()["qijuzhu_chronicle"]
    for name in ("date", "detail_level"):
        desc = _desc_text(props[name])
        assert "单日" in desc, f"qijuzhu_chronicle.{name} 缺分支语义注记（票 27 病灶 3）：{desc!r}"


def test_weak_advertised_bounds_described():
    """搭车抽检面（票 27 病灶 4）：handler 强校验、schema 零承诺的数值界/长度
    补机器可读承诺（纯声明，零行为变化）。"""
    props = _load_props()
    checks = [
        ("search", "top_k", {"minimum": 1, "maximum": 100}),
        ("verbatim_search", "top_k", {"minimum": 1, "maximum": 100}),
        ("scenes_list", "limit", {"minimum": 1, "maximum": 500}),
        ("candidates_pending", "limit", {"minimum": 1, "maximum": 500}),
        ("recall_report", "days", {"minimum": 1, "maximum": 365}),
        ("autodream_report", "limit", {"minimum": 1, "maximum": 5000}),
    ]
    for tool, param, want in checks:
        node = _dict_literal(props[tool][param])
        for k, v in want.items():
            assert _json_value(node.get(k)) == v, (
                f"{tool}.{param} 缺 {k}={v}（票 27 病灶 4）：{node}"
            )
    content = _dict_literal(props["add"]["content"])
    assert _json_value(content.get("minLength")) == 10, f"add.content 缺 minLength=10：{content}"


def test_domain_enum_semantics_difference_is_not_a_defect():
    """不可修项锁（复查结论）：muci_catalog/shiyi_pipeline 的 domain 是
    `MemoryItem.domain` 列等值过滤（muci.py:104），"all" 会静默返回零行——
    与 search 的 "all"=不过滤语义不同源，枚举**不得**统一到含 "all"
    （工单修复目标 4 的「统一」方向经消费端核对判不可修）。"""
    props = _load_props()
    for tool in ("muci_catalog", "shiyi_pipeline"):
        enum = _json_value(_dict_literal(props[tool]["domain"]).get("enum"))
        assert "all" not in (enum or []), (
            f"{tool}.domain 枚举被误加 all（列等值过滤语义下 all=静默空结果）：{enum}"
        )
    assert _json_value(_dict_literal(props["muci_catalog"]["domain"]).get("enum")) == [
        "user",
        "session",
        "agent",
    ], "muci_catalog.domain 枚举漂移"


# ── 行为面（真 dispatch / 隔离子进程，红：承诺没做）──────────────────


def test_triage_auto_pilot_bounds_enforced(tmp_path):
    """红测主臂：triage_auto_pilot 的 limit/conf 越界必须 -32602（现状：
    裸强转零检查，limit=0/99999/conf=1.5 全部成功返回）。

    真子进程（-I -X utf8 + 隔离 LANTAI_HOME + 真 init_db），dry_run=True
    保证越界样本（修复前会真跑巡检）零副作用。"""
    cases = [
        ({"limit": 0, "dry_run": True}, "limit must be an int in [1, 500]"),
        ({"limit": 99999, "dry_run": True}, "limit must be an int in [1, 500]"),
        ({"min_approve_conf": 1.5, "dry_run": True}, "min_approve_conf"),
        ({"max_reject_conf": -0.5, "dry_run": True}, "max_reject_conf"),
    ]
    code = (
        "import json\n"
        "from lantai.storage import db\n"
        "db.init_db()\n"
        "from lantai.cli import mcp\n"
        "out = []\n"
        "for args in json.loads(__import__('sys').argv[1]):\n"
        "    resp = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',\n"
        "                       'params': {'name': 'triage_auto_pilot', 'arguments': args}})\n"
        "    out.append(resp)\n"
        "print('MCPCALLS' + json.dumps(out, ensure_ascii=False))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    env["LANTAI_HOME"] = str(tmp_path / "home")
    env["LANTAI_MCP_PROFILE"] = "full"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(  # noqa: S603 — 固定 argv 测试装置
        [PY, "-I", "-X", "utf8", "-u", "-c", code, json.dumps([c[0] for c in cases])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("MCPCALLS"))
    results = json.loads(line[len("MCPCALLS") :])
    for (args, want_msg), resp in zip(cases, results):
        assert not _ok(resp), f"越界参数被静默接受（票 27 病灶 1）：{args} → {resp}"
        code_, msg = _err(resp)
        assert code_ == -32602, f"{args} 错误码 {code_}（应为 -32602）：{msg}"
        assert want_msg.split(" ")[0] in msg or want_msg in msg, f"{args} 错误信息不可定位：{msg!r}"


def test_triage_auto_pilot_boundaries_accepted(tmp_path):
    """现状锁（修复后语义）：合法边界值 1/500、0.0/1.0 照常执行（dry_run）。"""
    code = (
        "import json\n"
        "from lantai.storage import db\n"
        "db.init_db()\n"
        "from lantai.cli import mcp\n"
        "args = {'limit': 500, 'min_approve_conf': 1.0, 'max_reject_conf': 0.0, 'dry_run': True}\n"
        "resp = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',\n"
        "                   'params': {'name': 'triage_auto_pilot', 'arguments': args}})\n"
        "print('MCPCALLS' + json.dumps([resp], ensure_ascii=False))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    env["LANTAI_HOME"] = str(tmp_path / "home")
    env["LANTAI_MCP_PROFILE"] = "full"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(  # noqa: S603
        [PY, "-I", "-X", "utf8", "-u", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("MCPCALLS"))
    (resp,) = json.loads(line[len("MCPCALLS") :])
    assert _ok(resp), f"合法边界值被误拒（票 27）：{_err(resp)}"


def test_conflicts_list_status_enforced(monkeypatch):
    """现状锁：conflicts_list 的 status 枚举与 limit 界 handler 一直强校验
    （票 27 病灶 4 的「做了」侧）。"""
    monkeypatch.setenv("LANTAI_MCP_PROFILE", "full")
    bad = _call_tool("conflicts_list", {"status": "bogus"})
    code_, msg = _err(bad)
    assert code_ == -32602 and "status" in msg, f"非法 status 未拦：{code_} {msg}"
    good = _call_tool("conflicts_list", {"status": "open"})
    assert _ok(good), f"合法 status 被误拒：{_err(good)}"


def test_reject_reason_required_error_message(monkeypatch):
    """现状锁（票 27 病灶 2 的「做了」侧）：reject 无 reason 服务层强制必填，
    错误信息可定位（含 reason）。"""
    monkeypatch.setenv("LANTAI_MCP_PROFILE", "full")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch_db(session_factory),
        _SeedCandidate(session_factory),
    ):
        resp = _call_tool(
            "candidate_review",
            {"candidate_id": "cand-lock", "approve": False},
        )
        code_, msg = _err(resp)
        assert code_ == -32602 and "reason" in msg.lower(), (
            f"reject 无 reason 错误信息不可定位：{code_} {msg!r}"
        )


def test_qijuzhu_multi_day_marks_unused_params():
    """红测主臂（票 27 病灶 3）：days>1 时 date/detail_level 被静默丢弃——
    回执体必须含「参数未适用」标记（宁 visible 不静默）；单日分支真消费、
    不加标记。"""
    from unittest.mock import Mock, patch

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    vs = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.storage.vector_store.get_vector_store", return_value=vs),
    ):
        multi = handle_qijuzhu_chronicle({"days": 3, "date": "2026-01-01", "detail_level": "full"})
        text = json.dumps(multi, ensure_ascii=False)
        assert "参数未适用" in text, (
            f"多日分支静默丢弃 date/detail_level（票 27 病灶 3）：{text[:300]}"
        )
        single = handle_qijuzhu_chronicle({"days": 1, "date": "2026-01-01"})
        assert "参数未适用" not in json.dumps(single, ensure_ascii=False), "单日分支误报参数未适用"


# ── 小工具 ────────────────────────────────────────────────────────


def patch_db(session_factory):
    from unittest.mock import patch

    import lantai.models.tables  # noqa: F401

    return patch.object(db_module, "get_session", session_factory)


class _SeedCandidate:
    """种子 pending_review 候选（reject 流程先验存在与状态，再验 reason）。"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def __enter__(self):
        with self.session_factory() as s:
            s.add(
                MemoryCandidate(
                    id="cand-lock",
                    document_id="doc-lock",
                    status="pending_review",
                    summary="票 27 reject reason 现状锁种子",
                )
            )
            s.commit()
        return self

    def __exit__(self, *exc):
        return False
