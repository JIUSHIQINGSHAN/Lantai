"""记忆广播链（v0.11 烽燧，借鉴 aiduMEI memory_broadcast /recall_chain 窄版）。

从一条 seed 记忆出发，以它为 query 走既有混合检索（hybrid_search），命中结果
再作为下一层 seed 继续——BFS 逐层传播，呈现「记忆如何触发关联记忆」的有序链。

只读、零 DB 写入、零 LLM 生成（仅复用检索自身的 embedding）；单条搜索失败只
缺该层（宁 miss 不脏写：缺的层如实缺席，不编造关联）。
"""
import math

import jieba

from lantai.retrieval.hybrid import hybrid_search

MAX_CHAIN_DEPTH = 3
CHAIN_BRANCH = 3
CHAIN_MIN_SCORE = 0.3
CHAIN_TOTAL_MAX = 20
SELF_MATCH_THRESHOLD = 0.9


def validate_chain_params(max_depth, branch, min_score, total_max) -> None:
    """链路参数校验（REST/MCP/纯函数共用）：非法值抛 ValueError，不静默修正。

    宁 miss 不脏写：非法参数不自动钳制，由调用方决定 422/拒绝。
    """
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not (1 <= max_depth <= 5):
        raise ValueError("max_depth must be an int in [1, 5]")
    if not isinstance(branch, int) or isinstance(branch, bool) or not (1 <= branch <= 10):
        raise ValueError("branch must be an int in [1, 10]")
    if (not isinstance(min_score, (int, float)) or isinstance(min_score, bool)
            or not (0.0 <= min_score <= 1.0)):
        raise ValueError("min_score must be a float in [0.0, 1.0]")
    if not isinstance(total_max, int) or isinstance(total_max, bool) or not (1 <= total_max <= 50):
        raise ValueError("total_max must be an int in [1, 50]")


def _norm_text(text: str) -> str:
    return " ".join(text.split())


def _text_sim(a: str, b: str) -> float:
    """轻量文本相似度（jieba 词集合余弦；零 LLM、确定性，仅用于自匹配判定）。"""
    ta = set(jieba.lcut(a))
    tb = set(jieba.lcut(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def _is_self_match(mem_text: str, seed: str) -> bool:
    """seed 自身/近重复条目不入选（文本级判定，不依赖向量通道）。"""
    if not mem_text or not seed:
        return False
    if _norm_text(mem_text) == _norm_text(seed):
        return True
    return _text_sim(mem_text, seed) >= SELF_MATCH_THRESHOLD


def build_recall_chain(seed_text: str, max_depth: int = MAX_CHAIN_DEPTH,
                       branch: int = CHAIN_BRANCH, min_score: float = CHAIN_MIN_SCORE,
                       total_max: int = CHAIN_TOTAL_MAX) -> dict:
    """从 seed 出发逐层发现关联记忆，形成广播链（只读，不落库）。

    - 每层：以当前 seed 集逐条调 hybrid_search(top_k=branch*3, use_rerank=False)，
      链内按分数降序取前 branch 条（hybrid 非重排路径顺序依赖 DB 行序，需自排序）；
    - 入选：score >= min_score、非自匹配、id 未见过、总量未达 total_max；
    - 下一层 seed = 本层入选记忆的 content（传播链）；
    - 提前终止：总量达 total_max、某层零入选、或达到 max_depth。
    """
    validate_chain_params(max_depth, branch, min_score, total_max)
    seed_text = (seed_text or "").strip()
    if not seed_text:
        raise ValueError("seed must be a non-empty string")

    chain: list[dict] = []
    seen_ids: set[str] = set()
    total = 0
    current_seeds = [seed_text]

    for depth in range(max_depth):
        next_seeds: list[str] = []
        level_any = False
        for seed in current_seeds:
            seed = (seed or "").strip()
            if not seed:
                continue
            # 传播种子太短（<3 字）跳过；初始 seed 由入口校验非空（2 字中文合法）
            if depth > 0 and len(seed) < 3:
                continue
            try:
                results = hybrid_search(seed, top_k=branch * 3, use_rerank=False)
            except Exception:
                continue  # 单条搜索失败不阻断整链（宁 miss：缺层如实缺席）
            # hybrid 非重排路径返回顺序依赖 DB 行序：链内先按分数降序再取 branch
            results = sorted(results, key=lambda r: -(r.get("score") or 0.0))
            entry_results: list[dict] = []
            added = 0
            for r in results:
                if added >= branch:
                    break
                mem = r.get("memory") or {}
                mid = mem.get("id")
                text = (mem.get("content") or "").strip()
                if not mid or not text or mid in seen_ids:
                    continue
                score = float(r.get("score") or 0.0)
                if score < min_score:
                    continue
                if _is_self_match(text, seed):
                    seen_ids.add(mid)  # 锚点整链排除：自匹配条目不重入（链只见关联）
                    continue
                seen_ids.add(mid)
                total += 1
                added += 1
                level_any = True
                entry_results.append({
                    "id": mid,
                    "memory": text,
                    "score": round(score, 4),
                    "lane": mem.get("lane"),
                })
                next_seeds.append(text)
                if total >= total_max:
                    break
            if entry_results:
                chain.append({"depth": depth, "seed": seed, "results": entry_results})
            if total >= total_max:
                break
        current_seeds = next_seeds
        if not current_seeds or not level_any or total >= total_max:
            break

    return {
        "seed": seed_text,
        "chain": chain,
        "total": total,
        "params": {"max_depth": max_depth, "branch": branch,
                   "min_score": min_score, "total_max": total_max},
        "truncated": total >= total_max,
    }
