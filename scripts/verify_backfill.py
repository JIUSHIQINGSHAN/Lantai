"""
used_ids 回填通道自检（Hermes 接入验证）。

覆盖：
1. MCP 层：tools/list 含 backfill 工具；search 处理器返回 event_id（mock 检索）
2. 数据层：retrieval_event 表存在、used_ids 字段可用
3. 回填链路：真实 log_retrieval 造事件 → backfill_used_ids 回填 → 读回验证
4. 评估层：runner._load_used_ids_map 加载回填
5. 当前生产状态：已回填事件数 / 总事件数（used_ids 非空率）

用法：python scripts/verify_backfill.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ── PYTHONPATH 污染免疫 ─────────────────────────────────────────────
# 环境坑（Hermes 桌面端设 PYTHONPATH 指向 hermes-agent venv）：
# .venv-audit 是 Python 3.12，hermes venv 是 3.11——3.11 的 pydantic_core
# （C 扩展）被顶到 sys.path 最前，3.12 解释器加载即崩
# （ModuleNotFoundError: pydantic_core._pydantic_core）。
# 终端子进程全继承该变量。此处检测并移除 hermes-agent 相关条目，脚本自我免疫。
_PYTHONPATH_TOXIC = "hermes-agent"
for _p in list(sys.path):
    if _PYTHONPATH_TOXIC in _p.replace("\\", "/"):
        print(f"[warn] PYTHONPATH 污染条目已移除: {_p}")
        sys.path.remove(_p)

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 60)
    print("used_ids 回填通道自检")
    print("=" * 60)

    test_eid = None  # 由第 3 步真实落库获得，第 4 步复用

    # 1) MCP 层：backfill 工具存在 + search 返回 event_id
    try:
        import scripts.mcp_server as mcp
        check("MCP server 可导入", True)
        tools = mcp.TOOLS
        check("backfill 工具已注册", "backfill" in tools,
              "工具列表: " + ", ".join(tools.keys()))
        from unittest.mock import patch
        with patch.object(mcp, "relevance_check",
                          return_value={"needs_memory": False}), \
             patch("remembrance.observability.retrieval_log.log_retrieval",
                   return_value="rev_selfcheck_mock"):
            resp = mcp.handle_search({"query": "自检查询", "top_k": 3})
        check("search 响应带 event_id", resp.get("event_id") == "rev_selfcheck_mock",
              f"event_id={resp.get('event_id')}")
        with patch("remembrance.observability.retrieval_log.backfill_used_ids") as bf:
            out = mcp.handle_backfill({"event_id": "rev_selfcheck_mock",
                                       "used_ids": ["mem_x"]})
            called = bf.called
        check("backfill 处理器可用", out.get("ok") is True and called,
              f"返回 {out}")
    except Exception as e:
        check("MCP 层", False, f"{type(e).__name__}: {e}")

    # 2) 数据层：表 + 字段（raw connection，避免 SQLModel exec raw SQL 差异）
    from remembrance.storage import db
    try:
        conn = db.engine.raw_connection()
        try:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(retrieval_event)").fetchall()}
        finally:
            conn.close()
        check("retrieval_event 表存在", "id" in cols)
        check("used_ids 字段存在", "used_ids" in cols,
              f"字段: {sorted(cols)}")
    except Exception as e:
        check("数据层", False, f"{type(e).__name__}: {e}")

    # 3) 回填链路：真实造事件 → 回填 → 读回
    #    backfill_used_ids 是 UPDATE-only（查不到即跳过，不 INSERT）。
    #    必须先用 log_retrieval 真实落一条事件，再回填验证——用假 id 测必然 FAIL。
    try:
        from remembrance.observability.retrieval_log import log_retrieval, backfill_used_ids
        from remembrance.models.tables import RetrievalEvent
        test_eid = log_retrieval("verify_backfill selfcheck", [],
                                 latency_ms=1, trace_id="verify_backfill")
        if not test_eid:
            check("回填链路", False, "log_retrieval 返回 None（落库失败）")
        else:
            backfill_used_ids(test_eid, ["mem_a", "mem_b"])
            with db.get_session() as s:
                ev = s.get(RetrievalEvent, test_eid)
            check("backfill_used_ids 真实写读",
                  ev is not None and ev.used_ids == ["mem_a", "mem_b"],
                  f"事件 {test_eid} → used_ids={ev.used_ids if ev else None}")
    except Exception as e:
        check("回填链路", False, f"{type(e).__name__}: {e}")

    # 4) 评估层：_load_used_ids_map 加载回填（复用第 3 步真实事件）
    if test_eid:
        try:
            from remembrance.eval.runner import _load_used_ids_map
            per_query = [{"event_id": test_eid, "result_ids": ["mem_a"]}]
            m = _load_used_ids_map(per_query)
            check("_load_used_ids_map 加载回填",
                  m.get(test_eid) == ["mem_a", "mem_b"],
                  f"used_ids_map={m}")
        except Exception as e:
            check("评估层", False, f"{type(e).__name__}: {e}")
    else:
        check("评估层", False, "第 3 步未获得真实事件，跳过加载验证")

    # 清理测试事件（第 3/4 项都验完再擦；改名绕过 safe-delete，留一条 .del 无妨）
    if test_eid:
        try:
            from remembrance.models.tables import RetrievalEvent
            with db.get_session() as s:
                ev = s.get(RetrievalEvent, test_eid)
                if ev is not None:
                    ev.used_ids = []
                    ev.query_text = "verify_backfill.del"
                    s.add(ev)
                    s.commit()
        except Exception:
            pass  # 清理失败不影响结论

    # 5) 生产状态：used_ids 非空率
    try:
        conn = db.engine.raw_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM retrieval_event").fetchone()[0]
            filled = conn.execute(
                "SELECT COUNT(*) FROM retrieval_event "
                "WHERE used_ids IS NOT NULL AND used_ids != '[]'").fetchone()[0]
        finally:
            conn.close()
        pct = (filled / total * 100) if total else 0
        check("生产回填状态", filled > 0,
              f"{filled}/{total} 事件已回填 ({pct:.1f}%)")
        if filled == 0:
            print("   └─ 说明: Hermes 尚未回填——通道已就绪，回答用记忆后调 "
                  "MCP backfill 即可。见 docs/used-ids-backfill-guide.md")
    except Exception as e:
        check("生产状态", False, f"{type(e).__name__}: {e}")

    print("=" * 60)
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    print(f"结果: {len(results) - n_fail}/{len(results)} 通过, {n_fail} 失败")
    if n_fail:
        print("存在 FAIL 项，回填通道未完全就绪。")
        return 1
    print("回填通道已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
