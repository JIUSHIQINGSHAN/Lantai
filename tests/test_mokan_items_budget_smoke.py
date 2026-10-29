"""磨勘（v0.29 · MCMA 建议项 6）：4~5 条知识注入条目硬顶与信息密度控制冒烟测试。

依据 MCMA 论文（arXiv:2601.07470 · docs/research/papers/notes/07-mcma-reading.md §8 建议项 6）：
1. 知识注入数量上限：4~5 条知识最优，过多条目会分散模型注意力并引发负迁移；
2. 完璧原子事实整块保整：一个包含多个投影实体的 FactAtomic 块，严格视作 1 个知识条目；
3. 信息密度优先重排：结合 score 与认知层级（H2 规则/结晶 > H1 事实/经验 > H0 原始观察），
   优先保留高信息密度与高价值知识块。
"""

import pytest

from lantai.core.settings import settings
from lantai.models.tables import CognitiveRole, FactAtomic, MemoryItem
from lantai.retrieval.hierarchical import (
    count_injected_knowledge_units,
    rerank_by_information_density,
)


def test_settings_max_inject_items_default():
    """Seam 1 验证：设置项 SHELL_HOOK_MAX_INJECT_ITEMS 存在且默认为 5。"""
    assert hasattr(settings, "SHELL_HOOK_MAX_INJECT_ITEMS")
    assert settings.SHELL_HOOK_MAX_INJECT_ITEMS == 5


def test_count_injected_knowledge_units_atomic_fact_preservation():
    """Seam 2 验证：完璧原子事实块（即便含 4 个实体）严格计为 1 项知识单元。"""
    # 1. 普通记忆项
    normal_mem = {"score": 0.85, "memory": {"id": "mem_1", "role": "rule"}, "document": "规则 1"}
    assert count_injected_knowledge_units(normal_mem) == 1

    # 2. 挂载了多实体原子事实的项（整块事实保持为 1 项单元）
    wanbi_mem = {
        "score": 0.90,
        "memory": {"id": "mem_2", "role": "experience"},
        "document": "张三、李四和王五在 2025 年共同创办了兰图科技",
        "atomic_facts": [
            {
                "id": "fact_1",
                "statement": "张三、李四、王五于 2025 年共同创办了兰图科技",
                "predicate": "cofound",
                "entities": [
                    {"name": "张三", "role": "subject"},
                    {"name": "李四", "role": "subject"},
                    {"name": "王五", "role": "subject"},
                    {"name": "兰图科技", "role": "object"},
                ],
            }
        ],
    }
    # 无论是检查条目本身还是检查包含的事实，条目块整体计为 1 项
    assert count_injected_knowledge_units(wanbi_mem) == 1


def test_rerank_by_information_density_top5_cap_and_priority():
    """Seam 3 验证：输入 10 条候选时，严格截断至 <= 5 项，且高密度 H2 规则与高分事实优先保留。"""
    candidates = [
        # H0 原始会话碎片（低密度）
        {"id": "h0_1", "score": 0.88, "layer": "h0", "document": "今天天气不错，吃了午饭"},
        {"id": "h0_2", "score": 0.85, "layer": "h0", "document": "好的，收到"},
        {"id": "h0_3", "score": 0.82, "layer": "h0", "document": "明天见"},
        # H1 具体业务经验与事实
        {"id": "h1_1", "score": 0.80, "layer": "h1", "document": "项目上线时间定在 11 月 15 日"},
        {"id": "h1_2", "score": 0.78, "layer": "h1", "document": "服务器部署在华东一区"},
        # H1 完璧原子事实（高置信多实体事实）
        {
            "id": "h1_wanbi",
            "score": 0.84,
            "layer": "h1",
            "document": "张三向李四移交了订单模块负责人权限",
            "atomic_facts": [
                {"id": "fact_wanbi", "statement": "张三向李四移交了订单模块负责人权限"}
            ],
        },
        # H2 核心行为准则与高阶规则（高信息密度）
        {
            "id": "h2_rule1",
            "score": 0.81,
            "layer": "h2",
            "document": "核心函数必须有真实不 mock 冒烟测试",
        },
        {
            "id": "h2_rule2",
            "score": 0.79,
            "layer": "h2",
            "document": "新概念必须先登记 CONTEXT 词汇表",
        },
        {
            "id": "h2_rule3",
            "score": 0.76,
            "layer": "h2",
            "document": "校验失败不静默丢弃宁 miss 不脏写",
        },
        # 低分噪音
        {"id": "noise", "score": 0.52, "layer": "h0", "document": "哈哈哈哈"},
    ]

    reranked = rerank_by_information_density(candidates, max_items=5)

    # 1. 严格硬顶：输出总条目数恒 <= 5
    assert len(reranked) == 5

    # 2. 高密度优先：H2 高阶规则和含完璧原子事实的项应当成功晋级，而 H0 闲聊流水被挤出
    reranked_ids = [r["id"] for r in reranked]
    assert "h2_rule1" in reranked_ids
    assert "h2_rule2" in reranked_ids
    assert "h1_wanbi" in reranked_ids
    assert "h0_2" not in reranked_ids
    assert "h0_3" not in reranked_ids
    assert "noise" not in reranked_ids


