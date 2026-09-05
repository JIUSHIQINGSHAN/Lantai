"""器识（ADR-0029）：Persona 人格基座（L/G/E）测试。

验证：
1. PersonaProfile 数据库模型持久化与迁移（v15）；
2. persona_service: 创建、列表、激活切换、默认人格初始化、上下文生成；
3. 纯函数边界防护（宁 miss 不脏写：超长截断/空内容防御）；
4. REST 路由 (/persona/active, /persona/list, /persona, /persona/{id}/activate) 与 MCP 工具；
5. 会话首轮基座注入联动（与底本 Checkpoint 协同）。
"""
from fastapi.testclient import TestClient

from api_server import app
from lantai.services.persona_service import (
    activate_persona,
    ensure_default_persona,
    format_persona_context,
    get_active_persona,
    set_persona,
)


class TestPersonaModelAndService:
    """测试 PersonaProfile 表与 persona_service 核心逻辑（真实 SQLite 不 mock）。"""

    def test_default_persona_initialization(self, param_env):
        """测试初始默认人格创建。"""
        session_factory, _ = param_env
        with session_factory() as s:
            p = ensure_default_persona(s)
            assert p is not None
            assert p.is_active is True
            assert "兰台" in p.name
            assert len(p.linguistic_style) > 0
            assert len(p.guidelines) > 0
            assert len(p.epistemic_facts) > 0

    def test_set_and_activate_persona(self, param_env):
        """测试新建并激活人格基座。"""
        session_factory, _ = param_env
        with session_factory() as s:
            ensure_default_persona(s)
            
            # 创建新的人格
            p2 = set_persona(
                name="策论参谋",
                linguistic_style="言简意赅，直击要害",
                guidelines="务实落地，不务虚词",
                epistemic_facts="专注系统工程治理与架构重构",
                is_active=True,
                session=s,
            )
            assert p2.name == "策论参谋"
            assert p2.is_active is True

            # 确认当前 active 唯有一条
            active = get_active_persona(s)
            assert active.id == p2.id
            assert active.name == "策论参谋"

            # 切换回默认人格
            p_default = activate_persona("兰台执笔", session=s)
            assert p_default is not None
            assert p_default.is_active is True
            
            # 重新获取 active
            active_now = get_active_persona(s)
            assert active_now.name == "兰台执笔"

    def test_format_persona_context(self, param_env):
        """测试 L/G/E 三层人格上下文格式化输出。"""
        session_factory, _ = param_env
        with session_factory() as s:
            p = set_persona(
                name="测试人格",
                linguistic_style="古诗词点缀",
                guidelines="宁 miss 不脏写",
                epistemic_facts="大哥的电脑配置为华硕天选三",
                is_active=True,
                session=s,
            )
            ctx = format_persona_context(p)
            assert "器识·人格基座" in ctx
            assert "古诗词点缀" in ctx
            assert "宁 miss 不脏写" in ctx
            assert "华硕天选三" in ctx


class TestPersonaEndpoints:
    """测试 REST /persona 路由端点。"""

    def test_rest_persona_crud_and_activate(self, param_env):
        session_factory, _ = param_env
        client = TestClient(app)

        # 获取默认 active persona
        r1 = client.get("/persona/active")
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["name"] == "兰台执笔"

        # 创建并激活新 persona
        payload = {
            "name": "天工架构师",
            "linguistic_style": "技术精准，克制精炼",
            "guidelines": "TDD 先导，测试不 mock",
            "epistemic_facts": "掌握 Python 3.12 与 SQLite 全文检索",
            "is_active": True,
        }
        r2 = client.post("/persona", json=payload)
        assert r2.status_code == 200
        p2 = r2.json()
        assert p2["name"] == "天工架构师"
        assert p2["is_active"] is True

        # 列出所有人格
        r3 = client.get("/persona/list")
        assert r3.status_code == 200
        names = [item["name"] for item in r3.json()]
        assert "天工架构师" in names
        assert "兰台执笔" in names

        # 重新激活兰台执笔
        r4 = client.post(f"/persona/{data1['id']}/activate")
        assert r4.status_code == 200
        assert r4.json()["is_active"] is True

        # 再次查看 active
        r5 = client.get("/persona/active")
        assert r5.json()["name"] == "兰台执笔"


class TestPersonaMCPAndIntegrations:
    """测试 MCP 工具与系统级整合。"""

    def test_mcp_persona_tools(self, param_env):
        """测试 MCP 工具：persona_get 与 persona_set。"""
        from scripts.mcp_server import handle_persona_get, handle_persona_set

        # 测试获取当前激活
        res1 = handle_persona_get({})
        assert res1["persona"] is not None
        assert "器识·人格基座" in res1["context"]

        # 测试 MCP 创建新 persona
        res2 = handle_persona_set({
            "name": "通儒顾问",
            "linguistic_style": "引经据典",
            "guidelines": "恪守典章",
            "epistemic_facts": "通晓兰台文脉",
            "is_active": True,
        })
        assert res2["name"] == "通儒顾问"
        assert res2["is_active"] is True

        # 再次获取
        res3 = handle_persona_get({})
        assert res3["persona"]["name"] == "通儒顾问"
        assert "通晓兰台文脉" in res3["context"]

    def test_checkpoint_injection_with_persona(self, param_env):
        """测试底本上下文与器识人格基座联动注入。"""
        from lantai.services.checkpoint_service import (
            inject_checkpoint_context,
            write_session_checkpoint,
        )

        # 写入一条底本快照
        write_session_checkpoint("session_test_p", {
            "cp_active_intent": "测试器识注入",
            "cp_next_action": "执行全量回归",
        })

        # 注入带 persona 的上下文
        ctx = inject_checkpoint_context(include_persona=True)
        assert "器识·人格基座" in ctx
        assert "[Checkpoint · 上次会话]" in ctx
        assert "测试器识注入" in ctx
        assert "执行全量回归" in ctx

