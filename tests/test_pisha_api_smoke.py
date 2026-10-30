"""测试披沙（Ticket 02）集成服务层、REST 端点、MCP 90 工具协议与认知上下文切面。

遵循 AGENTS.md 测试纪律：真实构造输入，不 mock 核心计算。
覆盖 4 个独立接缝：
- 接缝 1：服务层统一入口（pisha_refine_service / batch_refine_candidates）
- 接缝 2：REST 路由端点（POST /pisha/refine 与 POST /pisha/batch）
- 接缝 3：MCP 90 工具协议往返与 1:1 双向对齐守卫
- 接缝 4：认知上下文披沙切面格式渲染（CognitiveContext pisha_digest）
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryCandidate
from lantai.services.refine_service import batch_refine_candidates, pisha_refine_service
from lantai.storage import db


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(test_db: Session):
    app.dependency_overrides[get_current_user] = lambda: Principal(
        user_id="test_user", tenant_id="tenant_01", role="admin"
    )
    # 覆盖 db.get_session 为测试 db
    old_get_session = db.get_session
    db.get_session = lambda: test_db
    yield TestClient(app)
    db.get_session = old_get_session
    app.dependency_overrides.clear()


class TestPishaApiSmoke:
    def test_seam1_service_layer_pisha(self, test_db: Session):
        """接缝 1：服务层统一入口调用。"""
        # 1. 纯文本提纯
        res_text = pisha_refine_service(
            text="其实他说了那个技术很关键呢",
            context="技术选型讨论",
            entity_hints={"他": "张总工", "那个技术": "Rust"},
        )
        assert res_text["is_valid"] is True
        assert "张总工" in res_text["refined_text"]
        assert "Rust" in res_text["refined_text"]

        # 2. 候选记录提纯
        cand = MemoryCandidate(
            id="cand_srv_01",
            document_id="doc_srv_01",
            summary="好的，他说那个方案要继续推行",
            extractor_confidence=0.35,
            status="pending_review",
            user_id="test_user",
            tenant_id="tenant_01",
        )
        test_db.add(cand)
        test_db.commit()

        res_cand = pisha_refine_service(
            candidate_id="cand_srv_01",
            session=test_db,
            principal=Principal(user_id="test_user", tenant_id="tenant_01"),
        )
        assert res_cand["id"] == "cand_srv_01"
        assert res_cand["provenance"]["pisha_refined"] is True

        # 3. 批量扫描
        batch_out = batch_refine_candidates(
            min_conf=0.2,
            max_conf=0.6,
            session=test_db,
            principal=Principal(user_id="test_user", tenant_id="tenant_01"),
        )
        assert "total_scanned" in batch_out

    def test_seam2_rest_routes(self, client: TestClient, test_db: Session):
        """接缝 2：REST 路由端点调用验证。"""
        # 1. POST /pisha/refine (文本模式)
        resp_text = client.post(
            "/pisha/refine",
            json={
                "text": "其实那个配置应该设为 60 秒",
                "entity_hints": {"那个配置": "timeout_seconds"},
            },
        )
        assert resp_text.status_code == 200
        data_text = resp_text.json()
        assert "timeout_seconds" in data_text["refined_text"]

        # 2. POST /pisha/refine (记录模式)
        cand = MemoryCandidate(
            id="cand_rest_01",
            document_id="doc_rest_01",
            summary="嗯，他要求明天上线",
            extractor_confidence=0.4,
            status="pending_review",
            user_id="test_user",
            tenant_id="tenant_01",
        )
        test_db.add(cand)
        test_db.commit()

        resp_cand = client.post(
            "/pisha/refine",
            json={"candidate_id": "cand_rest_01"},
        )
        assert resp_cand.status_code == 200
        data_cand = resp_cand.json()
        assert data_cand["id"] == "cand_rest_01"

        # 3. POST /pisha/batch
        resp_batch = client.post(
            "/pisha/batch",
            json={"min_conf": 0.15, "max_conf": 0.6, "limit": 10},
        )
        assert resp_batch.status_code == 200
        assert "total_scanned" in resp_batch.json()

    def test_seam3_mcp_90_tools_parity(self):
        """接缝 3：MCP 90 工具协议往返与 1:1 双向对齐守卫。"""
        # 工具总数必须恰好为 90 个里程碑
        assert len(TOOLS) == 90
        assert len(TOOL_HANDLERS) == 90
        assert set(TOOLS.keys()) == set(TOOL_HANDLERS.keys())
        assert "pisha_refine" in TOOLS

        # 协议调用验证
        req_msg = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {
                "name": "pisha_refine",
                "arguments": {
                    "text": "坦白讲，他负责了这个模块呢",
                    "entity_hints": {"他": "王工程师", "这个模块": "AuthEngine"},
                },
            },
        }
        res = handle(req_msg)
        assert res is not None
        assert "result" in res
        content = res["result"]["content"][0]["text"]
        assert "王工程师" in content
        assert "AuthEngine" in content

    def test_seam4_cognitive_context_pisha_digest(self):
        """接缝 4：认知上下文披沙提纯切面格式化渲染。"""
        ctx = CognitiveContext(
            task="长程架构决策",
            pisha_digest=[
                {
                    "refined_text": "系统已强制全链路应用数据围栏防护。",
                    "confidence": 0.88,
                    "reason": "消除了口语废话并明确了主体",
                    "pronoun_resolutions": {"该机制": "樊篱防护"},
                }
            ],
        )
        prompt = ctx.to_prompt()

        assert "## Refined Candidates & Denoised Memories (披沙 · 1):" in prompt
        assert "[披沙·P1 [置信度: 0.88] [消解: {'该机制': '樊篱防护'}]]" in prompt
        assert "系统已强制全链路应用数据围栏防护。" in prompt
        assert "<memory_data" in prompt
