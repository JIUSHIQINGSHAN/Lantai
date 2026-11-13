"""票 79：/memory/add/raw 去重归属收窄在 REST 入口未接线——principal 不透传。

病灶（`.scratch/v0.68-next-window-2026-11/issues/79-raw-route-principal-not-wired.md`，
行号亲核 HEAD `8c15bb8b`）：`add_raw_memory` 的去重归属收窄以 principal 形参为开关
（`lantai/services/memory_service.py:781-786`：principal 非 None 且非 admin 才追加
`user_id == viewer_of(principal) OR IS NULL` 过滤；principal=None → 无过滤）。
三个 caller 两接一漏：

- `lantai/cli/mcp.py:889-891`（handle_raw_add）：✅ principal=_principal_from_params；
- `lantai/services/obsidian_service.py:148-165`（sync_obsidian_note）：✅ 透传 principal；
- **`lantai/api/routes_memory.py:85-90`（add_raw_memory_route）：❌ 只传
  user_id/tenant_id/agent_id，不传 principal=ctx** → REST 直存面全部走
  无归属过滤臂，票 20 的跨属主去重守卫在 REST 是死代码。

危害链：REST `POST /memory/add/raw`（持 key 即达）——A 提交与 B 的 verbatim 行
sha256 相同的文本（读同一篇公开文档即可构造）→ 去重 SELECT 无归属过滤 →
命中 B 的行 → 返回 `{"memory_id": B 的 id, "dedup": True}` → 调用方（obsidian
同步形态）拿此 id `s.get` 到 B 的行，A 的下一次 sync 把 A 的双链实体从 B 的
记忆行连出去。

修法（一行透传，票 79 拍板：接线案）：routes_memory.py 的 add_raw_memory(...)
调用补 `principal=ctx`，与 MCP/obsidian 同口径。禁改 add_raw_memory 签名与
service 语义——principal=None（worker/MCP/obsidian 内部路径）行为逐字零漂移
（raw 家族十二文件回归为凭证）。与票 24 子项 2 的部分唯一索引无交互冲突：
接线后 A 看不见 B 的行、插自己的行，属主不同不撞 (memory_type, key, user_id) 索引。

测试形态：真内存 SQLite（param_env：StaticPool + 真建表 + init_fts +
patch db.get_session），mock 仅外部边界（embed/向量库由 conftest 统一替身）。
去重 SELECT、路由函数、落库全真执行——本文件同时是 add_raw_memory_route 的
不 mock 冒烟测试（AGENTS.md 测试纪律：核心函数冒烟随票落）。端到端臂走
TestClient + dependency_overrides[get_current_user]（tests/test_write_side_ownership.py
:264 先例）——证明 get_current_user → ctx → 路由透传全链（直调绿 ≠ HTTP 面绿，
票 01 同款陷阱）。
"""

from lantai.core.auth import Principal
from lantai.models.schemas import RawMemoryReq
from lantai.models.tables import MemoryItem

# 构造载体：一段"公开文档"文本——A 读同一篇公开文档即可构造出与 B 的 verbatim
# 行 sha256 相同的提交（票 20 危害链的最低成本形态）。内容不含注入模式，
# 过得了写入侧獬豸检测。
_SAME_DOC = "公开运维手册第七节：兰台记忆系统以 SQLite 与 FTS5 承载原文直存。"


def _principal(user_id: str) -> Principal:
    return Principal(tenant_id="t79", user_id=user_id, allowed_lanes=["fact"], role="user")


def _seed_row(content: str, user_id: str | None) -> str:
    """B 侧/存量行走真实写入路径种行（principal=None = 内部写，行落指定属主）。"""
    from lantai.services.memory_service import add_raw_memory

    out = add_raw_memory(RawMemoryReq(content=content, lane="fact"), user_id=user_id)
    assert out["dedup"] is False, f"种行前置失败（意外去重命中）：{out}"
    return out["memory_id"]


def _submit_as_a():
    """A（user-A）经 REST 路由提交同文——直调/护栏各臂共用的提交形状。"""
    from lantai.api.routes_memory import add_raw_memory_route

    return add_raw_memory_route(
        RawMemoryReq(content=_SAME_DOC, lane="fact"), ctx=_principal("user-A")
    )


