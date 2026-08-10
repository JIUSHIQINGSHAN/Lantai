import numpy as np
from lantai.llm.client import embed


def cos(a, b):
    a, b = np.array(a), np.array(b)
    if not a.any() or not b.any():
        return 0.0
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def novelty_score(candidate_text: str, existing_texts: list[str]) -> float:
    if not existing_texts:
        return 1.0
    vs = embed([candidate_text] + existing_texts)
    q, others = vs[0], vs[1:]
    max_sim = max(cos(q, o) for o in others)
    return 1.0 - max_sim
