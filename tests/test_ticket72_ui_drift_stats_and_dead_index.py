"""票 72 红测：UI 前后端漂移双病灶——悬镜头部 stats 键错位 + _INDEX_HTML 死副本。

病灶（工单锚点，HEAD=57a9842a 现场复测）：
1. stats 键错位：前端 xuanjing.js:89-95 消费 stats.total_nodes / total_edges /
   avg_decay_score 三键；服务端 xuanjing_service.py 两分支（空图 :114 / 有图
   :220-225）只输出 node_count / edge_count / lane_counts / relation_counts——
   三键恒不存在，「平均时效」控件恒显 '—'（写死在界面上的死功能），
   `?? currentData.nodes.length` 把本次加载量冒充全库总量。
2. 死副本：routes_ui.py:515-619 内嵌 _INDEX_HTML 全文件唯一出现处即定义行，
   无任何路由返回它；活页面是 :276-278 FileResponse 服务的 ui/index.html。
   两个「事实源」已漂移（死副本仍是五卡旧导航），未来改错副本即静默回退。

接缝（派单预敲定）：
- arm1 stats 契约臂 @ GET /graph/xuanjing（真内存库空图/有图两态）：前端消费
  键名以常量锁进测试防再漂移；空图 avg None、有图 avg 数值、两分支键组一致。
- arm2 死代码臂 @ import lantai.api.routes_ui：模块级锁形态（先例
  tests/test_dead_code_removal.py，.scratch/dead-code-removal/01）+ 源码文本
  零残留双保险。
- arm4 契约纽带自证臂：从 xuanjing.js 源文本提取全部 stats.<key> 消费点断言
  ⊇ FE_CONSUMED_KEYS——前端改键而常量未跟时先红（test_monitor_ui.py 读 JS
  源交叉验证强形态同构，Standards 轴建议）。
- arm3 回归臂：xuanjing 家族既有测试（test_xuanjing_topology_smoke /
  test_xuanjing_ui_smoke / test_graph*）随收口全量运行覆盖，不单写断言。

纪律：真内存库直调真路由，零 mock 被测链路（测试库走 conftest/夹具隔离，
红线：零连接触碰 LANTAI_HOME 真实开发库）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.api.app import app
from lantai.core.auth import Principal, get_current_user
from lantai.models.tables import MemoryEdge, MemoryItem

# xuanjing.js:89-95 逐字消费的三个键名（防再漂移的契约纽带：服务端 stats 必须始终含这三个键）
FE_CONSUMED_KEYS = ("total_nodes", "total_edges", "avg_decay_score")


@pytest.fixture()
def graph_env(monkeypatch):
    """真内存 SQLite（StaticPool）+ db.get_session 注入 + principal 覆写（家族同款）。"""
    import lantai.models.tables  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.api.app import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    test_user = Principal(user_id="user-1", tenant_id="t-1", role="user")
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: test_user)
    return session_factory


def _create_mem(mid: str, content: str, decay_score: float) -> MemoryItem:
    from datetime import UTC, datetime, timedelta

    return MemoryItem(
        id=mid,
        memory_type="semantic",
        key=f"k-{mid}",
        content=content,
        lane="fact",
        user_id="user-1",
        tenant_id="t-1",
        decay_score=decay_score,
        importance=0.8,
        use_count=5,
        helpful_count=2,
        status="active",
        created_at=datetime.now(UTC) - timedelta(days=2),
        updated_at=datetime.now(UTC) - timedelta(hours=1),
    )


# ── arm1：stats 契约臂（现状红：前端消费键在服务端契约面不存在）──────────────


def test_arm1a_empty_graph_stats_has_frontend_keys(graph_env):
    """空图态：GET /graph/xuanjing → stats 含前端消费全键；total/avg 语义正确
    （空图 total_nodes=0、avg_decay_score=None）。"""
    client = TestClient(app)
    resp = client.get("/graph/xuanjing")
    assert resp.status_code == 200, resp.text
    stats = resp.json()["stats"]

    missing = [k for k in FE_CONSUMED_KEYS if k not in stats]
    assert not missing, f"空图分支 stats 缺前端消费键（悬镜头部死控件病灶）：{missing}"

    assert stats["total_nodes"] == 0
    assert stats["total_edges"] == 0
    assert stats["avg_decay_score"] is None, "空图平均衰减应为 None（不是 0 也不是缺失）"


def test_arm1b_nonempty_graph_stats_values_match_nodes(graph_env):
    """有图态：stats 三键语义正确——total_nodes/total_edges 与节点/边数一致，
    avg_decay_score 等于节点衰减均值（期望值来自构造时写死的字面量，独立真值）。"""
    session_factory = graph_env
    with session_factory() as s:
        # 衰减字面量 0.82 / 0.61 → 期望均值 0.715（round 3 位）
        s.add(_create_mem("m-t72a", "票 72 契约臂节点甲", decay_score=0.82))
        s.add(_create_mem("m-t72b", "票 72 契约臂节点乙", decay_score=0.61))
        s.add(
            MemoryEdge(
                id="e-t72",
                source_memory_id="m-t72a",
                target_memory_id="m-t72b",
                relation="supports",
                confidence=0.85,
                tenant_id="t-1",
                user_id="user-1",
            )
        )
        s.commit()

    client = TestClient(app)
    resp = client.get("/graph/xuanjing")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    stats = data["stats"]

    missing = [k for k in FE_CONSUMED_KEYS if k not in stats]
    assert not missing, f"有图分支 stats 缺前端消费键：{missing}"

    assert stats["total_nodes"] == len(data["nodes"]) == 2
    assert stats["total_edges"] == len(data["edges"]) == 1
    assert stats["avg_decay_score"] == pytest.approx(0.715), (
        f"平均衰减应为节点均值 0.715，实际 {stats.get('avg_decay_score')}"
    )


def test_arm1c_stats_key_set_identical_across_branches(graph_env):
    """两分支键组必须一致（工单修复目标 3）：空图分支与有图分支 stats 输出
    同一组键，防「空图分支键组无人消费」的第二次漂移。"""
    session_factory = graph_env
    client = TestClient(app)

    empty = client.get("/graph/xuanjing").json()["stats"]

    with session_factory() as s:
        s.add(_create_mem("m-t72c", "键组一致性检查节点", decay_score=0.9))
        s.commit()
    nonempty = client.get("/graph/xuanjing").json()["stats"]

    assert set(empty) == set(nonempty), (
        f"空图/有图两分支 stats 键组漂移：空图 {sorted(empty)} vs 有图 {sorted(nonempty)}"
    )


# ── arm2：死代码臂（现状红：_INDEX_HTML 死副本仍在模块上）─────────────────────


def test_arm2_routes_ui_has_no_dead_index_html():
    """routes_ui 模块面：_INDEX_HTML 死副本必须删净（先例
    tests/test_dead_code_removal.py，dead-code-removal/01 的模块级锁形态）+
    源码文本零残留双保险（防改名绕过 hasattr 锁）。"""
    import lantai.api.routes_ui as routes_ui_module

    assert not hasattr(routes_ui_module, "_INDEX_HTML"), (
        "routes_ui._INDEX_HTML 死副本仍在（活页面是 FileResponse 服务的 ui/index.html，"
        "内嵌副本与之漂移，未来改错副本即静默回退）"
    )

    from pathlib import Path

    src = Path(routes_ui_module.__file__).read_text(encoding="utf-8")
    assert "_INDEX_HTML" not in src, "routes_ui.py 源码仍有 _INDEX_HTML 文本残留"


def test_arm4_frontend_source_mentions_all_consumed_keys():
    """契约纽带自证臂（Standards 轴建议，test_monitor_ui.py 读 JS 源交叉验证
    强形态同构）：从 xuanjing.js 源文本提取全部 stats.<key> 消费点，断言
    FE_CONSUMED_KEYS ⊆ 提取集——前端改键名而常量未跟时在此先红，再由
    arm1a/arm1b 锁服务端契约，两侧防漂移闭环。"""
    import re
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "lantai" / "api" / "ui" / "xuanjing.js"
    consumed = set(re.findall(r"stats\.([a-z_]+)", js.read_text(encoding="utf-8")))
    assert consumed, "xuanjing.js 源文本未提取到任何 stats.* 消费点（正则失效）"
    missing = set(FE_CONSUMED_KEYS) - consumed
    assert not missing, f"前端已不消费（或改名）的键仍留在 FE_CONSUMED_KEYS：{missing}"
