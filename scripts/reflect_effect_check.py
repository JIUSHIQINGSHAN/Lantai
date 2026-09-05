"""反思模块效果验证（spec: docs/plans/reflection-module-spec.md 第 7 节）。

场景：中文评测集 superseded 用例（公司域名 / API 密钥）——新值通过 supersedes 边
指向旧值 peer，旧值仍 active 可召回（chinese-memory-v1 实测 superseded_residual_rate
= 1.0，旧值保留在新值之后）。本脚本验证：mock LLM 返回 deprecate 提案 →
run_reflect_once 自动应用 → 旧值 archived → 残留率归零。

诚实标注：
- LLM（curator/rejecter）按测试纪律 mock；DB 建表、FTS5、健康扫描、提案落库、
  自动应用（checkpoint / supersedes 边 / 归档）全部真实执行。
- 检索结果集以「active 过滤后的确定性集合」模拟，不跑混合检索链路——
  hybrid_search 的 active 过滤在 lantai/retrieval/hybrid.py L161/L239/L379，
  旧值退出候选集是 WHERE status='active' 的直接推论。
"""
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.eval.chinese_memory_cases import build_chinese_dataset
from lantai.eval.forgetting_quality import compute_forgetting_metrics
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage.fts import init_fts, sync_fts

_OUT_DIR = _REPO_ROOT / "docs" / "memory-quality"


@contextmanager
def _patch_session(session_factory):
    original = db_module.get_session
    db_module.get_session = session_factory
    try:
        yield
    finally:
        db_module.get_session = original


def _seed_superseded(s, case, now):
    """与 eval/forgetting_quality._seed_case 同构的最小种子（仅 superseded 用例）。"""
    mapping: dict[str, str] = {}
    for i, seed in enumerate(case["seeds"]):
        created = now - timedelta(days=seed.get("created_days_ago", 0))
        mem = MemoryItem(
            id=new_id("mem"),
            memory_type="semantic",
            key=new_id("key"),
            content=seed["content"],
            lane=seed.get("lane", "general"),
            status="active",
            confidence=1.0,
            importance=seed.get("importance", 0.5),
            decay_class=seed.get("decay_class", "episodic"),
            created_at=created,
            updated_at=created,
            last_used_at=created,
        )
        s.add(mem)
        s.flush()  # 同一事务内写 FTS（ADR-0008 强一致）
        sync_fts(s, mem.id, mem.content)
        mapping[str(i)] = mem.id
    for edge in case.get("edges", []):
        s.add(MemoryEdge(
            id=new_id("edge"),
            source_memory_id=mapping[str(edge["source"])],
            target_memory_id=mapping[str(edge["target"])],
            relation="supersedes",
            confidence=1.0,
        ))
    return mapping


def _build_queries(case_maps):
    """构造指标输入：基线（新旧都可召回）与反思后（旧值因 active 过滤退出）。"""
    before: list[dict] = []
    after: list[dict] = []
    for entry in case_maps:
        case = entry["case"]
        m = entry["mapping"]
        target = m[str(case["target"])]
        peer = m[str(case["peer"])]
        base = {
            "category": "superseded",
            "query": case["query"],
            "target_id": target,
            "forbidden_ids": [],
            "preferred_id": target,
            "peer_id": peer,
        }
        before.append({**base, "result_ids": [target, peer]})
        after.append({**base, "result_ids": [target]})
    return before, after


def _run_reflection(case_maps):
    proposals = []
    for entry in case_maps:
        m = entry["mapping"]
        proposals.append({
            "proposal_type": "deprecate",
            "target_memory_id": m["0"],
            "evidence_ids": [m["1"], m["0"]],
            "new_content": "",
            "reason": "旧值已被新值取代（supersedes 边），继续 active 会污染检索",
            "confidence": 0.9,
        })

    def fake_chat_json(sys_prompt, user):
        if "FLAGGED MEMORIES" in user:
            return {"proposals": proposals}
        return {"accept": True, "risk": "low", "reason": "证据核验通过"}

    with patch("lantai.evolution.reflector.chat_json",
               side_effect=fake_chat_json), \
            patch("lantai.evolution.promoter.embed",
                  return_value=[[0.1] * 8]), \
            patch("lantai.retrieval.hybrid.get_vector_store",
                  return_value=Mock(add=Mock(), delete=Mock())):
        from lantai.evolution.reflector import run_reflect_once
        return run_reflect_once()


