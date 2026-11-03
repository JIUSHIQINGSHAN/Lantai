"""校雠（Jiaochou）服务层、REST 端点、MCP 工具与认知上下文切面冒烟测试 (Ticket 02)。

测试纪律（AGENTS.md & Matt Pocock TDD）：
- 真实构造输入，直调被测服务层、REST API、MCP 工具及认知上下文，不 mock 核心计算与存储过程；
- 覆盖 4 个公共接缝：
  1. 服务层 collate_memories_service / scan_redundancy_service 纯净调用验证；
  2. REST 路由端点 POST /jiaochou/collate 与 GET /jiaochou/scan 往返验证；
  3. MCP 协议工具 jiaochou_collate 往返解析与 83 工具池 1:1 双向对齐守卫；
  4. CognitiveContext 认知上下文 jiaochou_digest 补救切面樊篱包裹验证。
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cli.mcp import TOOL_HANDLERS, TOOLS, handle
from lantai.cognition.context import CognitiveContext
from lantai.cognition.jiaochou import JiaochouEngine
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.services.jiaochou_service import (
    collate_memories_service,
    scan_redundancy_service,
)
from lantai.storage import db

# seam1 的证据作用域：本用例自带一个别的文件写不进去的租户。
#
# 为什么不能留在 `default`：`scan_redundancy` 的 `limit` 是「先扫到的 N 对」，不是
# 「最相似的 N 对」——`lantai/cognition/jiaochou.py:200-203` 凑够 limit 就 break，
# `:205` 才排序；而全量测试共用一个会话级 SQLite（`tests/conftest.py::_isolate_data_dir`）。
# 撒进公共 `default` 租户后，「前面跑了哪些文件、撒进去多少对」决定了本用例的对进不进
# 得了窗口：逆序全量与乱序全量（seed=20261103）各判红一条，正序绿只是运气（票 05 续20）。
#
# 为什么换租户是**隔离**而不是**绕过**：读侧 ACL（`jiaochou.py::_check_access`）只放行
# 同属主行 + `user_id IS NULL` 行 + `swarm_shared` 行。实测逆序全量跑到本用例时共享库为
# `active=46 / owner_null=0 / swarm_shared=0`——外来租户的可见集为空，竞争对手是被 ACL
# 挡在门外，不是被断言视而不见；截断机制另有 `test_jiaochou_scan_limit_keeps_first_found_*`
# 钉住，防止有人把「窗口里恰好有我」当成正确性证据。
SEAM1_USER = f"jc-seam1-{uuid.uuid4().hex[:8]}"


def _mem(owner: str, tag: str, content: str, t) -> MemoryItem:
    return MemoryItem(
        id=f"mem-{tag}-{uuid.uuid4().hex[:8]}",
        content=content,
        confidence=0.9,
        user_id=owner,
        created_at=t,
        updated_at=t,
    )


@contextlib.contextmanager
def _private_store():
    """私有内存库：给「机制锁」一个不受跑测顺序影响的现场（不碰会话级共享库）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _seed_test_pair(user_id: str = "default") -> tuple[MemoryItem, MemoryItem]:
    t = utcnow()
    with db.get_session() as s:
        m1 = MemoryItem(
            id=f"mem-jc-api-1-{t.timestamp()}",
            content="生产集群已开启两级本地与分布式缓存",
            confidence=0.92,
            user_id=user_id,
            created_at=t,
            updated_at=t,
        )
        m2 = MemoryItem(
            id=f"mem-jc-api-2-{t.timestamp()}",
            content="生产环境集群配置了两级本地与分布式缓存架构",
            confidence=0.95,
            user_id=user_id,
            created_at=t,
            updated_at=t,
        )
        s.add(m1)
        s.add(m2)
        s.commit()
        s.refresh(m1)
        s.refresh(m2)
        return m1, m2


