"""票 38：两条硬删路径零清理两端 MemoryEdge——悬空边对 admin/内部面永久可见。

根因（立案基线 `a08878ea`）：`MemoryEdge.source_memory_id/target_memory_id`
无外键约束（tables.py:311-325），边清理全靠应用层；`DELETE /terminal/memory/{id}`
级联清单（FTS+审计+FactAtomic+向量）与 `promoter.delete_memory` 级联
（purge_facts+FTS+向量）**都不碰 MemoryEdge**——用户删任意参与过
supports/supersedes/links 的记忆，其在场每条边立即悬空、永久残留。
唯一按端点批量删边的实现只在 `lantai/eval/forgetting_quality.py:239-255`
（eval 清理，本轮修复范式：`or_(source==id, target==id)` 双端、同事务）。

读侧后果分层（工单亲核）：非 admin 由 edge_service「端点行不在 owners →
整条隐藏」挡住；admin / `principal=None`（MCP list_edges、CLI、worker）
经 get_edges/get_chain 照常拿到幽灵 id。悬空行同时是边数统计噪声。

六臂：
- terminal 主臂（红）：DELETE /terminal/memory/B → B 两端边（A→B、B→C）
  清零，第三方边（C→A）不动。
- promoter 主臂（红）：同构（promoter.delete_memory 是独立代码路径，
  source_service 文档级联经它删目标记忆）。
- 文档级联臂（红）：delete_document → 目标记忆经 delete_memory 删除 →
  其演化边（B→C supersedes）必须一并清理。
- 回滚锁：级联中途失败注入（工单 TDD 明示形态）→ 边与记忆均未删
  （整体回滚语义未被新代码破坏，严禁「边先独立 commit」反模式）。
- soft 态对照锁（绿）：撤回端点的边保留（审计价值，工单禁止项）。
- 读侧对照锁（绿）：存量悬空边对非 admin 依旧不可见、admin 可见
  （本票修写侧积与 admin 面，不动读侧过滤）。

真内存 SQLite + 真服务层全链，不 mock 被测链路；仅向量库用真内嵌
Chroma 临时目录（外部面替身边界同 tests/test_promoter_rollback_vector.py）。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.services import edge_service, source_service
from lantai.services.record_ops_service import retract_memory
from lantai.storage.vector_store import get_vector_store


@pytest.fixture()
def edge_env(monkeypatch, tmp_path):
    """真内存 SQLite + 真内嵌 Chroma（临时目录）。"""
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


_ADMIN = Principal(user_id="admin-user", role="admin")


def _mem(mid: str, *, key: str | None = None) -> MemoryItem:
    return MemoryItem(
        id=mid,
        content=f"票38种子-{mid}",
        key=key or f"k_{mid}",
        memory_type="semantic",
        status="active",
    )


def _edge(eid: str, src: str, dst: str, relation: str = "supports") -> MemoryEdge:
    return MemoryEdge(
        id=new_id("edge"), source_memory_id=src, target_memory_id=dst, relation=relation
    )


def _seed_triangle(e):
    """A --supports--> B --supersedes--> C --links--> A：B 是两条边的端点，
    C→A 是与 B 无关的第三方边（对照：只准删 B 的边）。"""
    with Session(e) as s:
        for m in (_mem("mem_A"), _mem("mem_B"), _mem("mem_C")):
            s.add(m)
        s.add(_edge("e1", "mem_A", "mem_B", "supports"))
        s.add(_edge("e2", "mem_B", "mem_C", "supersedes"))
        s.add(_edge("e3", "mem_C", "mem_A", "links"))
        s.commit()


def _edge_pairs(e) -> set[tuple[str, str, str]]:
    with Session(e) as s:
        return {
            (r.source_memory_id, r.relation, r.target_memory_id)
            for r in s.exec(select(MemoryEdge)).all()
        }


# ── 主臂 1：terminal DELETE 路由 ──


def test_terminal_delete_cleans_both_endpoint_edges(edge_env):
    """terminal 主臂（红）：DELETE /terminal/memory/B → B 两端边清零、
    第三方边不动。现状级联清单（FTS+审计+FactAtomic+向量）没有 MemoryEdge
    ——A→B 与 B→C 悬空残留。"""
    e = edge_env
    _seed_triangle(e)

    from lantai.api.routes_terminal import delete_memory as terminal_delete

    terminal_delete("mem_B", principal=_ADMIN)

    assert "mem_B" not in {m.id for m in Session(e).exec(select(MemoryItem)).all()}
    assert _edge_pairs(e) == {("mem_C", "links", "mem_A")}, (
        f"B 两端的边未清理（悬空残留）或误删第三方边：{_edge_pairs(e)}"
    )


# ── 主臂 2：promoter.delete_memory ──


def test_promoter_delete_memory_cleans_both_endpoint_edges(edge_env):
    """promoter 主臂（红）：独立代码路径，同构断言。"""
    e = edge_env
    _seed_triangle(e)

    from lantai.evolution.promoter import delete_memory as promoter_delete

    res = promoter_delete("mem_B")
    assert res["ok"] is True, res

    assert _edge_pairs(e) == {("mem_C", "links", "mem_A")}, (
        f"B 两端的边未清理（悬空残留）或误删第三方边：{_edge_pairs(e)}"
    )


# ── 主臂 3：documents 级联（source_service 全链）──


def test_document_cascade_cleans_evolution_edges(edge_env):
    """文档级联臂（红）：doc 来源边被 source_service 自有逻辑删除，但目标
    记忆 B 经 delete_memory 删除时，其**演化边**（B→C supersedes）此前
    无人清理。"""
    e = edge_env
    with Session(e) as s:
        from lantai.models.tables import RawDocument

        s.add(
            RawDocument(
                id="doc_ticket38",
                source_type="web",
                source_id="x1",
                url="https://example.com/t38",
                title="票38",
                content_hash="t38-hash",
                content="票38种子文档正文",
            )
        )
        s.add(_mem("mem_B"))
        s.add(_mem("mem_C"))
        s.add(_edge("edoc", "doc_ticket38", "mem_B", "supports"))
        s.add(_edge("eevo", "mem_B", "mem_C", "supersedes"))
        s.commit()

    res = source_service.delete_document("doc_ticket38", principal=_ADMIN)
    assert res["ok"] is True, res

    assert _edge_pairs(e) == set(), (
        f"目标记忆 B 的演化边（B→C supersedes）悬空残留：{_edge_pairs(e)}"
    )


# ── 回滚锁：级联中途失败 → 边与记忆均未删 ──


def test_cascade_failure_rolls_back_edges_and_memory(edge_env, monkeypatch):
    """回滚锁：级联中途失败注入（工单 TDD 明示形态）→ 整体回滚——
    边与记忆均在、严禁「边先独立 commit」反模式。两条路径各验一次。"""
    e = edge_env
    _seed_triangle(e)

    def _boom(session, memory_id):
        raise RuntimeError("ticket38 fault injection")

    import lantai.services.record_ops_service as ros

    monkeypatch.setattr(ros, "purge_facts_for_memory", _boom)

    # promoter 路径：ok=False，记忆与边原样
    from lantai.evolution.promoter import delete_memory as promoter_delete

    res = promoter_delete("mem_B")
    assert res["ok"] is False and "rolled back" in res["reason"], res
    assert "mem_B" in {m.id for m in Session(e).exec(select(MemoryItem)).all()}
    assert ("mem_A", "supports", "mem_B") in _edge_pairs(e), "边先于记忆被删（半写）"

    # terminal 路径：500，记忆与边原样
    from fastapi import HTTPException

    from lantai.api.routes_terminal import delete_memory as terminal_delete

    with pytest.raises(HTTPException) as ei:
        terminal_delete("mem_B", principal=_ADMIN)
    assert ei.value.status_code == 500
    assert "mem_B" in {m.id for m in Session(e).exec(select(MemoryItem)).all()}
    assert ("mem_A", "supports", "mem_B") in _edge_pairs(e)


# ── soft 态对照锁：撤回端点的边保留 ──


def test_soft_retracted_endpoint_keeps_edges(edge_env):
    """soft 态对照锁（绿，工单禁止项）：撤回是软态——端点的边保留
    （审计价值），本票只管行已删的硬悬空。"""
    e = edge_env
    _seed_triangle(e)
    assert retract_memory("mem_B", reason="票38:soft对照", actor="admin")["ok"] is True

    assert _edge_pairs(e) == {
        ("mem_A", "supports", "mem_B"),
        ("mem_B", "supersedes", "mem_C"),
        ("mem_C", "links", "mem_A"),
    }, "撤回（软态）端点的边被误删——审计价值面被破坏"


# ── 读侧对照锁：存量悬空边对非 admin 不可见、admin 可见 ──


def test_readside_filter_unchanged_for_dangling_edges(edge_env):
    """读侧对照锁（绿）：本票修写侧积与 admin/内部面，不动读侧过滤——
    绕过服务层直删（模拟存量悬空）后，非 admin 看不到悬空边、admin 仍见
    （现状锁：这正是本票要消灭的写侧形态，读侧行为逐字不变）。"""
    e = edge_env
    _seed_triangle(e)
    with Session(e) as s:  # 绕过服务层直删（存量悬空的模拟形态）
        mem = s.get(MemoryItem, "mem_B")
        s.delete(mem)
        s.commit()

    hidden = edge_service.list_edges("mem_B", principal=Principal(user_id="u", role="user"))
    assert hidden["edges"] == [], "非 admin 的悬空边可见性被改动（读侧应不动）"

    seen = edge_service.list_edges("mem_B", principal=_ADMIN)
    relations = {ed["relation"] for ed in seen["edges"]}
    assert relations == {"supports", "supersedes"}, (
        f"admin 面现状锁：悬空边本就可见（本票消灭该形态的写侧源头）；实得 {relations}"
    )
    assert get_vector_store() is not None  # 触碰向量面装配，保证夹具完整性