def _asserts(session_factory, result, case_maps, before, after) -> list[str]:
    issues: list[str] = []

    def _check(cond: bool, msg: str) -> None:
        if not cond:
            issues.append(msg)

    _check(result.get("ok") is True, f"run_reflect_once 未成功: {result}")
    _check(result.get("auto_applied") == 2,
           f"auto_applied 应为 2，实际 {result.get('auto_applied')}")
    _check(result["health_before"]["superseded_active"] == 2,
           f"扫描前 superseded_active 应为 2，实际 {result['health_before']}")
    _check(result["health_after"]["superseded_active"] == 0,
           f"扫描后 superseded_active 应为 0，实际 {result['health_after']}")
    _check(before["superseded_residual_rate"] == 1.0,
           "基线残留率应为 1.0")
    _check(after["superseded_residual_rate"] == 0.0,
           "反思后残留率应归零")
    with session_factory() as s:
        for entry in case_maps:
            m = entry["mapping"]
            old = s.get(MemoryItem, m["0"])
            new = s.get(MemoryItem, m["1"])
            _check(old is not None and old.status == "archived",
                   f"旧值 {m['0']} 应 archived，实际 {old.status if old else None}")
            _check(new is not None and new.status == "active",
                   f"新值 {m['1']} 应保持 active")
            edge = s.exec(select(MemoryEdge).where(
                MemoryEdge.relation == "supersedes",
                MemoryEdge.source_memory_id == m["1"],
                MemoryEdge.target_memory_id == m["0"])).first()
            _check(edge is not None, "supersedes 边应保留")
    return issues


def _render(case_maps, result, before, after, issues) -> str:
    rows = []
    for entry in case_maps:
        rows.append(f"| {entry['case']['query']} | 0.9(deprecate) | "
                    f"{entry['case']['seeds'][0]['content']} | archived |")
    lines = [
        "# 反思模块效果验证报告（superseded 残留率归零）",
        "",
        f"> 生成时间：{utcnow().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "> 场景：中文评测集 superseded 两例（公司域名 / API 密钥），新值 supersedes 旧值，",
        "> 旧值仍 active 可召回（基线残留率 1.0）。",
        "> 方法：LLM 按测试纪律 mock（curator 返回 deprecate 提案、rejecter 返回 accept/low），",
        "> DB/FTS5/健康扫描/提案落库/自动应用全部真实执行；检索以 active 过滤后的",
        "> 确定性结果集模拟（hybrid_search active 过滤见 hybrid.py L161/L239/L379）。",
        "",
        "## 反思前（基线）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| superseded_active（健康扫描） | {result['health_before']['superseded_active']} |",
        f"| superseded_residual_rate | {before['superseded_residual_rate']} |",
        f"| superseded_order_accuracy | {before['superseded_order_accuracy']} |",
        "",
        "## 反思后",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| superseded_active（健康扫描） | {result['health_after']['superseded_active']} |",
        f"| superseded_residual_rate | {after['superseded_residual_rate']} |",
        f"| superseded_order_accuracy | {after['superseded_order_accuracy']} |",
        f"| 提案数 / 自动应用 / 待审 / 丢弃 | {result['proposals_created']} / "
        f"{result['auto_applied']} / {result['pending']} / {result['discarded']} |",
        "",
        "| 用例 | 提案 | 旧值 | 处理后状态 |",
        "|---|---|---|---|",
        *rows,
        "",
        "## 结论",
        "",
    ]
    if not issues:
        lines.append("**通过**：反思自动应用 deprecate 后，被取代旧值退出 active 集合，"
                     "残留率从 1.0 归零；新值保持 active，supersedes 边保留，"
                     "健康快照 superseded_active 2 → 0（闭环自证）。")
    else:
        lines.append("**未通过**：")
        for msg in issues:
            lines.append(f"- {msg}")
    lines += [
        "",
        "> 诚实标注：本验证不跑混合检索链路，残留率归零是 active 过滤的直接推论；",
        "> 若未来检索改为不依赖 status 过滤，需以真实链路复测。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    dataset = build_chinese_dataset()
    superseded = [c for c in dataset["cases"] if c["category"] == "superseded"]
    now = utcnow()

    with _patch_session(session_factory):
        with session_factory() as s:
            case_maps = []
            for case in superseded:
                case_maps.append({"case": case,
                                  "mapping": _seed_superseded(s, case, now)})
            s.commit()

        per_query_before, per_query_after = _build_queries(case_maps)
        metrics_before = compute_forgetting_metrics(per_query_before)
        result = _run_reflection(case_maps)
        metrics_after = compute_forgetting_metrics(per_query_after)
        issues = _asserts(session_factory, result, case_maps,
                          metrics_before, metrics_after)

        report = _render(case_maps, result, metrics_before, metrics_after, issues)
        print(report)
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / f"reflect-effect-{now.date().isoformat()}.md"
        path.write_text(report, encoding="utf-8")
        print(f"\n报告已写入: {path}", file=sys.stderr)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
