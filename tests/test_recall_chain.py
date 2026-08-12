"""记忆广播链（烽燧，v0.11）测试。

validate_chain_params / build_recall_chain 直调不 mock 内部计算：检索走真实
SQLite+FTS + 本地 ngram 嵌入 + 假向量库（仅替换外部网络 embedding 与向量存储），
BFS 逐层展开 / 去重 / 自匹配排除 / 总量封顶 全部真实执行。
"""
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, sync_fts


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LocalEmbedder:
    """本地 bigram 词袋嵌入（测试替身）：确定性、零外部，仅替换 embedding API。"""

    def __init__(self, corpus):
        self.vocab = sorted({t[i:i + 2] for t in corpus for i in range(len(t) - 1)})
        self.dim = max(1, len(self.vocab))

    def embed(self, queries):
        vecs = []
        for q in queries:
            v = [0.0] * self.dim
            for i in range(len(q) - 1):
                g = q[i:i + 2]
                if g in self.vocab:
                    v[self.vocab.index(g)] += 1
            norm = sum(v) or 1.0
            vecs.append([x / norm for x in v])
        return vecs


class FakeVectorStore:
    """假向量库：归一化向量点积排序（距离 = 1 - 相似度，与 Chroma 语义一致）。"""

    def __init__(self, embedder, rows):
        self._vectors = {mid: embedder.embed([c])[0] for mid, c in rows}

    def search(self, query_embedding, top_k):
        scored = [(mid, sum(a * b for a, b in zip(query_embedding, v)))
                  for mid, v in self._vectors.items()]
        scored.sort(key=lambda x: -x[1])
        return [{"id": mid, "distance": 1.0 - s} for mid, s in scored[:top_k]]

    def add(self, *args, **kwargs):
        pass

    def delete(self, *args, **kwargs):
        pass


class _StoreProxy:
    """稳定补丁目标：search 动态转发到 harness 当前 store（seed 重建后仍有效）。"""

    def __init__(self, harness):
        self._h = harness

    def search(self, query_embedding, top_k):
        return self._h.store.search(query_embedding, top_k)

    def add(self, *args, **kwargs):
        pass

    def delete(self, *args, **kwargs):
        pass


