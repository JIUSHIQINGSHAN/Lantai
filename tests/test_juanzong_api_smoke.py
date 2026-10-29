"""卷宗（Juanzong）API、服务层、MCP 工具与认知上下文集成冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层与 API/MCP，不 mock 核心计算与存储过程。
- 覆盖 5 大公共接缝：
  1. dossier_service 服务层接口与 Principal 租户隔离；
  2. REST 路由端点 (/dossiers, /dossiers/{id}/bind, /dossiers/{id}/milestones, /dossiers/{id}/focus)；
  3. MCP 工具 dossier_create 与 dossier_focus 调用往返；
  4. MCP 76 工具池一致性双向守卫；
  5. CognitiveContext 卷宗切面格式化与樊篱（wrap_as_data）防注入隔离。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lantai.api.app import app
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem
from lantai.services.dossier_service import (
    bind_memories_service,
    close_dossier_service,
    create_dossier_service,
    focus_dossier_service,
    get_dossier_service,
    list_dossiers_service,
    unbind_memories_service,
    update_milestone_service,
)


def _principal(user_id: str, *, admin: bool = False, tenant_id: str | None = None) -> Principal:
    p = Principal(
        user_id=user_id,
        role="admin" if admin else "user",
    )
    if tenant_id:
        p.tenant_id = tenant_id
    return p


def _seed_memory(
    param_env,
    mem_id: str,
    content: str,
    user_id: str = "default",
    tenant_id: str | None = None,
):
    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=mem_id,
                key=f"k-{mem_id}",
                content=content,
                status="active",
                user_id=user_id,
                tenant_id=tenant_id,
                confidence=0.9,
            )
        )
        s.commit()


def test_dossier_service_seam(param_env):
    """Seam 1: 服务层接口与 Principal 隔离。"""
    user_alice = _principal("user_alice")
    user_bob = _principal("user_bob")

    _seed_memory(param_env, "mem_svc_1", "架构演进原则：单向依赖与分层治理", user_id="user_alice")
    _seed_memory(param_env, "mem_svc_2", "数据库连接池必须限制最大活跃数", user_id="user_alice")

    # 1. 创建卷宗
    created = create_dossier_service(
        title="架构演进第一战役",
        goal="清理循环依赖并优化连接池",
        milestones=[
            {"id": "ms-1", "title": "识别循环依赖", "status": "pending"},
            {"id": "ms-2", "title": "落地依赖倒置", "status": "pending"},
        ],
        memory_ids=["mem_svc_1"],
        principal=user_alice,
    )
    assert created["id"].startswith("dos_")
    assert created["title"] == "架构演进第一战役"
    assert created["status"] == "active"
    assert len(created["milestones"]) == 2

    dos_id = created["id"]

    # 2. 获取详情
    retrieved = get_dossier_service(dos_id, principal=user_alice)
    assert retrieved is not None
    assert retrieved["id"] == dos_id

    # 租户隔离校验：Bob 无法获取 Alice 的卷宗
    assert get_dossier_service(dos_id, principal=user_bob) is None

    # 3. 绑定与解绑记忆
    bind_res = bind_memories_service(dos_id, ["mem_svc_2"], principal=user_alice)
    assert bind_res["ok"] is True
    assert "mem_svc_2" in bind_res["memory_ids"]

    # 解绑记忆验证（覆盖 unbind_memories_service 冒烟）
    unbind_res = unbind_memories_service(dos_id, ["mem_svc_2"], principal=user_alice)
    assert unbind_res["ok"] is True
    assert "mem_svc_2" not in unbind_res["memory_ids"]

    # 重新绑定以便后续聚焦
    bind_memories_service(dos_id, ["mem_svc_2"], principal=user_alice)

    # 4. 更新里程碑
    ms_res = update_milestone_service(
        dos_id,
        milestone_id="ms-1",
        status="done",
        notes="已用工具扫描出 3 处循环依赖并拆解",
        principal=user_alice,
    )
    assert ms_res is not None
    ms_item = next(m for m in ms_res["milestones"] if m["id"] == "ms-1")
    assert ms_item["status"] == "done"
    assert "3 处循环依赖" in ms_item["notes"]

    # 5. 情境记忆聚焦
    focus = focus_dossier_service(dos_id, query="连接池治理", limit=10, principal=user_alice)
    assert focus["dossier_id"] == dos_id
    assert len(focus["focused_memories"]) == 2
    # 命中连接池相关的内容优先
    assert "连接池" in focus["focused_memories"][0]["content"]

    # 6. 办结卷宗
    closed = close_dossier_service(dos_id, summary="循环依赖全部清零", principal=user_alice)
    assert closed["status"] == "completed"
    assert closed["metadata"].get("closing_summary") == "循环依赖全部清零"


def test_dossier_rest_api_seam(param_env):
    """Seam 2: REST 路由端点端到端冒烟。"""
    _seed_memory(param_env, "api_mem_1", "FastAPI 路由须带统一错误拦截", user_id="default")

    client = TestClient(app)

    # 1. POST /dossiers 创建卷宗
    resp_create = client.post(
        "/dossiers",
        json={
            "title": "API 统一收敛专项",
            "goal": "消除全系统 4xx/5xx 散乱报文",
            "milestones": [{"id": "ms-api-1", "title": "定义错误模型", "status": "pending"}],
            "memory_ids": ["api_mem_1"],
        },
    )
    assert resp_create.status_code == 200, resp_create.text
    created = resp_create.json()
    dos_id = created["id"]
    assert dos_id.startswith("dos_")

    # 2. GET /dossiers 查询列表
    resp_list = client.get("/dossiers")
    assert resp_list.status_code == 200
    items = resp_list.json()
    assert any(it["id"] == dos_id for it in items)

    # 3. GET /dossiers/{id} 获取详情
    resp_get = client.get(f"/dossiers/{dos_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == dos_id

    # 4. POST /dossiers/{id}/milestones 更新里程碑
    resp_ms = client.post(
        f"/dossiers/{dos_id}/milestones",
        json={"milestone_id": "ms-api-1", "status": "in_progress", "notes": "正在编写 schema"},
    )
    assert resp_ms.status_code == 200
    ms_data = resp_ms.json()
    assert ms_data["milestones"][0]["status"] == "in_progress"

    # 5. POST /dossiers/{id}/focus 情境聚焦
    resp_focus = client.post(
        f"/dossiers/{dos_id}/focus",
        json={"query": "错误拦截模型", "limit": 5},
    )
    assert resp_focus.status_code == 200
    focus_data = resp_focus.json()
    assert focus_data["dossier_id"] == dos_id
    assert len(focus_data["focused_memories"]) >= 1

    # 6. 未知卷宗 404 响应
    resp_focus_404 = client.post(
        "/dossiers/dos_nonexistent/focus",
        json={"query": "测试", "limit": 5},
    )
    assert resp_focus_404.status_code == 404


def test_dossier_mcp_tools_seam(param_env):
    """Seam 3: MCP 工具 dossier_create 与 dossier_focus 调度往返。"""
    _seed_memory(param_env, "mcp_mem_1", "MCP 工具必须遵循 JSON-RPC 2.0 规范", user_id="default")

    from lantai.cli import mcp

    # 1. 调用 dossier_create
    msg_create = {
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {
            "name": "dossier_create",
            "arguments": {
                "title": "MCP 工具集建设战役",
                "goal": "扩充卷宗情境工具并保证 100% 协议兼容",
                "memory_ids": ["mcp_mem_1"],
                "milestones": [{"id": "ms-mcp-1", "title": "注册 dossier 工具", "status": "done"}],
            },
        },
    }
    resp_create = mcp.handle(msg_create)
    assert resp_create is not None
    assert "error" not in resp_create, resp_create.get("error")
    import json

    res_data = json.loads(resp_create["result"]["content"][0]["text"])
    dos_id = res_data["id"]
    assert dos_id.startswith("dos_")

    # 2. 调用 dossier_focus
    msg_focus = {
        "jsonrpc": "2.0",
        "id": 202,
        "method": "tools/call",
        "params": {
            "name": "dossier_focus",
            "arguments": {
                "dossier_id": dos_id,
                "query": "JSON-RPC 协议规范",
            },
        },
    }
    resp_focus = mcp.handle(msg_focus)
    assert resp_focus is not None
    assert "error" not in resp_focus, resp_focus.get("error")
    focus_res = json.loads(resp_focus["result"]["content"][0]["text"])
    assert focus_res["dossier_id"] == dos_id
    assert len(focus_res["focused_memories"]) == 1
    assert "JSON-RPC" in focus_res["focused_memories"][0]["content"]


def test_mcp_tool_count_and_parity_seam():
    """Seam 4: MCP 工具池扩充至 76 个并保持 1:1 双向对齐。"""
    from lantai.cli import mcp

    resp = mcp.handle({"jsonrpc": "2.0", "id": 999, "method": "tools/list"})
    assert resp is not None
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}

    # 验证工具总数扩充至 76 个（包含 dossier_create 与 dossier_focus）
    assert len(tools) == 76, f"Expected 76 MCP tools, got {len(tools)}: {names}"
    assert "dossier_create" in names
    assert "dossier_focus" in names

    # 严格 1:1 对齐
    for t_name in mcp.TOOLS:
        assert t_name in mcp.TOOL_HANDLERS, f"Tool {t_name} is advertised but missing handler"
    for h_name in mcp.TOOL_HANDLERS:
        assert h_name in mcp.TOOLS, f"Handler {h_name} exists but is not advertised in TOOLS"


def test_cognitive_context_dossier_fence_seam():
    """Seam 5: CognitiveContext 卷宗切面渲染与樊篱防注入。"""
    ctx = CognitiveContext(task="排查复杂跨系统网络时延")

    # 挂载激活的卷宗切面数据
    ctx.active_dossier = {
        "id": "dos_test_001",
        "title": "网络时延攻坚案",
        "goal": "将跨机房 RPC 时延从 120ms 压降至 20ms 以内",
        "milestones": [
            {
                "id": "ms-1",
                "title": "抓包分析 MTU 大小",
                "status": "done",
                "notes": "已确认分片重组导致延迟抖动",
            },
            {
                "id": "ms-2",
                "title": "开启巨型帧",
                "status": "in_progress",
                "notes": "需与机房网工协同",
            },
        ],
        "focused_memories": [
            {"id": "mem-101", "content": "巨型帧 MTU=9000 可消除跨机房分包开销", "confidence": 0.95}
        ],
    }

    prompt = ctx.to_prompt()

    # 1. 标题与段落呈现
    assert "## Active Dossier & Strategic Goal" in prompt
    assert "网络时延攻坚案" in prompt

    # 2. 樊篱围栏保护：所有卷宗数据必须被 <memory_data> 包裹，防止 Prompt 注入
    assert "<memory_data" in prompt
    assert "</memory_data>" in prompt
    assert "将跨机房 RPC 时延从 120ms 压降至 20ms 以内" in prompt
    assert "已确认分片重组导致延迟抖动" in prompt
    assert "巨型帧 MTU=9000 可消除跨机房分包开销" in prompt