class TestJiaochouApiSmoke:
    """Jiaochou Ticket 02 冒烟测试集。"""

    def test_seam1_jiaochou_service(self):
        """Seam 1: 服务层纯净调用验证。"""
        m1, m2 = _seed_test_pair(SEAM1_USER)
        p = Principal(user_id=SEAM1_USER)

        # 1. collate_memories_service
        collate_res = collate_memories_service(
            text_a=m1.content,
            text_b=m2.content,
            memory_id_a=m1.id,
            memory_id_b=m2.id,
            principal=p,
        )
        assert collate_res["similarity"] > 0.5
        assert collate_res["action_suggestion"] in ("merge", "update")
        assert len(collate_res["overlap_tokens"]) >= 1

        # 2. scan_redundancy_service
        scan_res = scan_redundancy_service(limit=10, similarity_threshold=0.5, principal=p)
        # 原为 `>= 1`：只证明「窗口里有任何一对」，不证明「那是我的对」，所以它会被
        # 别人的数据挤成假红、也会被别人的数据挤成假绿。收紧成精确可见集＝隔离本身可证。
        assert scan_res["total_pairs"] == 1, (
            f"专用作用域里本该只看得见本用例自己的一对，实测 {scan_res['total_pairs']} 对："
            "有别的文件往这个作用域写进了数据，隔离已破"
        )
        assert any(
            {p["memory_id_a"], p["memory_id_b"]} == {m1.id, m2.id}
            for p in scan_res["redundant_pairs"]
        )

    def test_seam2_rest_endpoints(self):
        """Seam 2: REST 路由 POST /jiaochou/collate 与 GET /jiaochou/scan。"""
        m1, m2 = _seed_test_pair()
        client = TestClient(app)

        # 1. POST /jiaochou/collate
        resp1 = client.post(
            "/jiaochou/collate",
            json={"text_a": m1.content, "text_b": m2.content},
            headers={"X-API-Key": "test"},
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["similarity"] > 0.5
        assert d1["synthesized_proposal"] != ""

        # 2. GET /jiaochou/scan
        resp2 = client.get(
            "/jiaochou/scan",
            params={"limit": 5, "similarity_threshold": 0.5},
            headers={"X-API-Key": "test"},
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["total_pairs"] >= 1

    def test_seam3_mcp_83_tools_parity(self):
        """Seam 3: MCP 协议工具 jiaochou_collate 往返解析与 83 工具池 1:1 双向对齐守卫。"""
        m1, m2 = _seed_test_pair()

        # 83+ 工具总数与 1:1 对齐断言
        assert len(TOOLS) >= 83
        assert len(TOOL_HANDLERS) == len(TOOLS)
        assert "jiaochou_collate" in TOOLS
        assert "jiaochou_collate" in TOOL_HANDLERS
        for t_name in TOOLS:
            assert t_name in TOOL_HANDLERS, (
                f"Tool {t_name} is advertised in TOOLS but missing from TOOL_HANDLERS"
            )

        # tools/list RPC
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = handle(req_list)
        assert resp_list is not None
        tool_names = [t["name"] for t in resp_list["result"]["tools"]]
        assert "jiaochou_collate" in tool_names

        # tools/call RPC - collate mode
        req_call1 = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "jiaochou_collate",
                "arguments": {
                    "text_a": m1.content,
                    "text_b": m2.content,
                    "user_id": "default",
                },
            },
        }
        res1 = handle(req_call1)
        assert res1 is not None and "result" in res1
        data1 = json.loads(res1["result"]["content"][0]["text"])
        assert data1["similarity"] > 0.5

        # tools/call RPC - scan mode
        req_call2 = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "jiaochou_collate",
                "arguments": {
                    "scan": True,
                    "similarity_threshold": 0.5,
                    "user_id": "default",
                },
            },
        }
        res2 = handle(req_call2)
        assert res2 is not None and "result" in res2
        data2 = json.loads(res2["result"]["content"][0]["text"])
        assert "redundant_pairs" in data2

    def test_seam4_cognitive_context_jiaochou_aspect(self):
        """Seam 4: CognitiveContext 认知上下文 jiaochou_digest 校雠切面樊篱包裹验证。"""
        items = [
            {
                "synthesized_proposal": "统一将生产数据库端口设定为 5432",
                "action_suggestion": "merge",
                "similarity": 0.95,
                "divergence_summary": "表述同义合并",
            }
        ]
        ctx = CognitiveContext(task="数据库端口统一配置", jiaochou_digest=items)
        prompt = ctx.to_prompt()

        assert "## Semantic Alignment & Collation (校雠 · 1):" in prompt
        assert "[校雠·C1 (merge) [相似度: 0.95]]" in prompt
        assert "<memory_data" in prompt
        assert "统一将生产数据库端口设定为 5432" in prompt
        assert "表述同义合并" in prompt


