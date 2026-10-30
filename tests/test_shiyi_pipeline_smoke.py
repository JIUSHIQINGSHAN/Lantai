"""拾遗（Shiyi）主流水线零召回自适应补救闭环冒烟测试 (Ticket 01)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造测试数据写入 SQLite，直调混合检索核心 hybrid_search 与 ShiyiEngine，不 mock 核心存储与补救算法；
- 覆盖 4 个独立接缝：
  1. Seam 1: hybrid_search 零召回场景下自适应唤醒拾遗补救（返回补救结果且带有 is_remedied 标记）；
  2. Seam 2: 补救候选结构投影（to_search_result）与 trace_steps 记录完整对齐；
  3. Seam 3: 当显式传入 enable_shiyi=False 时，零召回保持兼容回退（返回空列表）；
  4. Seam 4: 租户与属主行级 ACL 隔离（跨租户记忆在主流水线补救中坚决不泄露，宁漏不越权）及樊篱防注入包裹。
"""

from __future__ import annotations

import pytest

from lantai.cognition.shiyi import RemedyCandidate, ShiyiEngine
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage import db


def _seed_memory(
    session,
    *,
    id: str,
    content: str,
    user_id: str = "user-alice",
    tenant_id: str | None = None,
    domain: str = "user",
    lane: str = "general",
    confidence: float = 0.9,
    status: str = "active",
) -> MemoryItem:
    t = utcnow()
    mem = MemoryItem(
        id=id,
        content=content,
        user_id=user_id,
        tenant_id=tenant_id,
        domain=domain,
        lane=lane,
        confidence=confidence,
        status=status,
        created_at=t,
        updated_at=t,
    )
    session.add(mem)
    return mem


class TestShiyiPipelineSmoke:
    """拾遗主流水线零召回自适应补救闭环冒烟测试集。"""

    def test_seam1_main_pipeline_zero_recall_auto_remedy(self):
        """Seam 1: hybrid_search 在零召回时自动唤醒拾遗补救。"""
        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-k8s-pod-remedy-001",
                content="Kubernetes生产集群中核心微服务容器重启策略统一配置为Always",
                user_id="user-alice",
                tenant_id="tenant-alpha",
            )
            session.commit()

        principal = Principal(user_id="user-alice", tenant_id="tenant-alpha")

        # 模拟长尾/缩略查询（向量和 BM25 均易产生未对齐/零召回）
        query = "K8S微服务重启总是生效"
        results = hybrid_search(
            query=query,
            top_k=3,
            principal=principal,
            enable_shiyi=True,
        )

        assert isinstance(results, list)
        assert len(results) >= 1
        item = results[0]
        assert item["is_remedied"] is True
        assert item["remedy_tier"] in ("fuzzy_tokens", "subsequence", "domain_relax")
        assert item["memory"]["id"] == "mem-k8s-pod-remedy-001"
        assert "<memory_data" in item["fenced_content"]

    def test_seam2_trace_and_search_result_projection(self):
        """Seam 2: 补救候选结构投影（to_search_result）与 trace_steps 记录完整对齐。"""
        # 测试 RemedyCandidate 直接投影契约
        cand = RemedyCandidate(
            id="cand-001",
            content="自动化测试套件持续集成构建任务",
            remedy_tier="fuzzy_tokens",
            confidence=0.88,
            remedy_reason="命中关键词测试/持续集成",
            domain="agent",
            lane="rule",
            user_id="user-bob",
            tenant_id="tenant-beta",
            created_at=utcnow(),
        )
        res_dict = cand.to_search_result(layer="semantic")
        assert res_dict["score"] == 0.88
        assert res_dict["layer"] == "semantic"
        assert res_dict["is_remedied"] is True
        assert res_dict["remedy_tier"] == "fuzzy_tokens"
        assert res_dict["remedy_reason"] == "命中关键词测试/持续集成"
        assert "<memory_data" in res_dict["fenced_content"]

        # 验证带 trace=True 时的主流水线审计步
        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-trace-shiyi-001",
                content="PostgreSQL数据库主从复制读写分离配置文档",
                user_id="user-bob",
                tenant_id="tenant-beta",
            )
            session.commit()

        principal = Principal(user_id="user-bob", tenant_id="tenant-beta")
        res, trace_steps = hybrid_search(
            query="PG库从库读写分离说明",
            top_k=2,
            trace=True,
            principal=principal,
            enable_shiyi=True,
        )
        assert len(res) >= 1
        step_names = [st["step"] for st in trace_steps]
        assert any("shiyi_remedy" in s for s in step_names)

    def test_seam3_enable_shiyi_toggle_backward_compatibility(self):
        """Seam 3: 当显式传入 enable_shiyi=False 时，零召回保持兼容回退（返回空列表）。"""
        principal = Principal(user_id="user-nobody", tenant_id="tenant-none")
        query = "完全没有任何可能匹配的火星文随机词汇xyz987qwe"

        # 开启补救，即便无匹配也安全返回空
        res_on = hybrid_search(query=query, top_k=3, principal=principal, enable_shiyi=True)
        assert res_on == []

        # 关闭补救，严格保持旧有空返回行为
        res_off = hybrid_search(query=query, top_k=3, principal=principal, enable_shiyi=False)
        assert res_off == []

    def test_seam4_tenant_acl_isolation_and_fencing(self):
        """Seam 4: 跨租户记忆在主流水线补救中坚决阻断，宁漏不越权。"""
        with db.get_session() as session:
            _seed_memory(
                session,
                id="mem-secret-tenant-corp",
                content="企业内部机密财务预算审计规则与报销通道",
                user_id="user-corp-ceo",
                tenant_id="tenant-corp-secure",
            )
            session.commit()

        # 尝试使用外部租户的身份进行补救召回
        attacker_principal = Principal(user_id="user-intruder", tenant_id="tenant-external")
        res = hybrid_search(
            query="内部机密财务预算审计",
            top_k=5,
            principal=attacker_principal,
            enable_shiyi=True,
        )
        # 绝不应泄露其他租户的记忆
        leaked_ids = [r["memory"]["id"] for r in res]
        assert "mem-secret-tenant-corp" not in leaked_ids
