"""探颐·API、MCP 工具集成与认知上下文求证切面冒烟测试 (v0.38 Ticket 02)

遵循 Matt Pocock TDD 纪律，真实构造 SQLite 数据库与 FastAPI TestClient / MCP 消息分发，
验证对外暴露的公共接缝（Seams）：
1. CognitiveContext 认知上下文 clarification_probes 切面渲染与樊篱包裹；
2. HTTP REST 路由：POST /cognition/tanyi/probe（200 成功 + 租户/属主双重隔离）；
3. MCP 标准协议分发：memory_tanyi_probe 工具调用 + TOOLS/TOOL_HANDLERS 双向不变量。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.api.app import app
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import ConflictEvent, MemoryItem


def _principal(user_id: str, tenant_id: str | None = None) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        role="user",
        allowed_lanes=["fact", "preference", "general"],
    )


def _seed_rows(session: Session) -> None:
    """租户 A / 用户 A：一条未决冲突 + 一条低置信度事实；另造 B 的敏感行。"""
    session.add(
        MemoryItem(
            id="mem-a-conflict",
            content="团队核心常驻办公地点在上海张江",
            confidence=0.9,
            lane="fact",
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
    )
    session.add(
        ConflictEvent(
            id="conf-a-1",
            memory_id="mem-a-conflict",
            incoming_ref="团队近期已搬迁至北京中关村办公",
            rule_name="location_conflict",
            status="open",
        )
    )
    session.add(
        MemoryItem(
            id="mem-a-fuzzy",
            content="用户偏好喝冷萃不加糖咖啡",
            confidence=0.45,
            lane="preference",
            tenant_id="tenant-A",
            user_id="user-A",
            status="active",
        )
    )


def test_cognitive_context_clarification_probes_seam():
    """Seam 1: clarification_probes 切面结构化渲染求证建议并经樊篱数据围栏包裹。"""
    ctx = CognitiveContext(
        task="例行对话回复",
        clarification_probes=[
            {
                "probe_id": "probe_conf_a1",
                "conflict_id": "conf-a-1",
                "question": "关于「团队常驻上海张江」，是否已变更为「北京中关村」？",
            }
        ],
    )
    prompt = ctx.to_prompt()
    assert "## Clarification Probes" in prompt
    assert "<memory_data" in prompt  # 樊篱围栏：记忆正文是数据不是指令
    assert "北京中关村" in prompt


@pytest.fixture()
def api_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with session_factory() as s:
        _seed_rows(s)
        s.add(
            MemoryItem(
                id="mem-b-secret",
                content="租户B的薪酬调整预案",
                confidence=0.4,
                tenant_id="tenant-B",
                user_id="user-B",
                status="active",
            )
        )
        s.commit()

    import lantai.storage.db as db_module

    original_get_session = db_module.get_session
    db_module.get_session = session_factory
    yield session_factory
    db_module.get_session = original_get_session


def test_http_api_tanyi_probe_route_seam(api_env):
    """Seam 2: POST /cognition/tanyi/probe 返回探针与追问建议，且跨租户零泄漏。"""
    client = TestClient(app)
    user_a = _principal(user_id="user-A", tenant_id="tenant-A")
    app.dependency_overrides[get_current_user] = lambda: user_a
    try:
        resp = client.post("/cognition/tanyi/probe", json={"limit": 10})
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "probes" in data and "clarification_prompts" in data
        memory_ids = {p["memory_id"] for p in data["probes"]}
        assert "mem-a-conflict" in memory_ids
        assert "mem-a-fuzzy" in memory_ids
        assert "mem-b-secret" not in memory_ids  # 跨租户隔离

        # 全部探针归属租户 A
        assert all(p["tenant_id"] == "tenant-A" for p in data["probes"])

        # 追问建议与探针一一对应，冲突探针呈现新旧主张
        assert len(data["clarification_prompts"]) == len(data["probes"])
        conflict_probe = next(p for p in data["probes"] if p["conflict_id"] == "conf-a-1")
        idx = data["probes"].index(conflict_probe)
        assert "上海张江" in data["clarification_prompts"][idx]
        assert "北京中关村" in data["clarification_prompts"][idx]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_http_api_tanyi_probe_user_scope_within_tenant(api_env):
    """Seam 2b: 同租户跨属主零泄漏——非 admin 严格只见自己名下的记忆。"""
    session_factory = api_env
    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem-a2-secret",
                content="同事 A2 的私人日程",
                confidence=0.3,
                tenant_id="tenant-A",
                user_id="user-A2",
                status="active",
            )
        )
        s.commit()

    client = TestClient(app)
    user_a = _principal(user_id="user-A", tenant_id="tenant-A")
    app.dependency_overrides[get_current_user] = lambda: user_a
    try:
        resp = client.post("/cognition/tanyi/probe", json={"limit": 50})
        assert resp.status_code == 200, resp.text
        memory_ids = {p["memory_id"] for p in resp.json()["probes"]}
        assert "mem-a2-secret" not in memory_ids
        assert "mem-a-fuzzy" in memory_ids
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_tanyi_probe_tool_seam(param_env):
    """Seam 3: MCP memory_tanyi_probe 工具标准分发，参数 user_id 收窄归属。"""
    from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle

    # 双向不变量：广告 ⇔ handler 一一对应
    assert set(TOOLS) == set(TOOL_HANDLERS)

    session_factory, _engine = param_env
    with session_factory() as s:
        _seed_rows(s)
        s.add(
            MemoryItem(
                id="mem-b-secret",
                content="租户B的薪酬调整预案",
                confidence=0.4,
                tenant_id="tenant-B",
                user_id="user-B",
                status="active",
            )
        )
        s.commit()

    req = {
        "jsonrpc": "2.0",
        "id": "mcp-tanyi-1",
        "method": "tools/call",
        "params": {
            "name": "memory_tanyi_probe",
            "arguments": {"user_id": "user-A", "limit": 10},
        },
    }
    resp = handle(req)
    assert resp is not None and "result" in resp, f"应可派发，实得 {resp}"
    text = resp["result"]["content"][0]["text"]
    assert "mem-a-conflict" in text
    assert "mem-a-fuzzy" in text
    assert "mem-b-secret" not in text  # 跨属主/跨租户零泄漏
