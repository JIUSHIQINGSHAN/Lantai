"""考功（ADR-0031）：长程反馈驱动的记忆价值演化与升降评定测试。

验证：
1. kaogong_service 纯函数功过评级（上考晋升/下考降权/中考保持）；
2. 样本不足时不轻率升降（宁 miss 不脏写）；
3. 真实 SQLite 数据库考功周期执行（不 mock 冒烟）；
4. REST 端点 POST /evolution/kaogong 与 MCP kaogong_eval 工具。
"""
import pytest
from fastapi.testclient import TestClient

from api_server import app
from lantai.models.tables import MemoryItem
from lantai.services.kaogong_service import (
    evaluate_memory_item_grade,
    run_kaogong_cycle,
    get_kaogong_report,
)


class TestKaogongPureLogic:
    """测试考功纯函数评级逻辑。"""

    def test_evaluate_promote_longterm(self):
        """高频高采纳（use_count>=3, ratio>=0.8）-> 上考晋升长期语义层。"""
        mem = MemoryItem(
            id="mem_test_promote",
            memory_type="semantic",
            key="key1",
            content="高价值核心配置",
            use_count=5,
            helpful_count=5,
            tier="working",
            decay_class="episodic",
        )
        res = evaluate_memory_item_grade(mem)
        assert res["action"] == "promote_longterm"
        assert res["new_tier"] == "longterm"
        assert res["new_decay_class"] == "semantic"

    def test_evaluate_demote(self):
        """高频低效（use_count>=3, ratio<=0.2）-> 下考降权。"""
        mem = MemoryItem(
            id="mem_test_demote",
            memory_type="semantic",
            key="key2",
            content="低质过时信息",
            use_count=4,
            helpful_count=0,
            tier="working",
            importance=0.6,
        )
        res = evaluate_memory_item_grade(mem)
        assert res["action"] == "demote_deprecate"
        assert res["new_importance"] <= 0.1

    def test_evaluate_insufficient_samples_keep(self):
        """样本不足（use_count < 3）-> 中考保持原状（宁 miss 不脏写）。"""
        mem = MemoryItem(
            id="mem_test_neutral",
            memory_type="semantic",
            key="key3",
            content="普通日常记忆",
            use_count=2,
            helpful_count=2,
            tier="working",
            decay_class="episodic",
        )
        res = evaluate_memory_item_grade(mem)
        assert res["action"] == "keep_neutral"


class TestKaogongServiceDB:
    """真实 SQLite 数据库不 mock 冒烟测试。"""

    def test_run_kaogong_cycle_in_db(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            m1 = MemoryItem(
                id="mem_db_01",
                memory_type="semantic",
                key="config_01",
                content="大哥的华硕天选三硬件环境",
                use_count=6,
                helpful_count=6,
                tier="working",
                decay_class="episodic",
                status="active",
            )
            m2 = MemoryItem(
                id="mem_db_02",
                memory_type="semantic",
                key="config_02",
                content="某临时废弃配置",
                use_count=5,
                helpful_count=0,
                tier="working",
                importance=0.8,
                status="active",
            )
            s.add(m1)
            s.add(m2)
            s.commit()

            # 运行考功周期
            report = run_kaogong_cycle(session=s)
            assert report["evaluated"] >= 2
            assert report["promoted_longterm"] >= 1
            assert report["demoted"] >= 1

            # 验证 DB 落库状态
            db_m1 = s.get(MemoryItem, "mem_db_01")
            assert db_m1.tier == "longterm"
            assert db_m1.decay_class == "semantic"

            db_m2 = s.get(MemoryItem, "mem_db_02")
            assert db_m2.importance <= 0.1


class TestKaogongEndpointsAndMCP:
    """测试 REST 端点与 MCP 工具。"""

    def test_rest_kaogong_cycle(self, param_env):
        session_factory, _ = param_env
        client = TestClient(app)

        with session_factory() as s:
            m = MemoryItem(
                id="mem_rest_kg",
                memory_type="semantic",
                key="kg_k",
                content="REST 端点考功测试记忆",
                use_count=4,
                helpful_count=4,
                tier="working",
                decay_class="episodic",
                status="active",
            )
            s.add(m)
            s.commit()

        # 触发 REST 考功周期
        r1 = client.post("/evolution/kaogong")
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["evaluated"] >= 1

        # 获取考功报告
        r2 = client.get("/evolution/kaogong/report")
        assert r2.status_code == 200
        assert "evaluated" in r2.json()

    def test_mcp_kaogong_eval(self, param_env):
        session_factory, _ = param_env
        from scripts.mcp_server import handle_kaogong_eval
        res = handle_kaogong_eval({})
        assert "evaluated" in res