def test_jiaochou_scan_limit_keeps_first_found_not_most_similar():
    """机制锁（真库真引擎，不 mock）：`limit` 是「先扫到的 N 对」，不是「最相似的 N 对」。

    根因位置 `lantai/cognition/jiaochou.py:200-203`：凑够 limit 对就 break，`:205` 才按
    相似度排序——排序作用在一个**已经被截断**的集合上，结果只取决于行的插入顺序。而
    seam1 读写的是会话级共享库（`tests/conftest.py::_isolate_data_dir`），插入顺序又取决于
    「前面跑了哪些文件」，于是「窗口里有没有我」变成一个跑测顺序的函数。这条锁用私有库
    把截断机制本身钉住（本票不改校雠的返回契约），防止有人把「正序绿」当成正确性证据。
    """
    t = utcnow()
    crowd = []
    for i in range(12):
        text = f"冗余簇 {i}：同一句完全一样的话"
        crowd.append(_mem("jc-crowd", f"crowd-a{i}", text, t))
        crowd.append(_mem("jc-crowd", f"crowd-b{i}", text, t))
    mine = [
        _mem("jc-crowd", "mine-a", "生产集群已开启两级本地与分布式缓存", t),
        _mem("jc-crowd", "mine-b", "生产环境集群配置了两级本地与分布式缓存架构", t),
    ]
    with _private_store() as session:
        for m in [*crowd, *mine]:  # 竞争对先进库 = 先被扫到
            session.add(m)
        session.commit()
        res = JiaochouEngine(
            session=session, principal=Principal(user_id="jc-crowd")
        ).scan_redundancy(limit=10, similarity_threshold=0.5)
    mine_ids = {mine[0].id, mine[1].id}
    assert res["total_pairs"] == 10, res["total_pairs"]
    assert mine_ids not in [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]], (
        "窗口居然装得下：这条锁没测到截断机制"
    )


def test_jiaochou_seam1_evidence_survives_a_crowded_shared_store():
    """隔离锁：seam1 的证据不许取决于「别的文件先往同一个作用域撒了多少对」。

    实测事故（票 05 续20）：逆序全量与乱序全量（seed=20261103）各判红一条，都是
    `test_seam1_jiaochou_service`——它曾把样本撒进共享 `default` 租户，而扫描凑够 limit
    就 break，竞争对先占满窗口时本用例的对就被挤出去；正序绿只是运气。本锁在私有库里
    重放那个拥挤现场：谁把 `SEAM1_USER` 改回公共租户，这条锁立刻判红。
    """
    t = utcnow()
    crowd = []
    for i in range(12):
        text = f"别的文件先撒进来的冗余对 {i}"
        crowd.append(_mem("default", f"rival-a{i}", text, t))
        crowd.append(_mem("default", f"rival-b{i}", text, t))
    with _private_store() as session:
        for m in crowd:
            session.add(m)
        session.commit()
        m1, m2 = _seed_scoped_pair(session, t)
        res = JiaochouEngine(
            session=session, principal=Principal(user_id=SEAM1_USER)
        ).scan_redundancy(limit=10, similarity_threshold=0.5)
    hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
    assert {m1.id, m2.id} in hits, (
        f"窗口被别人的数据占满，本用例的证据进不来：{len(hits)} 对里没有它"
    )


def _seed_scoped_pair(session: Session, t) -> tuple[MemoryItem, MemoryItem]:
    """按 seam1 当前的作用域播种一对冗余样本（私有库里直插，避免污染会话级共享库）。"""
    pair = [
        _mem(SEAM1_USER, "own-a", "生产集群已开启两级本地与分布式缓存", t),
        _mem(SEAM1_USER, "own-b", "生产环境集群配置了两级本地与分布式缓存架构", t),
    ]
    for m in pair:
        session.add(m)
    session.commit()
    return pair[0], pair[1]
