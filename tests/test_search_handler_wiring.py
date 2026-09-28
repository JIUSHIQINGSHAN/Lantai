"""票 `.scratch/mcp-identity-gaps/11` 的**接线那一半**：`handle_search` 没把
principal 传给 `hybrid_search`。

**先说影响**：`handle_search`（`lantai/cli/mcp.py`）在 `:52` 算出了
`principal`，却只在 `:79` 喂给检索事件日志——`hybrid_search(...)`
**一个身份参数都没收到**。于是宿主透传 `user_id` 的 `search` 调用
照样全库召回。**这是接线漏，不是 None 语义问题**：即使 None 语义那条
（同票的"入口收敛"）修好，"传了身份却不生效"依然在。

本文件只覆盖接线；None 语义由 `tests/test_hybrid_none_principal.py` 覆盖
（按 01b 教训分开提交、分开验证）。

**不 mock 冒烟**：走 `handle_search` 真入口，真内存 SQLite + 真 FTS5
trigram + 真 `MemoryItem` 行；向量通道 monkeypatch 成抛错以孤立验证
SQL 通道（否则向量召回会把结果补回来，掩盖 SQL 侧的漏）。
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

REPO_ROOT = Path(__file__).parent.parent
MCP_PATH = REPO_ROOT / "lantai" / "cli" / "mcp.py"

MARK_B = "ZZBBBZZ B 的私有记忆：对家报价底牌 88 万"
MARK_A = "ZZAAAAZZ A 自己的记忆：本周排期"


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server_wiring_11", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """真建库 + 真 FTS + 种子数据 + 关掉向量通道，返回 mcp 模块。"""
    from lantai.core.time import utcnow
    from lantai.models.tables import MemoryItem
    from lantai.storage.fts import init_fts, sync_fts

    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)

    now = utcnow()
    with Session(e) as s:
        for mid, owner, content in [
            ("m-B", "user-B", MARK_B),
            ("m-A", "user-A", MARK_A),
            ("m-legacy", None, "ZZLEGZZ 老行的记忆（NULL 属主）"),
        ]:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
                    namespace="default",
                    status="active",
                    tier="working",
                    importance=0.5,
                    confidence=1.0,
                    reason="",
                    role="OBSERVATION",
                    lane="general",
                    domain="general",
                    version=1,
                    use_count=0,
                    helpful_count=0,
                    decay_score=1.0,
                    decay_class="slow",
                    event_time_precision="none",
                    lifecycle_status="ACTIVE",
                    user_id=owner,
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
        for mid, body in [
            ("m-B", MARK_B),
            ("m-A", MARK_A),
            ("m-legacy", "ZZLEGZZ 老行的记忆（NULL 属主）"),
        ]:
            sync_fts(s, mid, body)
        s.commit()

    import lantai.retrieval.hybrid as hybrid_mod
    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "engine", e)
    monkeypatch.setattr(
        hybrid_mod,
        "get_vector_store",
        lambda: (_ for _ in ()).throw(RuntimeError("probe: vector off")),
    )
    return _load_mcp()


def _texts(resp) -> str:
    """把 MCP 响应的 content 块拼成一个字符串（供 ZZ 标记搜索）。

    块形态不假设：可能缺 `text` 键，也可能根本不是 dict——
    一律按"取不到就跳过"处理，不让取文本的副作用影响归属断言。
    """
    parts = []
    for block in resp.get("result", {}).get("content", []) or []:
        if isinstance(block, dict):
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def _call(mod, query: str, **extra):
    """`force=True` 绕过相关性闸门——本文件验的是**归属**，
    闸门要不要放行是另一件事（`gate/prefilter.py` 的地盘）。
    不 force 的话 "报价"/"排期" 这类短查询会被
    `no_signal` 挡在检索之前，根本走不到被测的那一行。"""
    args = {"query": query, "top_k": 10, "force": True}
    args.update(extra)
    return mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": args},
        }
    )


class TestSearchHandlerWiring:
    """`handle_search` 必须把 principal 传给 `hybrid_search`。"""

    def test_host_passed_user_id_is_honored(self, wired):
        """宿主透传 user_id=user-A → 搜不到 B 的私有记忆（接线修好后才成立）。"""
        resp = _call(wired, "报价", user_id="user-A")
        body = _texts(resp)
        assert "result" in resp, f"handler 报错了：{resp}"
        assert MARK_B not in body, f"透传了 user_id 却仍召回 B 的私有记忆：{body[:300]}"

    def test_host_passed_user_id_still_recalls_own(self, wired):
        """宿主透传 user_id=user-A → 仍能搜到自己的（收窄不是清空）。"""
        resp = _call(wired, "排期", user_id="user-A")
        body = _texts(resp)
        assert "result" in resp, f"handler 报错了：{resp}"
        assert MARK_A in body, f"A 搜不到自己的记忆：{body[:300]}"

    def test_legacy_null_owner_row_still_recalled(self, wired):
        """单人部署不空转：NULL 属主老行在收敛后仍可召回。"""
        resp = _call(wired, "老行", user_id="user-A")
        body = _texts(resp)
        assert "result" in resp, f"handler 报错了：{resp}"
        assert "ZZLEGZZ" in body, f"NULL 老行搜不到了（单人部署空转）：{body[:300]}"
