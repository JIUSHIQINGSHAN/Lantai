"""确定性评测 embed 单一真源（票 02 前置：杜绝 512/1024 混维）。

sha256 字符 3-gram 计数 → 归一化稀疏向量，512 维、跨进程稳定。
所有评测侧确定性 embed（staged / e2_temporal_cases 等）一律从本模块导入，
不得再各自内联实现。offline.py 的 8 维 mock 从属于本真源（见其注释）。
"""

from __future__ import annotations

import hashlib

EVAL_EMBED_DIM = 512


def embed_for_eval(texts: list[str]) -> list[list[float]]:
    """确定性评测 embed：sha256 3-gram，EVAL_EMBED_DIM 维。"""
    vecs = []
    for text in texts:
        v = [0.0] * EVAL_EMBED_DIM
        grams = [text[i : i + 3] for i in range(max(0, len(text) - 2))]
        for g in grams or [text]:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16)
            v[h % EVAL_EMBED_DIM] += 1.0
        n = sum(v) or 1.0
        vecs.append([x / n for x in v])
    return vecs
