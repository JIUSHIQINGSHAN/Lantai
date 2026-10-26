"""E1 阶段化评测 CLI（票 03 交付 3）：输出每段一行报告。

用法：
    PYTHONPATH=. python scripts/run_staged_eval.py            # 跑六段回放并打印
    PYTHONPATH=. python scripts/run_staged_eval.py --latest   # 打印最近一次 staged 结果

不 mock 边界：CLI 直连部署库与真实 embed/向量库（生产路径）。
"""

import sys

from lantai.eval.deterministic_embed import EVAL_EMBED_DIM
from lantai.eval.staged import format_staged_report, run_staged_eval


def _existing_vector_dim() -> int | None:
    """部署向量库已有向量的维度；空库返回 None。"""
    from lantai.storage.vector_store import get_vector_store

    vs = get_vector_store()
    col = getattr(vs, "_collection", None)
    if col is None:
        return None
    got = col.get(include=["embeddings"], limit=1)
    embs = (got or {}).get("embeddings") or []
    if not embs:
        return None
    return len(embs[0])


def _preflight() -> str | None:
    """维度一致性断言：写入侧固定 512 维确定性 embed（staged.py ④ 段），

    库内已有向量若异维（如生产 1024 维），混写会污染部署 collection /
    触发 Chroma 维度冲突——直接拒跑；空库放行。
    另注：⑤ 召回段走生产 hybrid_search（生产 embed，可能 1024 维），与
    512 维写入语料天然不同维，向量通道 Hit@K 不可比属已知边界（spec §1.2）。
    """
    try:
        dim = _existing_vector_dim()
    except Exception as exc:  # noqa: BLE001 —— 预检失败同样拒跑（宁 miss 不脏写）
        return f"向量库预检失败：{exc!r}"
    if dim is not None and dim != EVAL_EMBED_DIM:
        return (
            f"拒跑：部署向量库已有 {dim} 维向量，而本评测写入侧固定 "
            f"{EVAL_EMBED_DIM} 维确定性 embed——混维风险。"
            "请改用评测专用空库（隔离 CHROMADB_PATH）后重跑。"
        )
    return None


def main() -> int:
    if "--latest" not in sys.argv:
        reason = _preflight()
        if reason:
            print(reason, file=sys.stderr)
            return 2
    result = run_staged_eval()
    print(format_staged_report(result))

    anchors_ok = all(a["achieved"] for a in result["anchors"])
    stages_complete = len(result["stages"]) == 6 and all(
        "samples" in st and "success_rate" in st for st in result["stages"]
    )
    print()
    print(f"六段齐全: {stages_complete} | 预埋锚点全归位: {anchors_ok}")
    return 0 if (anchors_ok and stages_complete) else 1


if __name__ == "__main__":
    sys.exit(main())