def _assert_row_owner(session_factory, memory_id: str, user_id: str | None, note: str) -> None:
    """落库面属主断言：行存在且 user_id 相符（直调/HTTP 两臂共用）。"""
    with session_factory() as s:
        row = s.get(MemoryItem, memory_id)
        assert row is not None, f"{note}：行 {memory_id} 不存在"
        assert row.user_id == user_id, f"{note}：属主 {row.user_id!r} != {user_id!r}"


class TestRestRawDedupOwnershipWiring:
    """REST 入口补 principal=ctx 接线：红臂 ×2（现状红）+ 护栏臂 ×3（现状绿）。"""

    def test_direct_route_call_returns_own_row_not_b(self, param_env):
        """红臂 1（决定性，直调形态）：A 直调路由提交与 B 的 verbatim 行 sha256
        相同的文本，不得返回 B 的 memory_id。现状：返回 B 的 id + dedup=True（红）。"""
        session_factory, _ = param_env
        b_id = _seed_row(_SAME_DOC, "user-B")

        out = _submit_as_a()

        assert out["memory_id"] != b_id, (
            f"REST 去重命中 B 的行（返回 {out['memory_id']}，dedup={out.get('dedup')}）"
            "——principal 未透传，票 20 守卫在 REST 是死代码"
        )
        assert out["dedup"] is False, f"A 无既有同主行，应插自己的新行：{out}"
        _assert_row_owner(session_factory, out["memory_id"], "user-A", "A 的新行")

    def test_http_post_add_raw_returns_own_row_not_b(self, param_env):
        """红臂 2（端到端，督导拍板保留）：get_current_user → ctx → 路由透传全链。
        直调绿 ≠ HTTP 面绿（票 01 同款陷阱），TestClient 打真路由面。
        现状：HTTP 面同样返回 B 的 id + dedup=True（红）。"""
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        session_factory, _ = param_env
        b_id = _seed_row(_SAME_DOC, "user-B")

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                resp = c.post("/add/raw", json={"content": _SAME_DOC, "lane": "fact"})
                assert resp.status_code == 200, resp.text
                body = resp.json()
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert body["memory_id"] != b_id, (
            f"HTTP 面去重命中 B 的行（body={body}，B 行 {b_id}）——principal 未透传"
        )
        assert body["dedup"] is False, f"A 无既有同主行，应插自己的新行：{body}"
        _assert_row_owner(session_factory, body["memory_id"], "user-A", "A 的新行")

    def test_own_content_still_dedups_after_wiring(self, param_env):
        """护栏 1：A 提交自己的同文两次 → 第二次 dedup=True 且 id 是 A 自己的行
        （接线不得把去重修废）。现状绿，修后必须仍绿。"""
        first = _submit_as_a()
        assert first["dedup"] is False
        second = _submit_as_a()
        assert second["dedup"] is True, f"同主同文第二次应判重复：{second}"
        assert second["memory_id"] == first["memory_id"], "应收敛到 A 自己的行"

    def test_null_owner_legacy_row_still_dedups(self, param_env):
        """护栏 2：NULL 属主老行（user_id=None）对 A 仍判「重复」不判「新建」
        （add_raw_memory docstring :757-761 的价值观拍板，接线后保持）。
        现状绿，修后必须仍绿。"""
        session_factory, _ = param_env
        legacy_id = _seed_row(_SAME_DOC, None)
        with session_factory() as s:
            row = s.get(MemoryItem, legacy_id)
            assert row is not None and row.user_id is None, "前置：种出 NULL 属主老行"

        out = _submit_as_a()
        assert out["dedup"] is True, f"NULL 属主老行应判重复（价值观拍板）：{out}"
        assert out["memory_id"] == legacy_id, "应命中 NULL 属主老行"

    def test_b_row_untouched_after_a_submit(self, param_env):
        """护栏 3：A 提交后 B 的行原样未动（未被覆写/未被 A 改属主）。
        现状绿，修后必须仍绿。"""
        session_factory, _ = param_env
        b_id = _seed_row(_SAME_DOC, "user-B")
        _submit_as_a()

        with session_factory() as s:
            b_row = s.get(MemoryItem, b_id)
            assert b_row is not None
            assert b_row.user_id == "user-B", f"B 的行属主被改：{b_row.user_id!r}"
            assert b_row.content == _SAME_DOC, "B 的行正文被覆写"
            assert b_row.status == "active", "B 的行状态被改"
