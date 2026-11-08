"""票 26：MCP 广告面反向缺口——消费面不得私藏（哨兵双向化 + 五处补声明）。

病灶（立案轮 5 AST 双向对账，五处亲核）：广告→消费方向零缺口（票 07 收口），
消费→广告方向五处缺口——`search.force` 旁路相关性闸未广告（治理暗门）、
`cehou_observe.mock_days_observed` 伪造观察窗未广告（REST 面同参已声明
「模拟观察天数（测试/巡检指定）」，MCP 面漏跟）、`user_id` 经
`_principal_from_params` 中心消费 66 工具未声明（30 已声明，归属链在广告面
不可达）、`search.session_id/shared_scope/task` 有行为无广告、
`memory_archived_search.scene_id` 未广告别名。

哨兵设计：AST **结构化提取**（TOOLS dict → inputSchema → properties 逐层
导航，绝不 ast.walk 提取——嵌套 items.properties 会覆写顶层键集，实证曾把
triage_apply 误判成隐藏 actions）；消费键集 = handler 体内 params.get("字面量")
与 _int_param(params, "字面量", …)（票 66 钳制 helper）调用；双向 ⊆ 断言 +
全局豁免表硬编码（防悄悄扩权）。
"""

import ast
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.cli.mcp import handle_search
from lantai.models.tables import RetrievalEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_PATH = REPO_ROOT / "lantai" / "cli" / "mcp.py"
PY = sys.executable

# 全局豁免表（硬编码防悄悄扩权）：user_id 由 _principal_from_params 中心消费
# （~90 个 handler 复用同一 helper，AST 无法按 handler 归因其消费）——
# 声明面由 test_user_id_declared_on_all_consuming_tools 单独锁死。
GLOBAL_EXEMPT = {"user_id"}


def _desc_text(node):
    """参数声明节点的 description 常量文本（非 Dict/缺省返回空串）。"""
    d = _dict_literal(node)
    v = d.get("description")
    return v.value if isinstance(v, ast.Constant) else ""


def _dict_literal(node):
    """Dict 字面量 → {常量键: 值节点}。"""
    if not isinstance(node, ast.Dict):
        return {}
    return {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}


def _load_audit():
    """结构化提取广告面/消费面（模块级缓存）。返回 (键集, 描述表, handlers, mapping)。"""
    tree = ast.parse(MCP_PATH.read_text(encoding="utf-8"))
    assigns = {
        n.targets[0].id: n
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    tools_node = assigns["TOOLS"].value
    tl_node = assigns["TOOL_HANDLERS"].value
    mapping = {
        k.value: v.id
        for k, v in zip(tl_node.keys, tl_node.values)
        if isinstance(k, ast.Constant) and isinstance(v, ast.Name)
    }
    advertised, descriptions = {}, {}
    for tname, schema_node in _dict_literal(tools_node).items():
        ins = _dict_literal(_dict_literal(schema_node).get("inputSchema"))
        props_node = ins.get("properties")
        props = _dict_literal(props_node) if props_node else {}
        advertised[tname] = set(props.keys())
        descriptions[tname] = {p: _desc_text(node) for p, node in props.items()}
    handlers = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    return advertised, descriptions, handlers, mapping


def _consumed_keys(fn):
    """handler 体内的直接消费键集：params.get("字面量") 与
    _int_param(params, "字面量", …)（票 66 钳制 helper——键字面量仍在调用点，
    AST 可逐 handler 精确归因，故收编进扫描器而非豁免表；其余未知消费形态
    一律视为不消费，哨兵保持 fail-closed）。"""
    keys = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            and isinstance(f.value, ast.Name)
            and f.value.id == "params"
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(f, ast.Name)
            and f.id == "_int_param"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "params"
            and isinstance(node.args[1], ast.Constant)
        ):
            keys.add(node.args[1].value)
    return keys


def _references(fn, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(fn))


# ── 哨兵：双向对账 ────────────────────────────────────────────────


def test_no_hidden_params():
    """消费 ⊆ 广告 ∪ 豁免（逐工具）：有行为、schema 无声明的参数即私藏。"""
    advertised, _, handlers, mapping = _load_audit()
    hidden = {}
    for tname, fname in mapping.items():
        fn = handlers.get(fname)
        assert fn is not None, f"工具 {tname} 的 handler {fname} 不存在"
        h = _consumed_keys(fn) - advertised[tname] - GLOBAL_EXEMPT
        if h:
            hidden[tname] = sorted(h)
    assert not hidden, (
        f"MCP 隐藏参数（消费 ⊄ 广告，票 26）：{hidden}——治理杠杆不得有暗门，广告或删除二选一"
    )


def test_no_ghost_params():
    """广告 ⊆ 消费 ∪ 豁免（逐工具，票 07 方向全局化）：不得虚广告。"""
    advertised, _, handlers, mapping = _load_audit()
    ghost = {}
    for tname, fname in mapping.items():
        fn = handlers.get(fname)
        g = advertised[tname] - _consumed_keys(fn) - GLOBAL_EXEMPT
        if g:
            ghost[tname] = sorted(g)
    assert not ghost, f"MCP 虚广告（广告 ⊄ 消费）：{ghost}"


