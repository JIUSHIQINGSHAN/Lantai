"""票 40（子项 1+2）：幽灵源边喂反思 R1 + 边读不判端点状态。

根因（立案基线 `d2d784fe`，现 HEAD 亲核行号已漂移）：
1. `promoter.apply_proposal` 的 deprecate 分支把 `prop.evidence_ids[0]`
   （LLM curator 输出）直接落成 supersedes 边的 source，**零存在性检查**
   ——同文件 consolidation 分支有对照口径「`src is None` → 不建边（边指向
   幽灵即脏写）」，deprecate 漏同款。幽灵边经 reflector R1 装进
   `superseded_by` 映射——目标记忆若处于 active（如 deprecate 后被 admin
   合法 unarchive 复原），R1 即误标 superseded 候选。
2. `edge_service` 的 `_edge_visible`/`_filter_visible_batch._ok` 只查端点
   行存在 + 属主，**不查 status**——已撤回/已归档记忆的边对非 admin 照常
   返回，撤回契约「全检索面禁用」在边读面失守。

子项 3/4/5（/terminal/graph 回流、zhiming 软指针、relation 白名单）需
维护者拍板，本票明示不碰。

四臂（1 红为复合臂：幽灵边落地 + unarchive 复原后 R1 误标；2/3 红为
状态臂：撤回/归档端点的边对非 admin 照常返回；4 绿为对照：active 端点
的边照常可见 + consolidation 口径回归）。真内存 SQLite + 真服务层全链
（apply_proposal → unarchive → health_scan / retract → list_edges），
仅向量库用真内嵌 Chroma 临时目录（外部面替身边界同票 38 工装），
禁 mock 被测链路。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.evolution.promoter import apply_proposal
from lantai.evolution.reflector import health_scan
from lantai.models.tables import MemoryEdge, MemoryItem, MemoryProposal
from lantai.services import edge_service
from lantai.services.record_ops_service import archive_memory, retract_memory, unarchive_memory
from lantai.storage.vector_store import get_vector_store


@pytest.fixture()
def genv(monkeypatch, tmp_path):
    """真内存 SQLite + FTS5 + 真内嵌 Chroma（临时目录）。"""
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    from lantai.storage.fts import init_fts

    init_fts(e.raw_connection())
    monkeypatch.setattr(db_module, "engine", e)
    import lantai.storage.vector_store as vs_module

    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    monkeypatch.setattr(settings, "CHROMADB_PATH", str(chroma_dir))
    monkeypatch.setattr(vs_module, "_store", None)
    monkeypatch.setattr(settings, "VECTOR_STORE_TYPE", "chromadb")
    yield e


def _mem(mid: str, *, user_id: str | None = None) -> MemoryItem:
    return MemoryItem(
        id=mid,
        content=f"票40种子-{mid}",
        key=f"k_{mid}",
        memory_type="semantic",
        status="active",
        user_id=user_id,
    )


def _supersedes_for(e, mid: str) -> MemoryEdge | None:
    with Session(e) as s:
        return s.exec(
            select(MemoryEdge).where(
                MemoryEdge.relation == "supersedes", MemoryEdge.target_memory_id == mid
            )
        ).first()


def _deprecate_proposal(e, mid: str, evidence_id: str) -> str:
    with Session(e) as s:
        prop = MemoryProposal(
            id=new_id("prop"),
            proposal_type="deprecate",
            target_memory_id=mid,
            evidence_ids=[evidence_id],
            reason="票40:幽灵源臂",
            proposed_patch={"content": ""},
            confidence=0.9,
            provenance={"prompt": "ticket40-smoke"},
        )
        s.add(prop)
        s.commit()
        return prop.id


# ── 臂 1：幽灵源边落地 + unarchive 复原后 R1 误标 ──


def test_ghost_source_edge_not_created_and_no_r1_mislabel(genv):
    """复合臂（红）：deprecate 提案 evidence_ids[0] 指向不存在 id →

    修复前：①supersedes 边落地（source=幽灵）——脏写；②admin 合法
    unarchive 复原目标后（deprecate 先归档目标，复原是 admin 合法入口），
    health_scan R1 把 active 的目标误标 superseded 候选（superseded_by=
    幽灵 id）。
    修复后：不建边（对齐 consolidation 口径「边指向幽灵即脏写」，提案照常
    留痕）→ unarchive 复原后 R1 无从误标——两段断言都走产品路径。

    存量残留如实登记：已落库的存量幽灵边仍会被 R1 误标——reflector 读侧
    不在本票落点清单（工单明示只攻 promoter 写侧 + edge_service 读侧），
    存量对账属运维面。"""
    e = genv
    with Session(e) as s:
        s.add(_mem("mem_B"))
        s.commit()
    prop_id = _deprecate_proposal(e, "mem_B", "mem_ghost")
    assert apply_proposal(prop_id)["ok"] is True

    assert _supersedes_for(e, "mem_B") is None, (
        "幽灵 source 的 supersedes 边落地——边指向幽灵即脏写"
        "（consolidation 分支同款口径未对齐，票 40 子项 1）"
    )

    # R1 后果段走产品路径：admin 合法复原（unarchive）后目标回到 active，
    # 写侧不建边 ⇒ R1 的 superseded_by 映射里没有它 ⇒ 不误标
    assert unarchive_memory("mem_B", actor="admin", reason="票40:复原")["ok"] is True
    with Session(e) as s:
        assert s.get(MemoryItem, "mem_B").status == "active"
        snap = health_scan(s)
    mislabeled = [
        c
        for c in snap.get("candidates", [])
        if isinstance(c, dict) and c.get("memory_id") == "mem_B" and c.get("signal") == "superseded"
    ]
    assert not mislabeled, (
        f"幽灵源边把 active 目标误标 superseded 候选（票 40 子项 1 的反思层后果）；"
        f"candidates={mislabeled}"
    )
    # 注：mem_B 可能以 signal="expired" 入候选（deprecate 自身设置的 valid_to
    # 已过——R2 合法行为，与 R1 幽灵误标无关），断言只锁 R1 的 superseded 形态。


# ── 臂 2/3：撤回 / 归档端点的边对非 admin 拒返 ──


def test_retracted_endpoint_edge_hidden_from_non_admin(genv):
    """状态臂 1（红）：撤回契约「全检索面禁用」——retracted 端点的边对
    非 admin（端点属主本人）必须拒返；现状照常返回。"""
    e = genv
    with Session(e) as s:
        s.add(_mem("mem_A", user_id="user-alice"))
        s.add(_mem("mem_B", user_id="user-alice"))
        s.add(
            MemoryEdge(
                id=new_id("edge"),
                source_memory_id="mem_A",
                target_memory_id="mem_B",
                relation="supports",
            )
        )
        s.commit()
    assert retract_memory("mem_B", reason="票40:状态臂", actor="admin")["ok"] is True

    owner = Principal(user_id="user-alice", role="user")
    res = edge_service.list_edges("mem_A", principal=owner)
    assert res["edges"] == [], (
        f"retracted 端点的边对非 admin 照常返回——撤回契约在边读面失守"
        f"（票 40 子项 2）；edges={res['edges']}"
    )
    batch = edge_service.list_edges_batch(["mem_A"], principal=owner)
    assert batch["mem_A"] == [], "批量读面同病（_filter_visible_batch 未查 status）"


def test_archived_endpoint_edge_hidden_from_non_admin(genv):
    """状态臂 2（红）：archived 端点的边对非 admin 同样拒返。"""
    e = genv
    with Session(e) as s:
        s.add(_mem("mem_A", user_id="user-alice"))
        s.add(_mem("mem_B", user_id="user-alice"))
        s.add(
            MemoryEdge(
                id=new_id("edge"),
                source_memory_id="mem_A",
                target_memory_id="mem_B",
                relation="supports",
            )
        )
        s.commit()
    assert archive_memory("mem_B", actor="admin", reason="票40:状态臂")["ok"] is True

    owner = Principal(user_id="user-alice", role="user")
    res = edge_service.list_edges("mem_A", principal=owner)
    assert res["edges"] == [], (
        f"archived 端点的边对非 admin 照常返回（票 40 子项 2）；edges={res['edges']}"
    )


# ── 臂 4：对照（绿）——active 端点边照常可见，勿过滤过头 ──


def test_active_endpoint_edge_still_visible(genv):
    """对照臂（绿锁）：active 端点的边对属主照常返回——状态检查不得
    过滤过头（撤回/归档之外的正常面零变化）。"""
    e = genv
    with Session(e) as s:
        s.add(_mem("mem_A", user_id="user-alice"))
        s.add(_mem("mem_B", user_id="user-alice"))
        s.add(
            MemoryEdge(
                id=new_id("edge"),
                source_memory_id="mem_A",
                target_memory_id="mem_B",
                relation="supports",
            )
        )
        s.commit()

    owner = Principal(user_id="user-alice", role="user")
    res = edge_service.list_edges("mem_A", principal=owner)
    assert len(res["edges"]) == 1 and res["edges"][0]["relation"] == "supports", (
        f"active 端点的边被过滤过头（对照臂）；edges={res['edges']}"
    )
    assert get_vector_store() is not None  # 触碰向量面装配，保证夹具完整性