class ChainHarness:
    """种子库 + 检索替身：patch hybrid 命名空间内的 embed / get_vector_store。"""

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.rows = []
        self.embedder = LocalEmbedder([])
        self.store = FakeVectorStore(self.embedder, [])

    def seed(self, contents):
        ids = []
        with self.session_factory() as s:
            for c in contents:
                m = MemoryItem(
                    id=uuid.uuid4().hex, memory_type="general", key=c[:48],
                    content=c, lane="general", status="active",
                    decay_class="episodic", decay_score=0.9,
                    created_at=_utcnow(), updated_at=_utcnow())
                s.add(m)
                s.flush()
                sync_fts(s, m.id, c)
                ids.append(m.id)
            s.commit()
        self.rows.extend(zip(ids, contents))
        self._rebuild()
        return ids

    def _rebuild(self):
        corpus = [c for _, c in self.rows]
        self.embedder = LocalEmbedder(corpus)
        self.store = FakeVectorStore(self.embedder, self.rows)

    def _embed(self, queries):
        """委托当前 embedder（seed() 重建后补丁仍指向最新对象）。"""
        return self.embedder.embed(queries)

    def _store_proxy(self):
        return _StoreProxy(self)

    def __enter__(self):
        os.environ["LANTAI_INTENT_OFF"] = "1"  # 意图分类确定性（不调 LLM）
        self._patches = [
            patch.object(db_module, "get_session", self.session_factory),
            patch("lantai.retrieval.hybrid.embed", side_effect=self._embed),
            patch("lantai.retrieval.hybrid.get_vector_store", return_value=self._store_proxy()),
            patch("lantai.storage.vector_store.get_vector_store", return_value=self._store_proxy()),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        os.environ.pop("LANTAI_INTENT_OFF", None)


@pytest.fixture()
def chain_env():
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    with ChainHarness(session_factory) as h:
        yield h


# ── 纯函数：参数校验（不 mock）────────

def test_validate_chain_params_pure():
    from lantai.ops.recall_chain import validate_chain_params
    validate_chain_params(3, 3, 0.3, 20)  # 默认值合法
    bad = [
        (0, 3, 0.3, 20), (6, 3, 0.3, 20), (3, 0, 0.3, 20), (3, 11, 0.3, 20),
        (3, 3, -0.1, 20), (3, 3, 1.1, 20), (3, 3, 0.3, 0), (3, 3, 0.3, 51),
    ]
    for args in bad:
        with pytest.raises(ValueError):
            validate_chain_params(*args)


def test_build_recall_chain_empty_seed_rejected():
    from lantai.ops.recall_chain import build_recall_chain
    with pytest.raises(ValueError, match="non-empty"):
        build_recall_chain("   ")


# ── 全链路（真实 SQLite+FTS + 本地嵌入，BFS 真实执行）────────

def test_build_recall_chain_empty_db(chain_env):
    from lantai.ops.recall_chain import build_recall_chain
    out = build_recall_chain("苹果水果", max_depth=2)
    assert out["total"] == 0
    assert out["chain"] == []
    assert out["truncated"] is False


def test_build_recall_chain_expands_levels(chain_env):
    """多跳传播：seed → 直接相关 → 经内容接力到更远关联；无关记忆不入链。"""
    from lantai.ops.recall_chain import build_recall_chain
    mids = chain_env.seed([
        "苹果是一种水果",
        "水果需要冷藏保存",
        "冷藏保存延长保质期",
        "Python 编程语言教程",
        "烘焙苹果派需要面粉",
    ])
    m1, m2, m3, m4, m5 = mids
    out = build_recall_chain("苹果", max_depth=3, branch=3, min_score=0.1)
    all_ids = [r["id"] for e in out["chain"] for r in e["results"]]
    assert m4 not in all_ids                        # 无关记忆不入选
    assert len(all_ids) == len(set(all_ids))        # 跨层去重
    assert out["total"] == len(all_ids)
    level0_ids = [r["id"] for e in out["chain"] if e["depth"] == 0
                  for r in e["results"]]
    assert m1 in level0_ids and m5 in level0_ids    # seed 直接相关入选
    assert m3 in all_ids                            # 多跳：经 m2 内容接力到 m3
    assert max(e["depth"] for e in out["chain"]) >= 1
    assert all(r["score"] >= 0.05 for e in out["chain"] for r in e["results"])


def test_build_recall_chain_self_match_excluded(chain_env):
    """seed 内容本身就是某条记忆 → 该条不入选（自匹配排除），关联仍入选。"""
    from lantai.ops.recall_chain import build_recall_chain
    mids = chain_env.seed([
        "苹果是一种水果",
        "水果需要冷藏保存",
        "Python 编程语言教程",
    ])
    m1, m2, m4 = mids
    out = build_recall_chain("苹果是一种水果", max_depth=2, branch=3, min_score=0.1)
    all_ids = [r["id"] for e in out["chain"] for r in e["results"]]
    assert m1 not in all_ids
    assert m2 in all_ids
    assert m4 not in all_ids


def test_build_recall_chain_total_cap(chain_env):
    """总量封顶：total_max 截断 + truncated 标记。"""
    from lantai.ops.recall_chain import build_recall_chain
    chain_env.seed([
        "苹果是一种水果",
        "水果需要冷藏保存",
        "冷藏保存延长保质期",
    ])
    out = build_recall_chain("苹果", max_depth=3, branch=3,
                             min_score=0.1, total_max=2)
    assert out["total"] == 2
    assert out["truncated"] is True
    assert sum(len(e["results"]) for e in out["chain"]) == 2


def test_build_recall_chain_min_score_filters(chain_env):
    """低相关（分数 < min_score）条目被过滤：0.99 门槛下空链。"""
    from lantai.ops.recall_chain import build_recall_chain
    chain_env.seed([
        "苹果是一种水果",
        "水果需要冷藏保存",
    ])
    out = build_recall_chain("苹果", max_depth=2, branch=3, min_score=0.99)
    assert out["total"] == 0
    assert out["chain"] == []