def test_user_id_declared_on_all_consuming_tools():
    """user_id 声明全覆盖：凡 handler 引用 _principal_from_params 或内联
    params.get("user_id") 的工具，schema 必须声明 user_id（票 26 病灶 3：
    归属链在广告面不可达 = 宿主按 tools/list 开发即系统性漏配）。"""
    advertised, _, handlers, mapping = _load_audit()
    undeclared = {}
    for tname, fname in mapping.items():
        fn = handlers.get(fname)
        consumes_uid = _references(fn, "_principal_from_params") or (
            "user_id" in _consumed_keys(fn)
        )
        if consumes_uid and "user_id" not in advertised[tname]:
            undeclared[tname] = fname
    assert not undeclared, (
        f"user_id 消费未声明（票 26 病灶 3，{len(undeclared)} 工具）：{undeclared}——"
        "归属收窄靠 schema 不可见的参数，宿主按 tools/list 开发即漏配"
    )


def test_hidden_levers_declared_with_whitelist_semantics():
    """治理杠杆声明 + 白名单语义注记：force（评测/放行豁免）与
    mock_days_observed（测试/巡检模拟）必须广告且 description 注明豁免语义，
    与 REST 面同参同语义（routes_search.py 透传放行 / routes_cehou.py
    「模拟观察天数（测试/巡检指定）」）。"""
    advertised, descriptions, _, _ = _load_audit()
    force_desc = descriptions["search"].get("force", "")
    assert "force" in advertised["search"], "search.force 未广告（票 26 病灶 1 暗门）"
    assert any(kw in force_desc for kw in ("评测", "放行", "跳过")), (
        f"search.force 声明缺白名单语义注记：{force_desc!r}"
    )
    mock_desc = descriptions["cehou_observe"].get("mock_days_observed", "")
    assert "mock_days_observed" in advertised["cehou_observe"], (
        "cehou_observe.mock_days_observed 未广告（票 26 病灶 2）"
    )
    assert any(kw in mock_desc for kw in ("测试", "巡检", "模拟")), (
        f"mock_days_observed 声明缺白名单语义注记：{mock_desc!r}"
    )


def test_archived_search_scene_alias_removed():
    """未广告别名 scene_id 删除：memory_archived_search 只认已广告的 scene。"""
    _, _, handlers, _ = _load_audit()
    fn = handlers["handle_memory_archived_search"]
    assert "scene_id" not in _consumed_keys(fn), (
        "memory_archived_search 仍消费未广告别名 scene_id（票 26 病灶 5："
        "两名一参只文档一个，别名应删消费点）"
    )


# ── 行为锁（真库真链，防「只改 schema 不查行为」假绿）────────────────


@pytest.fixture()
def mcp_env():
    import lantai.models.tables  # noqa: F401
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
        patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock),
    ):
        yield session_factory, engine, vector_store_mock


def test_force_false_gate_semantics_unchanged(mcp_env):
    """现状锁：force=false 语义不变——真 relevance_check 照拦（短查询短路、
    不进 hybrid_search）；force=true 才放行进检索主路（治理杠杆是显式豁免，
    不是默认旁路）。"""
    session_factory, engine, _ = mcp_env
    r_no_force = handle_search({"query": "你好", "top_k": 5, "user_id": "gate-lock"})
    assert "gate" in r_no_force and r_no_force["gate"]["needs_memory"] is False
    assert "evidence" not in r_no_force, "无 force 的闸拦轮不该进检索主路（现状锁被破坏）"
    r_forced = handle_search({"query": "你好", "top_k": 5, "force": True, "user_id": "gate-lock"})
    assert "evidence" in r_forced, "force=true 应放行进检索主路（现状锁）"


def test_user_id_propagates_to_retrieval_event(mcp_env):
    """现状锁：user_id 透传归属落库（真内存库断言，防只改 schema 不查行为）。"""
    session_factory, engine, _ = mcp_env
    handle_search({"query": "归属落库锁", "top_k": 5, "user_id": "user-lock"})
    with session_factory() as s:
        row = s.exec(select(RetrievalEvent).where(RetrievalEvent.user_id == "user-lock")).first()
    assert row is not None, "user_id 透传未落 retrieval_event（归属链断）"


# ── 真子进程 MCP 冒烟（镜像票 05 形态：-I -X utf8 干净子进程真 import）──


def test_tools_list_schema_via_subprocess():
    """真子进程 tools/list 面：五个缺口参数与 66 个 user_id 声明必须在
    干净子进程的真 import 下可见（防测试进程内 patch 掩盖真实广告面）。"""
    code = (
        "import json\n"
        "from lantai.cli import mcp\n"
        "out = {}\n"
        "for name, tool in mcp.TOOLS.items():\n"
        "    props = tool['inputSchema'].get('properties', {})\n"
        "    out[name] = sorted(props.keys())\n"
        "print('MCPSCHEMA' + json.dumps(out, ensure_ascii=False))\n"
    )
    env = {
        k: v for k, v in __import__("os").environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(  # noqa: S603 — 固定 argv 测试装置
        [PY, "-I", "-X", "utf8", "-u", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("MCPSCHEMA"))
    out = json.loads(line[len("MCPSCHEMA") :])
    assert set(out["search"]) >= {"force", "session_id", "shared_scope", "task"}, (
        f"search 广告面缺参数（票 26）：{sorted(out['search'])}"
    )
    assert "mock_days_observed" in out["cehou_observe"], (
        "cehou_observe 广告面缺 mock_days_observed（票 26）"
    )
    # 消费驱动断言：凡消费 user_id 的工具必须声明（不消费的工具声明反成虚广告）
    _, _, handlers, mapping = _load_audit()
    consuming = sorted(
        t
        for t, fname in mapping.items()
        if _references(handlers.get(fname), "_principal_from_params")
        or "user_id" in _consumed_keys(handlers.get(fname))
    )
    missing_uid = sorted(t for t in consuming if "user_id" not in out.get(t, []))
    assert not missing_uid, f"user_id 消费未声明（票 26，{len(missing_uid)} 工具）：{missing_uid}"