def test_shell_hook_max_items_guard_smoke(monkeypatch):
    """Seam 4 冒烟测试：模拟 shell_hook.build_context 处理超量召回（例如 10 条候选），
    验证经由信息密度重排与硬顶闸门后，最终注入 evidence 与记忆条目严格 <= SHELL_HOOK_MAX_INJECT_ITEMS (5)。
    且挂载原子事实条目保持保整。
    """
    import importlib.util
    import os
    from unittest.mock import patch

    hook_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
    )
    spec = importlib.util.spec_from_file_location("shell_hook", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 构造 8 条模拟检索结果（含普通记忆项与完璧原子事实项）
    mock_results = []
    # 3 条高阶规则
    for i in range(1, 4):
        mid = f"mem_rule_{i}"
        mock_results.append(
            {
                "score": 0.80 + i * 0.01,
                "layer": "h2",
                "memory": {
                    "id": mid,
                    "content": f"系统规则准则第 {i} 条",
                    "role": "rule",
                    "lane": "rule",
                },
            }
        )
    # 1 条完璧原子事实项
    mock_results.append(
        {
            "score": 0.82,
            "layer": "h1",
            "memory": {
                "id": "mem_wanbi",
                "content": "张三向李四和王五交代了项目交接事宜",
                "role": "experience",
                "lane": "experience",
            },
            "atomic_facts": [
                {
                    "id": "fact_wanbi",
                    "statement": "张三向李四、王五完成了项目交接",
                    "predicate": "handover",
                    "entities": [{"name": "张三"}, {"name": "李四"}, {"name": "王五"}],
                }
            ],
        }
    )
    # 4 条 H0 原始观察流水
    for i in range(1, 5):
        mid = f"mem_h0_{i}"
        mock_results.append(
            {
                "score": 0.75 + i * 0.01,
                "layer": "h0",
                "memory": {
                    "id": mid,
                    "content": f"昨天闲聊流水记录 {i}",
                    "role": "observation",
                    "lane": "chat",
                },
            }
        )

    # 模拟 hybrid_search 返回 8 条结果
    monkeypatch.setattr(mod, "hybrid_search", lambda query, top_k=5: mock_results)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_INJECT_ITEMS", 5)

    res = mod.build_context("记得项目开发部署的系统架构规范原则与交接事实")

    assert "evidence" in res
    assert "context" in res
    evidence = res["evidence"]

    # 1. 验证硬顶生效：注入的条目数严格 <= 5（MCMA 建议项 6 铁律）
    assert len(evidence) <= 5
    evidence_ids = [e["id"] for e in evidence]

    # 2. 验证高密度项优先：规则和完璧原子事实保留，低价值流水被淘汰
    assert "mem_rule_3" in evidence_ids
    assert "mem_wanbi" in evidence_ids
    assert "mem_h0_1" not in evidence_ids


def test_shell_hook_real_db_wanbi_items_budget_smoke(monkeypatch):
    """Seam 5 真实 SQLite + FTS 端到端不 mock 冒烟：
    真实写入 7 条混合数据（规则 + 完璧原子事实 + 普通事实 + 闲聊流水），
    直调真实的 build_context 驱动检索与注入流水线，
    验证无论底层召回多少候选，最终注入的 context/evidence 知识条目总数严格 <= 5。
    """
    import importlib.util
    import os
    from unittest.mock import Mock, patch

    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.storage.db as db_module
    from lantai.core.ids import new_id
    from lantai.models.tables import FactEntity
    from lantai.retrieval import hybrid
    from lantai.storage.fts import init_fts, sync_fts

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(hybrid.db, "get_session", session_factory)

    # 写入 7 条记忆：2 条规则(H2) + 1 条完璧事实(H1) + 2 条普通事实(H1) + 2 条闲聊(H0)
    rule_ids = [new_id("mem") for _ in range(2)]
    fact_wanbi_mid = new_id("mem")
    fid = new_id("fact")
    fact_normal_ids = [new_id("mem") for _ in range(2)]
    obs_ids = [new_id("mem") for _ in range(2)]

    with session_factory() as s:
        # H2 规则
        s.add(
            MemoryItem(
                id=rule_ids[0],
                content="Python 模块开发铁律：核心函数必须具备真实不 mock 冒烟测试",
                role="rule",
                lane="rule",
                status="active",
                importance=0.9,
            )
        )
        s.add(
            MemoryItem(
                id=rule_ids[1],
                content="知识注入原则：上下文注入知识条目硬顶不超过 5 条",
                role="rule",
                lane="rule",
                status="active",
                importance=0.88,
            )
        )
        # H1 完璧事实
        s.add(
            MemoryItem(
                id=fact_wanbi_mid,
                content="张三、李四和王五在 2025 年共同创办了兰图科技",
                role="experience",
                lane="experience",
                status="active",
                importance=0.85,
            )
        )
        s.add(
            FactAtomic(
                id=fid,
                statement="张三、李四、王五于 2025 年共同创办了兰图科技",
                predicate="cofound",
                object="兰图科技",
                confidence=0.95,
                source_memory_id=fact_wanbi_mid,
            )
        )
        s.add_all(
            [
                FactEntity(fact_id=fid, entity_name="张三", role="subject"),
                FactEntity(fact_id=fid, entity_name="李四", role="subject"),
                FactEntity(fact_id=fid, entity_name="王五", role="subject"),
                FactEntity(fact_id=fid, entity_name="兰图科技", role="object"),
            ]
        )
        # H1 普通事实
        s.add(
            MemoryItem(
                id=fact_normal_ids[0],
                content="兰图科技部署了华东一区的高可用服务器集群",
                role="experience",
                lane="fact",
                status="active",
                importance=0.7,
            )
        )
        s.add(
            MemoryItem(
                id=fact_normal_ids[1],
                content="团队每周五下午三点准时同步项目开发进展",
                role="experience",
                lane="fact",
                status="active",
                importance=0.65,
            )
        )
        # H0 闲聊流水
        s.add(
            MemoryItem(
                id=obs_ids[0],
                content="今天天气晴朗，大家一起吃了外卖",
                role="observation",
                lane="chat",
                status="active",
                importance=0.3,
            )
        )
        s.add(
            MemoryItem(
                id=obs_ids[1],
                content="明天可能要下雨记得带伞",
                role="observation",
                lane="chat",
                status="active",
                importance=0.2,
            )
        )
        s.commit()

        # 全部同步至 FTS
        for mid in rule_ids + [fact_wanbi_mid] + fact_normal_ids + obs_ids:
            item = s.get(MemoryItem, mid)
            if item:
                sync_fts(s, item.id, item.content)
        s.commit()

    hook_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
    )
    spec = importlib.util.spec_from_file_location("shell_hook", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 注入端运行（不 mock 检索核心计算逻辑，仅隔离外部向量网络）
    with (
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "candidate_n": 10},
        ),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("scripts.shell_hook.embed", return_value=[[0.1] * 8]),
        patch(
            "lantai.retrieval.hybrid.get_vector_store",
            return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
        ),
        patch(
            "scripts.shell_hook.get_vector_store",
            return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
        ),
    ):
        monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_INJECT_ITEMS", 5)
        # 调取带触发词的 query，直通 build_context
        ctx_res = mod.build_context("记得张三创办兰图科技的事实与部署核心原则是什么")

    assert "evidence" in ctx_res
    evidence = ctx_res["evidence"]
    # 严格硬顶限制 <= 5
    assert len(evidence) <= 5
    # 完美保整：完璧原子事实被召回并在注入文本中承载完整事实
    ev_ids = [e["id"] for e in evidence]
    assert fact_wanbi_mid in ev_ids
    # 检查 context 内容中包含完璧事实标注
    assert "张三、李四、王五于 2025 年共同创办了兰图科技" in ctx_res["context"]
