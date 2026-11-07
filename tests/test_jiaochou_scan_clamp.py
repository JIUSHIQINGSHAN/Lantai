"""校雠 scan_redundancy 计算量与入参钳制冒烟测试（票 59，真库真引擎不 mock）。

病灶（.scratch/v0.68-next-window-2026-11/issues/59-jiaochou-scan-redundancy-quadratic.md）：
- `scan_redundancy` 全表拉 active 行 + O(n²) 两两 jieba 比对，`limit` 只截输出
  不截计算量——limit=10 在大库上仍做全量两两比对；
- `limit`/`similarity_threshold` 零校验（limit=-1/0、threshold=0/2/NaN 静默接受），
  MCP `jiaochou_collate` 入口原样透传——LLM 一次正常调用可拖死 stdio 单线程进程；
- `principal=None` 时 `_check_access` 全表放行（zhaji 范式是收敛到 default 属主空间）。

对票面 TDD 的一处口径申报：票面 TDD 1 要求「300 条计时、修复前超阈值」的时钟红臂。
本文件用**结构性证据**替代计时：扫描口径外显（`scanned_items` / `scan_truncated`）
+ 候选池硬上界可配——断言「40 行的库在 cap=20 下只比对 20 行」是确定性的，而
「修复前后耗时对比」正是本仓 watch 台账里反复登记的机器速度敏感 flaky 家族
（.scratch/watch/build-context-2s-timeout-flaky.md），不把同一形状的病写回测试面。

测试纪律（AGENTS.md）：全部真私有内存库 + 真 JiaochouEngine 直调（或真 handle()
JSON-RPC 分发），patch 仅落 settings 扫描上限旋钮（纪律允许口径），
不 mock 被测链路的任何内部逻辑。
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from lantai.cognition.jiaochou import JiaochouEngine
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

pytestmark = pytest.mark.mcp_full_surface()

# 与 routes_jiaochou.py GET /jiaochou/scan 的 Query(ge=1, le=200) 同界——
# 引擎是单一真源，REST 的 pydantic 校验与 MCP 的 -32602 继承同一契约。
_OUTPUT_LIMIT_MAX = 200

_MCP_PATH = Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py"


def _load_mcp():
    """exec 副本加载 mcp 模块（test_mcp.py 同款范式）。

    服务层/引擎经 sys.modules 解析到唯一真实现，被测链路不 mock；副本只隔离
    mcp 模块自身的模块级单例（_guard_instance），避免给隔离护栏台账添新例证
    （票 01 收口纪律：新增测试文件台账零新增）。
    """
    spec = importlib.util.spec_from_file_location("mcp_server_jiaochou_clamp", _MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _private_store() -> Iterator[Session]:
    """私有内存库：本文件所有用例不碰会话级共享库（跑测顺序免疫）。"""
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


def _mem(owner: str | None, tag: str, content: str, t) -> MemoryItem:
    return MemoryItem(
        id=f"mem-{tag}-{uuid.uuid4().hex[:8]}",
        content=content,
        confidence=0.9,
        user_id=owner,
        created_at=t,
        updated_at=t,
    )


# 近重复样本对（分词高重合，compare_and_collate 相似度 > 0.5，既有 seam 用例同款语形）
_PAIR_TEXT_A = "生产集群已开启两级本地与分布式缓存"
_PAIR_TEXT_B = "生产环境集群配置了两级本地与分布式缓存架构"


def test_scan_rejects_nonpositive_limit():
    """钳制臂：limit<=0 必须拒绝，不许静默当 1 用（现状：-1/0 被当作「凑够 1 对就断」）。"""
    t = utcnow()
    with _private_store() as session:
        for i in range(4):
            session.add(_mem("default", f"lim-{i}", f"冗余样本第 {i} 条同一句话", t))
        session.commit()
        engine = JiaochouEngine(session=session, principal=None)
        for bad in (0, -5):
            with pytest.raises(ValueError, match="limit"):
                engine.scan_redundancy(limit=bad, similarity_threshold=0.5)


def test_scan_rejects_threshold_out_of_range():
    """钳制臂：threshold 越界（含 NaN）必须拒绝——threshold=0 现状把所有对都报成冗余。"""
    t = utcnow()
    with _private_store() as session:
        for i in range(4):
            session.add(_mem("default", f"thr-{i}", f"冗余样本第 {i} 条同一句话", t))
        session.commit()
        engine = JiaochouEngine(session=session, principal=None)
        for bad in (0.0, 1.5, float("nan")):
            with pytest.raises(ValueError, match="similarity_threshold"):
                engine.scan_redundancy(limit=10, similarity_threshold=bad)


def test_scan_rejects_limit_above_output_cap():
    """钳制臂：limit 上界对齐 REST le=200，越界拒绝（MCP 面借 -32602 继承同一契约）。"""
    t = utcnow()
    with _private_store() as session:
        session.add(_mem("default", "cap-1", "样本一句话", t))
        session.commit()
        engine = JiaochouEngine(session=session, principal=None)
        with pytest.raises(ValueError, match="limit"):
            engine.scan_redundancy(limit=_OUTPUT_LIMIT_MAX + 1, similarity_threshold=0.5)


def test_scan_reports_scan_universe_and_truncation():
    """口径臂：结果必须外显扫描口径——未截断时 scanned_items=全表、scan_truncated=False。"""
    t = utcnow()
    with _private_store() as session:
        for i in range(6):
            session.add(_mem("default", f"cal-{i}", f"口径样本第 {i} 条同一句话", t))
        session.commit()
        res = JiaochouEngine(session=session, principal=None).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
    assert res["scanned_items"] == 6
    assert res["scan_truncated"] is False


def test_scan_universe_hard_capped_regardless_of_table_size(monkeypatch):
    """计算量杠杆臂：候选池硬上界与库大小解耦——cap=20 时 40 行的库只比对 20 行。

    修复前该用例不可能绿：`limit` 只截输出对数，计算量恒等于全表两两组合，
    且结果里没有 scanned_items/scan_truncated 口径。修复后 SQL LIMIT 钉住扫描
    宇宙（cap+1 探测截断），计算量上限恒定——这是对票面「规模红臂」的结构性
    替代（见模块 docstring 口径申报）。
    """
    monkeypatch.setattr(settings, "JIAOCHOU_MAX_SCAN_CANDIDATES", 20)
    t = utcnow()
    with _private_store() as session:
        # 冗余对排最前：落在截断窗内，钳制不得吞掉窗内明显冗余
        win_a = _mem("default", "win-a", _PAIR_TEXT_A, t)
        win_b = _mem("default", "win-b", _PAIR_TEXT_B, t)
        session.add(win_a)
        session.add(win_b)
        for i in range(38):
            session.add(_mem("default", f"crowd-{i}", f"填充样本第 {i} 条同一句话", t))
        session.commit()
        res = JiaochouEngine(session=session, principal=None).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
        rows = session.exec(select(MemoryItem)).all()
    window_ids = {m.id for m in rows[:20]}
    assert win_a.id in window_ids and win_b.id in window_ids
    assert res["scan_truncated"] is True
    assert res["scanned_items"] == 20
    assert res["total_pairs"] >= 1
    for pair in res["redundant_pairs"]:
        assert {pair["memory_id_a"], pair["memory_id_b"]} <= window_ids


def test_scan_semantic_pair_survives_crowded_clamped_store(monkeypatch):
    """语义回归臂：钳制后明显冗余对仍被报告（票面 TDD 3——收窄不得吞掉真冗余）。"""
    monkeypatch.setattr(settings, "JIAOCHOU_MAX_SCAN_CANDIDATES", 8)
    t = utcnow()
    with _private_store() as session:
        pair_a = _mem("default", "pair-a", _PAIR_TEXT_A, t)
        pair_b = _mem("default", "pair-b", _PAIR_TEXT_B, t)
        session.add(pair_a)
        session.add(pair_b)
        for i in range(30):  # 拥挤库，远超 cap=8
            session.add(_mem("default", f"noise-{i}", f"干扰样本第 {i} 条同一句话", t))
        session.commit()
        res = JiaochouEngine(session=session, principal=None).scan_redundancy(
            limit=5, similarity_threshold=0.5
        )
    assert res["scanned_items"] == 8
    hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
    assert {pair_a.id, pair_b.id} in hits, f"截断窗内（前 2 行）的冗余对被钳制吞掉：{hits}"


def test_scan_identity_none_principal_sees_default_only():
    """身份臂（票 57 收敛口径）：principal=None 只见 default/NULL 属主行，不见他人私行。"""
    t = utcnow()
    with _private_store() as session:
        alice_a = _mem("user-a", "alice-a", _PAIR_TEXT_A, t)
        alice_b = _mem("user-a", "alice-b", _PAIR_TEXT_B, t)
        session.add(alice_a)
        session.add(alice_b)
        session.add(_mem("default", "def-a", "默认属主的缓存集群说明一", t))
        session.add(_mem("default", "def-b", "默认属主的缓存集群说明二", t))
        session.add(_mem(None, "null-a", "无属主老行的缓存集群说明一", t))
        session.add(_mem(None, "null-b", "无属主老行的缓存集群说明二", t))
        session.commit()
        res = JiaochouEngine(session=session, principal=None).scan_redundancy(
            limit=10, similarity_threshold=0.5
        )
    assert res["scanned_items"] == 4  # 2 default + 2 NULL；user-a 两行不可见
    hits = [{p["memory_id_a"], p["memory_id_b"]} for p in res["redundant_pairs"]]
    assert hits, "default/NULL 属主内部的冗余对应照常报告"
    for pair in res["redundant_pairs"]:
        assert pair["memory_id_a"] != alice_a.id
        assert pair["memory_id_b"] != alice_a.id
        assert pair["memory_id_a"] != alice_b.id
        assert pair["memory_id_b"] != alice_b.id


def test_mcp_scan_rejects_invalid_params():
    """MCP 入口臂：真 handle() JSON-RPC 分发，非法参数收 -32602 错误帧而非结果帧。"""
    mod = _load_mcp()
    for bad_params in (
        {"scan": True, "limit": -1},
        {"scan": True, "limit": 0},
        {"scan": True, "limit": _OUTPUT_LIMIT_MAX + 1},
        {"scan": True, "similarity_threshold": 0.0},
        {"scan": True, "similarity_threshold": 2.0},
    ):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "jiaochou_collate", "arguments": bad_params},
        }
        resp = mod.handle(req)
        assert resp is not None and "error" in resp, f"非法参数被静默接受：{bad_params}"
        assert resp["error"]["code"] == -32602, f"拒绝语义不是 invalid params：{bad_params}"


def test_mcp_scan_valid_params_still_roundtrip():
    """MCP 入口对照臂：合法参数照常返回结果帧（拒绝面不得误伤正常调用）。"""
    mod = _load_mcp()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "jiaochou_collate",
            "arguments": {"scan": True, "limit": 5, "similarity_threshold": 0.5},
        },
    }
    resp = mod.handle(req)
    assert resp is not None and "result" in resp
    assert "error" not in resp
    data = json.loads(resp["result"]["content"][0]["text"])
    assert "redundant_pairs" in data
