"""LongMemEval-S cleaned 外部锚点评测核心（票 02 步骤④ / 15 天计划票 B）。

纯函数模块（无 I/O 副作用）：数据集加载、题目→导入行映射、预注册抽样、
config_hash、结果→会话映射、会话级 distinct-session recall、覆盖审计、
半题检测、指标聚合。跑测编排（API 单通道灌库/checkpoint/服务器管理）在
scripts/lme_run.py；核心函数不 mock 冒烟见 tests/test_longmemeval_core.py。

口径（票 B 预注册）：
- 主指标 = 会话级 distinct-session recall@2/@5：金标 session（answer_session_ids）
  是否出现在 top-K 检索条目映射回的会话集中（命中任一即 hit）；
- gate 跳过 = miss 计入全题分母；条件召回（分母=过闸题）附报；
- 五题型（+S 集 preference 第六类）分层数字标「描述性」；
- abstention 单列（recall 不定义；cleaned-S 实测无 abstention 题则记 0）；
- 映射保真度审计：每金标 session ≥1 条可检索条目，覆盖率与 recall 同报；
- 诚实边界：英文语料；只测⑤召回段（无 judge 无生成）；不代表全链路。
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path

QUESTION_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)

ABSTENTION_TYPE = "abstention"  # cleaned-S 实测不存在；保留口径位


def load_dataset(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def config_hash(config: dict) -> str:
    """配置指纹：数据集 revision + 检索权重/开关 + 题集。checkpoint 绑定用。"""
    blob = json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def preregistered_ids(data: list[dict], *, seed: int, n: int) -> list[str]:
    """分层抽样（按题型分布定比，票 B），**嵌套可缩**（缩题只减不换）。

    做法：先构造全量分层有序清单（各类题型按占比交错展开，配额用最大余数法
    分配到全量 500 题；组内种子洗牌），任何 n 取前缀——前缀即缩、不换题，
    且任意 n 的抽样仍是近似题型分层。同种子可复现。
    """
    rng = random.Random(seed)
    by_type: dict[str, list[str]] = {t: [] for t in QUESTION_TYPES}
    for q in data:
        t = q["question_type"]
        by_type.setdefault(t, []).append(q["question_id"])
    for t in by_type:
        rng.shuffle(by_type[t])

    # 全量分层有序清单：每题型按步长交错铺位（item j of type t 位次分 =
    # j × total/len(pool)），全局按位次分排序——任意前缀长度都近似题型
    # 占比分层（Bresenham 式），且嵌套（缩题=截前缀，不换题）。
    total = sum(len(v) for v in by_type.values())
    keyed: list[tuple[float, int, str]] = []
    for t in QUESTION_TYPES + tuple(k for k in by_type if k not in QUESTION_TYPES):
        pool = by_type.get(t) or []
        stride = total / len(pool) if pool else 0.0
        for j, qid in enumerate(pool):
            keyed.append((j * stride, QUESTION_TYPES.index(t) if t in QUESTION_TYPES else 99, qid))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [qid for _, _, qid in keyed][:n]


def assert_prereg_subset(prereg_ids: list[str], data: list[dict]) -> None:
    """运行时断言：预注册 id 集 ⊆ 下载数据集 id 集（不满足响亮失败）。"""
    have = {q["question_id"] for q in data}
    missing = [i for i in prereg_ids if i not in have]
    if missing:
        raise RuntimeError(f"预注册 id 不在下载数据集中：{missing[:5]}（拒跑）")


def session_transcript(session: list[dict]) -> str:
    """session（[{role, content}, …]）→ 单条可检索文本（role: content 逐行）。"""
    return "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in session)


def import_lines_for_question(q: dict) -> list[dict]:
    """题 → JSONL 导入行（每 haystack session 一行）。

    - 题级隔离由评测编排实现（scripts/lme_run.py：每题独立 LANTAI_HOME 评测库
      + 正式 API 单通道），故 lane 用默认集合内的 "general"（API 密钥
      allowed_lanes=DEFAULT_LANES，越界 lane 行会被 import ACL 记 errors 不落库）；
    - content 前缀 [LME qid/sid]：映射兜底真源（tags 缺失时解析回会话 id）；
    - tags = [session_id]：检索结果映射回会话集的首选真源；
    - created_at = haystack_dates 同位元素（事务轴如实）。
    """
    qid = q["question_id"]
    lines = []
    for sid, date, session in zip(
        q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]
    ):
        lines.append(
            {
                "content": f"[LME {qid}/{sid}]\n{session_transcript(session)}",
                "created_at": parse_lme_date(date),
                "lane": "general",
                "tags": [sid],
            }
        )
    return lines


def parse_lme_date(s: str) -> str:
    """ "2023/05/20 (Sat) 02:21" → ISO UTC（剔除括注星期后解析；失败回落当天 00:00）。"""
    import re

    cleaned = re.sub(r"\s*\([^)]*\)", "", s).strip()
    try:
        dt = datetime.strptime(cleaned, "%Y/%m/%d %H:%M")
    except ValueError:
        dt = datetime.strptime(cleaned.split(" ")[0], "%Y/%m/%d")
    return dt.isoformat() + "+00:00"


def result_session_ids(results: list[dict]) -> list[str]:
    """检索结果（/search 的 results）→ 会话 id 序列（保序，去重前）。

    映射真源 = memory.tags[0]（导入时钉入的 haystack session id）；tags 缺失
    回落解析 content 前缀 [LME qid/sid]。
    """
    out = []
    for r in results or []:
        mem = r.get("memory") if isinstance(r, dict) else None
        if not isinstance(mem, dict):
            continue
        tags = mem.get("tags") or []
        if tags:
            out.append(str(tags[0]))
            continue
        content = str(mem.get("content", ""))
        if content.startswith("[LME ") and "]" in content:
            out.append(content[5 : content.index("]")].split("/", 1)[-1])
    return out


def session_recall_hit(question: dict, retrieved_sessions: list[str], k: int) -> bool:
    """会话级 distinct-session recall@K：金标会话 ∩ top-K 会话集 ≠ ∅。"""
    gold = set(question["answer_session_ids"])
    topk = set(retrieved_sessions[:k])
    return bool(gold & topk)


def coverage_audit(question: dict, imported_session_ids: list[str]) -> dict:
    """映射保真度审计：每金标 session 是否 ≥1 条可检索条目（我们每 session
    恰灌 1 条 verbatim，故判在场即可；打分前跑，覆盖率与 recall 同报）。"""
    have = set(imported_session_ids)
    gold = list(dict.fromkeys(question["answer_session_ids"]))
    missing = [g for g in gold if g not in have]
    return {
        "question_id": question["question_id"],
        "gold_sessions": len(gold),
        "covered": len(gold) - len(missing),
        "missing": missing,
        "coverage_rate": round((len(gold) - len(missing)) / len(gold), 4) if gold else None,
    }


def half_question_detect(expected_n: int, got_n: int) -> bool:
    """半题检测：应灌 N 实测 M<N 即半题（续跑须清题重灌）。"""
    return got_n < expected_n


def aggregate_metrics(records: list[dict]) -> dict:
    """逐题记录 → 汇总指标。

    records 元素形状：{question_id, question_type, gate_skipped: bool,
    hit_at_2, hit_at_5, coverage_full: bool, abstention: bool, error?: str}
    gate 跳过题：hit=False 计入全题分母；条件召回分母 = 非跳过题。
    """
    n = len(records)
    if n == 0:
        return {"n": 0}
    abst = [r for r in records if r.get("abstention")]
    non_abst = [r for r in records if not r.get("abstention")]
    gate_pass = [r for r in non_abst if not r.get("gate_skipped")]

    def rate(rows: list[dict], key: str):
        return round(sum(1 for r in rows if r.get(key)) / len(rows), 4) if rows else None

    by_type: dict[str, dict] = {}
    for t in QUESTION_TYPES:
        rows = [r for r in non_abst if r["question_type"] == t]
        if rows:
            by_type[t] = {
                "n": len(rows),
                "recall_at_2": rate(rows, "hit_at_2"),
                "recall_at_5": rate(rows, "hit_at_5"),
                "descriptive_only": True,  # 分层小样本，禁作比较（票 B）
            }
    return {
        "n": n,
        "abstention_n": len(abst),
        "gate_skipped_n": sum(1 for r in non_abst if r.get("gate_skipped")),
        "recall_at_2_all": rate(non_abst, "hit_at_2"),  # 跳过=miss 计入分母
        "recall_at_5_all": rate(non_abst, "hit_at_5"),
        "recall_at_2_conditional_gate_pass": rate(gate_pass, "hit_at_2"),
        "recall_at_5_conditional_gate_pass": rate(gate_pass, "hit_at_5"),
        "coverage_full_rate": rate(non_abst, "coverage_full"),  # 审计：金标全覆盖的题占比
        "by_type": by_type,
    }
