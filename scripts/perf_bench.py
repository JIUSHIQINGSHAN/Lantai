"""兰台性能基线工具（离线、函数级、可重复）——v0.68 并行流 D

与 `perf_baseline.py`（HTTP 端到端，需活服务）不同：本工具自建临时 SQLite
（真实建表 + 迁移 + FTS5），按规模灌入种子化合成语料，**进程内直调**核心函数
计时，并可选 cProfile 热点排查。产出 JSON + Markdown 双格式基线报告。

口径纪律（票据 `.scratch/v068-perf/issues/01`）：
- 只测**本地管线**：LLM（chat_json/embed 网络部分）是外部服务面，用确定性
  替身并单独计量，不与本地管线混账；向量库用生产同款内嵌 ChromaDB（HNSW），
  本环境装不上时自动降级为纯 Python 替身并在报告里声明。
- 环境隔离：临时库 + 临时 LANTAI_HOME，绝不读写宿主真实开发库；
  OPENAI_BASE_URL 强制指向本机死端口，漏网调用快速失败而非真连网。
- 数据可重复：固定随机种子，同机同版本下数字可复现。
- 全部 patch 在上下文离开时还原——harness 也被冒烟测试以 import 方式复用，
  不许把生产单例改脏（tests/conftest 的绊线会抓）。

用法：
    python scripts/perf_bench.py                    # 全场景，默认两档规模
    python scripts/perf_bench.py --scale 2000       # 单档规模
    python scripts/perf_bench.py --profile          # 附带 cProfile 热点（原始 .pstats 落 --out 目录）
    python scripts/perf_bench.py --only hybrid_search --iterations 30

注意：请用带 chromadb 的 py3.11 虚拟环境运行（本工作树为 .venv311；主仓 .venv
与工作树根 .venv 都没有 chromadb——用错环境向量库会静默降级纯 Python 替身，
数字对不上基线）；宿主若有 PYTHONPATH 指向其他环境，请先清空（hermes 宿主的
PYTHONPATH 会把 py3.14 的 pydantic 混进来，import 即炸）。
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import os
import pstats
import random
import re
import shutil
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

EMBED_DIM = 128


def _apply_llm_env_guards() -> dict[str, str | None]:
    """LLM 直连三道闸（bench_env 进入时施加、离开时按原值还原）。

    必须在 bench_env 内、任何 lantai import 之前生效（llm/client.py 模块级
    OpenAI()）。**不许在模块 import 时写**：本模块被冒烟测试以 import 方式
    加载，进程级 env 写入会泄漏进同一 pytest 会话的后续测试（实测把
    test_reranker 的意图分类全量切到 OFF 分支，2 例连坐）。
    """
    saved = {
        k: os.environ.get(k) for k in ("LANTAI_INTENT_OFF", "OPENAI_API_KEY", "OPENAI_BASE_URL")
    }
    os.environ.setdefault("LANTAI_INTENT_OFF", "1")  # 意图分类走本地规则
    os.environ.setdefault("OPENAI_API_KEY", "perf-bench-local-only")
    os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"  # 漏网调用死端口快速失败
    return saved


def _restore_llm_env_guards(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── 确定性假嵌入 ────────────────────────────────────────────────
# token（CJK 二元组 + ASCII 整词）哈希单位向量求和归一：共享 token 越多
# 余弦越高，使向量通道召回与 FTS 呈合理正相关、查询-原文近邻能通过
# hybrid 的 distance<0.8 截断，而无关对 distance≈1 被滤掉——不这样设计，
# 向量通道在真 HNSW 下会全数落空，合并路径就测不到。
def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in re.sub(r"[^\w一-龥]+", " ", str(text)).split():
        if all(not ("一" <= ch <= "鿿") for ch in word):
            if len(word) >= 2:
                tokens.append(word.lower())
            continue
        if len(word) <= 2:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def fake_embed(texts, timeout=None):
    out = []
    for t in texts:
        vec = [0.0] * EMBED_DIM
        for token in _tokenize(t):
            h = int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "big")
            vec[h % EMBED_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


# ── 纯 Python 向量替身（ChromaDB 不可用时兜底）──────────────────
def _match_condition(meta: dict, cond: dict) -> bool:
    for k, v in cond.items():
        if k == "$or":
            if not any(_match_condition(meta, c) for c in v):
                return False
        elif k == "$and":
            if not all(_match_condition(meta, c) for c in v):
                return False
        elif isinstance(v, dict) and "$in" in v:
            if meta.get(k) not in v["$in"]:
                return False
        elif meta.get(k) != v:
            return False
    return True


class HashVectorStore:
    """确定性向量替身：进程内暴力余弦，接口与 ChromaVectorStore 对齐。"""

    def __init__(self):
        self._vectors: dict[str, list[float]] = {}
        self._meta: dict[str, dict] = {}

    def add(self, ids, embeddings, metadatas):
        for i, e, m in zip(ids, embeddings, metadatas):
            self._vectors[i] = list(e)
            self._meta[i] = dict(m or {})

    def update(self, ids, embeddings, metadatas):
        self.add(ids, embeddings, metadatas)

    def delete(self, ids):
        for i in ids:
            self._vectors.pop(i, None)
            self._meta.pop(i, None)

    def has(self, id) -> bool:  # noqa: A002 - 对齐上游接口
        return id in self._vectors

    def count(self) -> int:
        return len(self._vectors)

    def search(self, query_embedding, top_k, filters=None):
        q = query_embedding[0] if isinstance(query_embedding[0], list) else query_embedding
        scored = []
        for mid, vec in self._vectors.items():
            if filters and not _match_condition(self._meta.get(mid, {}), filters):
                continue
            dist = 1.0 - sum(x * y for x, y in zip(q, vec))
            scored.append((mid, dist))
        scored.sort(key=lambda x: x[1])
        return [
            {"id": mid, "distance": dist, "metadata": self._meta.get(mid, {})}
            for mid, dist in scored[:top_k]
        ]


# ── 合成语料（种子化，确定性）─────────────────────────────────
_PROJECTS = ["兰台", "星舰", "青梧", "白泽", "烛龙", "墨匣", "沧溟", "观星"]
_STACKS = ["SQLite", "FTS5", "ChromaDB", "FastAPI", "SQLModel", "Redis", "PostgreSQL", "Docker"]
_TOPICS = [
    "检索延迟",
    "候选闸门",
    "潮波合并",
    "衰减曲线",
    "注入回执",
    "分词精度",
    "去重聚类",
    "卷宗聚合",
]
_PREFERENCES = [
    "回答偏简短，先结论后论据",
    "代码注释用中文，标识符用英文",
    "方案对比时先给推荐项再列备选",
    "不做没有票据依据的重构决策",
    "周例会固定安排在周三上午十点",
    "偏好用 pytest 而非 unittest 框架",
]
_RULES = [
    "提交前必须通过 release_check 门禁检查",
    "核心函数改动必须补不 mock 的冒烟测试",
    "临时实验文件一律放 scratch 目录暂存",
    "数据库迁移必须走 user_version 增量链",
    "禁止在主分支上直接执行 push 操作",
]
_FACTS = [
    "{proj}项目的检索服务部署在{stack}集群上运行",
    "{proj}项目使用{stack}存储全部向量索引数据",
    "{proj}项目{topic}专题的负责人是{name}",
    "{proj}项目在{date}完成了{topic}专项评审",
    "{stack}集群的生产版本是v{ver}，去年上线",
    "{proj}项目的{topic}指标基线是{num}毫秒",
]
_EXPERIENCES = [
    "排查{proj}项目{topic}问题时，先看检索事件表再查应用日志",
    "上次{proj}项目{topic}故障的根因是数据库连接池耗尽",
    "给{proj}项目做{topic}优化时，先建基线再动手调参数",
    "{topic}相关工单通常在周内会重开一次，注意留存上下文",
]
_CHAT = [
    "今天{name}提到{proj}项目整体进展比较顺利",
    "早上站会讨论了{topic}的几个边界情况处理",
    "会议纪要：{topic}相关结论下周继续跟进落实",
]


def _corpus_row(rng: random.Random, idx: int) -> dict:
    r = rng.random()
    if r < 0.40:
        lane, tpl = "fact", rng.choice(_FACTS)
    elif r < 0.55:
        lane, tpl = "rule", rng.choice(_RULES)
    elif r < 0.65:
        lane, tpl = "preference", rng.choice(_PREFERENCES)
    elif r < 0.85:
        lane, tpl = "experience", rng.choice(_EXPERIENCES)
    elif r < 0.95:
        lane, tpl = "chat", rng.choice(_CHAT)
    else:
        lane, tpl = "general", rng.choice(_FACTS)
    content = tpl.format(
        proj=rng.choice(_PROJECTS),
        stack=rng.choice(_STACKS),
        topic=rng.choice(_TOPICS),
        name=rng.choice(["老周", "小陈", "阿禾", "林工"]),
        date=f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}",
        ver=rng.randint(10, 40),
        num=rng.randint(20, 900),
    )
    if lane in ("fact", "rule"):
        decay_class = "procedural" if rng.random() < 0.3 else "semantic"
    elif lane == "chat":
        decay_class = "episodic"
    else:
        decay_class = rng.choice(["episodic", "semantic"])
    created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=idx)
    return {
        "id": f"mi_{idx:08d}",
        "content": content,
        "lane": lane,
        "domain": "user" if rng.random() < 0.9 else "session",
        "memory_type": "verbatim" if rng.random() < 0.02 else "text",
        "tier": "longterm" if rng.random() < 0.7 else "working",
        "importance": round(rng.uniform(0.1, 0.95), 2),
        "confidence": round(rng.uniform(0.5, 1.0), 2),
        "status": "archived" if rng.random() < 0.05 else "active",
        "use_count": rng.choice([0, 0, 0, 1, 3, 7]),
        "injection_count": rng.choice([0, 0, 1, 2, 5, 11]),
        "decay_class": decay_class,
        "user_id": "default",
        "created_at": created,
        "updated_at": created,
        "valid_from": created,
        "last_used_at": created + timedelta(days=rng.randint(0, 30)),
    }


def build_corpus(n: int, seed: int = 20261107) -> list[dict]:
    rng = random.Random(seed)
    return [_corpus_row(rng, i) for i in range(n)]


_MISS_QUERIES = [
    "量子纠缠态的超导曲率引擎维护手册全文",
    "西西弗斯推石头效率评估年度总结报告",
    "月光族理财防剁手十讲完整课程目录",
    "深潜马里亚纳海沟的快递配送方案细节",
    "给火星温室土豆浇水的最佳时刻表安排",
]


def build_query_set(rows: list[dict], n_hit: int = 20, n_miss: int = 5, seed: int = 7):
    """hit：语料原文（≥15 汉字保证过闸门 rule 6）；miss：语料外探空 query。"""
    rng = random.Random(seed)
    active = [r["content"] for r in rows if r["status"] == "active" and len(r["content"]) >= 15]
    hits = rng.sample(active, min(n_hit, len(active)))
    return hits, _MISS_QUERIES[:n_miss]


# ── 环境装配 ──────────────────────────────────────────────────
class BenchContext:
    def __init__(self, engine, session_factory, vector_store, backend, home: Path, rows):
        self.engine = engine
        self.session_factory = session_factory
        self.vector_store = vector_store
        self.vector_backend = backend
        self.home = home
        self.rows = rows
        from lantai.core.acl import Principal

        self.default_principal = Principal(role="user", user_id="default")
        self.hit_queries, self.miss_queries = build_query_set(rows)
        self.shell_hook = None
        self.sanity = {}


@contextmanager
def bench_env(n_rows: int, seed: int = 20261107, workdir: Path | None = None):
    """隔离基线环境：临时库 + 临时 LANTAI_HOME + 确定性替身，离开时全部还原。"""
    saved_env = _apply_llm_env_guards()
    try:
        yield from _bench_env_inner(n_rows, seed=seed, workdir=workdir)
    finally:
        _restore_llm_env_guards(saved_env)


def _bench_env_inner(n_rows: int, seed: int, workdir: Path | None):
    from sqlmodel import Session, SQLModel, create_engine

    home = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="lantai-perf-"))
    home.mkdir(parents=True, exist_ok=True)
    db_file = home / "bench.db"
    db_file.touch()

    from lantai.core.settings import settings

    saved = {
        "LANTAI_HOME": settings.LANTAI_HOME,
        "DATABASE_URL": settings.DATABASE_URL,
        "CHROMADB_PATH": settings.CHROMADB_PATH,
        "COALESCE_ENABLED": settings.COALESCE_ENABLED,
        "RERANKER_ENABLED": settings.RERANKER_ENABLED,
    }
    settings.LANTAI_HOME = str(home)
    settings.DATABASE_URL = f"sqlite:///{db_file.as_posix()}"
    settings.CHROMADB_PATH = str(home / ".chromadb")
    settings.COALESCE_ENABLED = False  # 摄取走确定性同步路径（缓冲冲刷属另一票口径）
    settings.RERANKER_ENABLED = False  # rerank 是外部服务（v0.15.2 基线已单列）

    import lantai.models.tables  # noqa: F401 注册全部表
    import lantai.parameters.trust_models  # noqa: F401
    import lantai.storage.db as db_module
    from lantai.storage.db import apply_migrations
    from lantai.storage.fts import init_fts

    engine = create_engine(settings.DATABASE_URL, echo=False, connect_args={"timeout": 30})
    SQLModel.metadata.create_all(engine)
    raw = engine.raw_connection()
    try:
        apply_migrations(raw)
        fts_ok = init_fts(raw)
    finally:
        raw.close()
    assert fts_ok, "FTS5 初始化失败——本环境应支持 fts5（stdlib sqlite3 自带）"

    saved_db = {
        "engine": db_module.engine,
        "get_session": db_module.get_session,
        "FTS_OK": db_module.FTS_OK,
    }
    db_module.engine = engine
    db_module.get_session = lambda: Session(engine)
    db_module.FTS_OK = True

    def session_factory() -> Session:
        return Session(engine)

    # 向量后端：生产同款内嵌 ChromaDB；不可用则纯 Python 替身（报告里声明）
    vector_store, backend_name = _resolve_vector_backend(settings)

    # 外部服务替身：LLM 客户端源模块 + 各模块级绑定（同 conftest 口径，全量还原）
    import lantai.evolution.promoter as promoter_mod
    import lantai.gate.decision as gate_decision_mod
    import lantai.gate.scorer as gate_scorer_mod
    import lantai.llm.client as llm_client
    import lantai.parsing.extractor as extractor_mod
    import lantai.retrieval.hybrid as hybrid_mod
    import lantai.retrieval.intent as intent_mod
    import lantai.services.memory_service as memory_service_mod
    import lantai.storage.vector_store as vector_store_mod

    saved_llm = {}
    for mod, attr in [
        (llm_client, "embed"),
        (llm_client, "chat_json"),
        (llm_client, "vision_caption"),
        (hybrid_mod, "embed"),
        (hybrid_mod, "get_vector_store"),
        (memory_service_mod, "embed"),
        (memory_service_mod, "get_vector_store"),
        (vector_store_mod, "get_vector_store"),
        (intent_mod, "chat_json"),
        (extractor_mod, "chat_json"),
        (promoter_mod, "embed"),
        (gate_decision_mod, "embed"),
        (gate_scorer_mod, "embed"),
    ]:
        saved_llm[(mod.__name__, attr)] = getattr(mod, attr, None)

    def _fake_extraction(*args, **kwargs):
        return {
            "summary": "基线提取摘要：检索与存储链路的确定性验证样本",
            "claims": ["样本声明：主链路可用", "样本声明：延迟可测"],
            "methods": [],
            "constraints": [],
            "actions": ["记录基线数据"],
            "topic": ["性能基线"],
            "atomic_facts": [],
            "extractor_confidence": 0.9,
        }

    llm_client.embed = fake_embed
    llm_client.chat_json = _fake_extraction
    llm_client.vision_caption = lambda media_url: "perf-bench vision stub"
    hybrid_mod.embed = fake_embed
    hybrid_mod.get_vector_store = lambda: vector_store
    memory_service_mod.embed = fake_embed
    memory_service_mod.get_vector_store = lambda: vector_store
    vector_store_mod.get_vector_store = lambda: vector_store
    intent_mod.chat_json = _fake_extraction
    extractor_mod.chat_json = _fake_extraction
    promoter_mod.embed = fake_embed
    gate_decision_mod.embed = fake_embed
    gate_scorer_mod.embed = fake_embed

    rows = build_corpus(n_rows, seed=seed)
    _seed_db(session_factory, vector_store, rows)

    ctx = BenchContext(engine, session_factory, vector_store, backend_name, home, rows)
    try:
        yield ctx
    finally:
        engine.dispose()
        # 全部还原（冒烟测试以 import 方式复用本模块，生产单例不许留脏）
        db_module.engine = saved_db["engine"]
        db_module.get_session = saved_db["get_session"]
        db_module.FTS_OK = saved_db["FTS_OK"]
        for (mod_name, attr), value in saved_llm.items():
            mod = sys.modules.get(mod_name)
            if mod is not None and value is not None:
                setattr(mod, attr, value)
        for k, v in saved.items():
            setattr(settings, k, v)
        if workdir is None:
            shutil.rmtree(home, ignore_errors=True)


def _resolve_vector_backend(settings):
    try:
        from lantai.storage.vector_store import ChromaVectorStore

        store = ChromaVectorStore()
        return store, "chromadb-hnsw"
    except Exception as e:  # pragma: no cover - 本机装不上 chromadb 时
        return HashVectorStore(), f"hash-substitute({type(e).__name__})"


def _seed_db(session_factory, vector_store, rows) -> None:
    """灌库：ORM 插入（吃到 SQLModel 的 Python 层默认值）+ FTS + 向量入库。"""
    from lantai.models.tables import MemoryItem
    from lantai.storage.fts import sync_fts

    with session_factory() as s:
        items = [MemoryItem(**r) for r in rows]
        s.add_all(items)
        s.commit()
        conn = s.connection()
        raw = conn.connection.driver_connection
        raw.executemany(
            "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
            [(r["id"], r["content"]) for r in rows if r["status"] == "active"],
        )
        s.commit()

    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        vector_store.add(
            ids=[r["id"] for r in chunk],
            embeddings=fake_embed([r["content"] for r in chunk]),
            metadatas=[
                {
                    "key": "",
                    "memory_type": r["memory_type"],
                    "lane": r["lane"],
                    "domain": r["domain"],
                    "tenant_id": "",
                    "user_id": r["user_id"],
                    "session_id": "",
                    "agent_id": "",
                }
                for r in chunk
            ],
        )


# ── 计时与统计 ────────────────────────────────────────────────
def run_timed(call, iterations: int, warmup: int = 2) -> dict:
    for i in range(warmup):
        call(i)
    lat = []
    for i in range(iterations):
        t0 = time.perf_counter()
        call(i)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    n = len(lat)
    return {
        "iterations": n,
        "p50_ms": round(lat[n // 2], 2),
        "p95_ms": round(lat[min(n - 1, int(math.ceil(n * 0.95)) - 1)], 2),
        "mean_ms": round(statistics.fmean(lat), 2),
        "min_ms": round(lat[0], 2),
        "max_ms": round(lat[-1], 2),
        "stdev_ms": round(statistics.pstdev(lat), 2) if n > 1 else 0.0,
        "raw_ms": [round(x, 2) for x in lat],
    }


# ── 场景 ──────────────────────────────────────────────────────
def _raw_conn(s):
    return s.connection().connection.driver_connection


def scen_search_fts(ctx, i):
    from lantai.storage.fts import search_fts

    q = ctx.hit_queries[i % len(ctx.hit_queries)]
    with ctx.session_factory() as s:
        search_fts(_raw_conn(s), q, top_k=10, principal=ctx.default_principal)


def scen_search_fts_bm25(ctx, i):
    from lantai.storage.fts import search_fts_bm25

    q = ctx.hit_queries[i % len(ctx.hit_queries)]
    with ctx.session_factory() as s:
        search_fts_bm25(_raw_conn(s), q, top_k=30, principal=ctx.default_principal)


def scen_hybrid_search(ctx, i):
    from lantai.retrieval.hybrid import hybrid_search

    q = ctx.hit_queries[i % len(ctx.hit_queries)]
    hybrid_search(q, top_k=5, use_rerank=False, principal=None)


def scen_hybrid_shiyi_miss(ctx, i):
    """探空 query + 拾遗补救开档：量零召回补救（模糊子串重试等）的成本。"""
    from lantai.retrieval.hybrid import hybrid_search

    q = ctx.miss_queries[i % len(ctx.miss_queries)]
    hybrid_search(q, top_k=5, use_rerank=False, principal=None, enable_shiyi=True)


def scen_build_context(ctx, i):
    q = ctx.hit_queries[i % len(ctx.hit_queries)]
    ctx.shell_hook.build_context(q, session_id="perf-bench")


def _unique_call(fn):
    """写场景用真实调用计数生成唯一内容：warmup 与正式轮共用一条序号流，
    不重放相同内容。基线要测的是「全新写入」的全成本路径（文档 INSERT +
    候选 INSERT 各一次）；重复内容自票 .scratch/v068-perf/02 修复后在产品
    代码里合法（在场复用文档、照建候选），但那是更便宜的另一条路径，混在
    一起会把 P50 拉低失真。"""
    state = {"n": 0}

    def call(_i):
        n = state["n"]
        state["n"] += 1
        fn(n)

    return call


def _ingest_add_memory_once(n: int) -> None:
    from lantai.models.schemas import AddMemoryReq
    from lantai.services.memory_service import add_memory

    add_memory(
        AddMemoryReq(
            title=f"基线摄取样本 {n:05d}",
            content=(
                f"基线摄取样本{n:05d}：{_PROJECTS[n % len(_PROJECTS)]}项目"
                f"{_TOPICS[n % len(_TOPICS)]}的季度复盘纪要与后续动作清单"
            ),
            lane="fact",
        ),
        user_id="default",
    )


def make_scen_ingest_add_memory(ctx, iterations: int = 0):
    return _unique_call(_ingest_add_memory_once)


def make_scen_ingest_fastpath(ctx, iterations: int = 0):
    """白名单直写（零 Token 高速直写 ADR-0064）路径；模板必须真命中 fastpath。"""
    from lantai.models.schemas import AddMemoryReq
    from lantai.parsing.fastpath import fastpath_check
    from lantai.services.memory_service import add_memory

    probe = f"我叫老周{0:05d}，是后端工程师，负责检索链路"
    assert fastpath_check(probe), "fastpath 模板未命中白名单——场景口径失效，禁止出报告"

    def once(n: int) -> None:
        add_memory(
            AddMemoryReq(
                title=f"自我声明 {n:05d}",
                content=f"我叫老周{n:05d}，是后端工程师，负责检索链路",
                lane="profile",
            ),
            user_id="default",
        )

    return _unique_call(once)


_proposal_seq = {"n": 0}


def _seed_proposals(ctx, count: int) -> list[str]:
    from lantai.core.time import utcnow
    from lantai.models.tables import MemoryProposal

    seq = _proposal_seq["n"]
    _proposal_seq["n"] += 1
    ids = []
    with ctx.session_factory() as s:
        now = utcnow()
        for k in range(count):
            pid = f"prop_bench_{seq:03d}_{k:05d}"
            ids.append(pid)
            s.add(
                MemoryProposal(
                    id=pid,
                    user_id="default",
                    candidate_id=None,
                    proposal_type="add",
                    status="pending",
                    reason="perf-bench",
                    proposed_patch={
                        "content": f"提案正文{k:05d}：{_PROJECTS[k % len(_PROJECTS)]}项目"
                        f"{_TOPICS[k % len(_TOPICS)]}的演化结论",
                        "memory_type": "text",
                        "lane": "fact",
                        "key": None,
                    },
                    confidence=0.9,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
    return ids


def make_scen_apply_proposal(ctx, iterations: int):
    from lantai.evolution.promoter import apply_proposal

    # 提案一用一少（apply 改状态）；池子必须盖住 warmup + 全部迭代，不许回卷重放
    ids = _seed_proposals(ctx, count=iterations + 3)
    state = {"cursor": 0, "ids": ids}

    def call(i):
        pid = state["ids"][state["cursor"]]
        state["cursor"] += 1
        out = apply_proposal(pid, principal=None)
        if not out.get("ok"):
            raise AssertionError(f"apply_proposal 未成功：{out}")

    return call


def scen_apply_forgetting(ctx, i):
    from lantai.memory.forgetting import apply_forgetting

    apply_forgetting(principal=None)


def scen_embed_cost(ctx, i):
    fake_embed(["白泽项目检索延迟基线测量样本"])


def scen_vector_backend_search(ctx, i):
    qv = fake_embed(["白泽项目检索延迟基线测量样本"])[0]
    ctx.vector_store.search(
        qv,
        top_k=30,
        filters={"$or": [{"user_id": "default"}, {"user_id": ""}]},
    )


def load_shell_hook(ctx) -> None:
    import importlib.util

    hook_path = REPO_ROOT / "scripts" / "shell_hook.py"
    spec = importlib.util.spec_from_file_location("perf_bench_shell_hook", hook_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    ctx.shell_hook = mod


# (场景函数/工厂, 迭代数, warmup)。读路径先跑；写路径后跑（改库）；
# apply_forgetting 全表扫会把语料归档，最后跑且 warmup=0（冷扫本身就是测点）。
SCENARIOS = {
    "search_fts": (scen_search_fts, 30, 2),
    "search_fts_bm25": (scen_search_fts_bm25, 30, 2),
    "hybrid_search": (scen_hybrid_search, 30, 2),
    "hybrid_shiyi_miss": (scen_hybrid_shiyi_miss, 15, 1),
    "build_context": (scen_build_context, 30, 2),
    # 外部替身自价（报告单列，不与本地管线混账）
    "embed_fake_cost": (scen_embed_cost, 50, 2),
    "vector_backend_search": (scen_vector_backend_search, 30, 2),
    # 写路径（工厂场景：make_* 收 ctx 返回 call(i)，内容按真实调用序号唯一）
    "ingest_add_memory": (make_scen_ingest_add_memory, 60, 2),
    "ingest_fastpath": (make_scen_ingest_fastpath, 60, 2),
    "apply_proposal": (make_scen_apply_proposal, 40, 1),
    # 全表扫（会归档语料，最后跑）
    "apply_forgetting": (scen_apply_forgetting, 2, 0),
}


def _resolve_call(ctx, name: str, iterations: int):
    """工厂场景（make_*，签名 fn(ctx, iterations)）返回 call(i)；
    普通场景函数是 fn(ctx, i)，包一层。"""
    fn = SCENARIOS[name][0]
    if fn.__name__.startswith("make_"):
        return fn(ctx, iterations)
    return (lambda f: lambda i: f(ctx, i))(fn)


def run_scenario(ctx, name: str, iterations: int):
    _fn, _iters, warmup = SCENARIOS[name]
    return run_timed(_resolve_call(ctx, name, iterations), iterations, warmup=warmup)


# ── 健全性自检 ────────────────────────────────────────────────
def sanity_check(ctx) -> dict:
    """跑一次命中 query，三通道都必须有货——没有则环境坏了，数字无意义。"""
    from lantai.retrieval.hybrid import hybrid_search
    from lantai.storage.fts import search_fts, search_fts_bm25

    load_shell_hook(ctx)
    q = ctx.hit_queries[0]
    with ctx.session_factory() as s:
        fts = search_fts(_raw_conn(s), q, top_k=10, principal=ctx.default_principal)
        bm25 = search_fts_bm25(_raw_conn(s), q, top_k=30, principal=ctx.default_principal)
    results = hybrid_search(q, top_k=5, use_rerank=False, principal=None, trace=True)
    hits, trace_steps = results if isinstance(results, tuple) else (results, [])
    out = ctx.shell_hook.build_context(q, session_id="perf-bench")
    ctx.sanity = {
        "fts_hits": len(fts),
        "bm25_hits": len(bm25),
        "hybrid_results": len(hits),
        "hybrid_trace_ms": trace_steps[-1].get("elapsed_ms") if trace_steps else None,
        "build_context_retrieved": bool(out.get("event_id") or out.get("evidence")),
    }
    assert len(fts) > 0, "FTS 通道零召回——语料或索引装配有问题，禁止出报告"
    assert len(hits) > 0, "hybrid_search 零召回——环境装配有问题，禁止出报告"
    assert ctx.sanity["build_context_retrieved"], (
        "build_context 未走检索路径（闸门拦下）——hit query 需 ≥15 字实质内容"
    )
    return ctx.sanity


# ── cProfile 热点 ─────────────────────────────────────────────
def profile_scenario(ctx, name: str, iterations: int = 10, dump_path: Path | None = None) -> dict:
    call = _resolve_call(ctx, name, iterations)

    prof = cProfile.Profile()
    prof.enable()
    for i in range(iterations):
        call(i)
    prof.disable()

    st = pstats.Stats(prof)
    if dump_path is not None:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        st.dump_stats(str(dump_path))
    entries = []
    for (fpath, lineno, func), (cc, nc, tt, ct, callers) in st.stats.items():
        entries.append(
            {
                "file": fpath.replace("\\", "/"),
                "line": lineno,
                "func": func,
                "tottime_s": tt,
                "cumtime_s": ct,
                "ncalls": nc,
            }
        )

    def _short(e):
        for marker in ("lantai/", "scripts/", "sqlalchemy/", "sqlmodel/", "chromadb/"):
            idx = e["file"].find(marker)
            if idx >= 0:
                return e["file"][idx:]
        return e["file"]

    def _top(key, limit=12, pred=None):
        pool = [e for e in entries if (pred is None or pred(e))]
        pool.sort(key=key, reverse=True)
        return [
            {
                "where": f"{_short(e)}:{e['line']}({e['func']})",
                "tottime_ms": round(e["tottime_s"] * 1000, 2),
                "cumtime_ms": round(e["cumtime_s"] * 1000, 2),
                "ncalls": e["ncalls"],
            }
            for e in pool[:limit]
        ]

    total = sum(e["tottime_s"] for e in entries)
    out = {
        "iterations": iterations,
        "total_tottime_s": round(total, 3),
        "top_tottime": _top(lambda e: e["tottime_s"]),
        "top_cumtime": _top(lambda e: e["cumtime_s"]),
        "top_cumtime_lantai": _top(
            lambda e: e["cumtime_s"], pred=lambda e: "/lantai/" in e["file"]
        ),
    }
    if dump_path is not None:
        out["pstats_dump"] = dump_path.name
    return out


# ── 报告 ──────────────────────────────────────────────────────
def collect_env_info(ctx) -> dict:
    import platform
    import sqlite3

    from lantai.core.settings import settings

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "vector_backend": ctx.vector_backend,
        "corpus_rows": len(ctx.rows),
        "embed_dim": EMBED_DIM,
        "embed": "fake(确定性哈希嵌入, 外部 embedding API 不计)",
        "llm": "fake(确定性提取应答, 外部 LLM 不计)",
        "rerank": "off(外部服务, v0.15.2 基线已单列)",
        "coalesce": settings.COALESCE_ENABLED,
        "fts_gram_tokenize": settings.FTS_BM25_GRAM_TOKENIZE,
        "sanity": ctx.sanity,
    }


def write_report(scale_results: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "baseline.json"
    md_path = out_dir / "baseline.md"
    json_path.write_text(json.dumps(scale_results, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 兰台性能基线（v0.68 并行流 D）", ""]
    lines.append(f"- 生成时刻：{datetime.now().isoformat(timespec='seconds')}")
    lines.append("- 工具：`scripts/perf_bench.py`（离线、进程内直调、种子化可重复）")
    lines.append("")
    for scale, res in scale_results.items():
        env = res["env"]
        lines.append(f"## 规模 {scale} 条语料")
        lines.append("")
        lines.append(
            f"- 环境：Python {env['python']} / SQLite {env['sqlite']} / "
            f"向量后端 {env['vector_backend']}"
        )
        lines.append(f"- 替身口径：{env['embed']}；{env['llm']}；{env['rerank']}")
        s = env["sanity"]
        lines.append(
            f"- 健全性自检：FTS 命中 {s.get('fts_hits')}，BM25 命中 {s.get('bm25_hits')}，"
            f"hybrid 结果 {s.get('hybrid_results')}"
            f"（hybrid 端到端 {s.get('hybrid_trace_ms')}ms），"
            f"build_context 走到检索 {s.get('build_context_retrieved')}"
        )
        lines.append("")
        lines.append("| 场景 | 迭代 | P50(ms) | P95(ms) | mean | min | max | stdev |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, m in res["scenarios"].items():
            lines.append(
                f"| {name} | {m['iterations']} | {m['p50_ms']} | {m['p95_ms']} | "
                f"{m['mean_ms']} | {m['min_ms']} | {m['max_ms']} | {m['stdev_ms']} |"
            )
        lines.append("")
        for name, p in (res.get("profiles") or {}).items():
            lines.append(f"### 热点：{name}（cProfile，{p['iterations']} 迭代）")
            if p.get("pstats_dump"):
                lines.append(f"- 原始 pstats：`{p['pstats_dump']}`（pstats.Stats 可交互复盘）")
            lines.append("")
            lines.append("| 排名 | 位置 | tottime(ms) | cumtime(ms) | ncalls |")
            lines.append("|---|---|---|---|---|")
            for rank, e in enumerate(p["top_tottime"], 1):
                lines.append(
                    f"| {rank} | `{e['where']}` | {e['tottime_ms']} | "
                    f"{e['cumtime_ms']} | {e['ncalls']} |"
                )
            lines.append("")
            lines.append("按 cumtime（lantai 内部）：")
            lines.append("")
            for e in p["top_cumtime_lantai"][:8]:
                lines.append(
                    f"- `{e['where']}` cum {e['cumtime_ms']}ms / self {e['tottime_ms']}ms"
                    f" × {e['ncalls']} 次"
                )
            lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ── 主流程 ────────────────────────────────────────────────────
def run_scale(
    n_rows: int,
    seed: int = 20261107,
    iterations_scale: float = 1.0,
    only: list[str] | None = None,
    profile: bool = False,
    profile_dump_dir: Path | None = None,
) -> dict:
    """单档规模全场景基线。返回 {env, scenarios, profiles}。"""
    with bench_env(n_rows, seed=seed) as ctx:
        load_shell_hook(ctx)
        sanity_check(ctx)
        scenario_names = [n for n in SCENARIOS if (only is None or n in only)]
        scenarios = {}
        for name in scenario_names:
            iters = max(1, int(SCENARIOS[name][1] * iterations_scale))
            scenarios[name] = run_scenario(ctx, name, iters)
        profiles = {}
        if profile:
            for name in ["hybrid_search", "build_context", "ingest_add_memory", "apply_proposal"]:
                if name in scenario_names:
                    dump = (
                        profile_dump_dir / f"{n_rows}-{name}.pstats"
                        if profile_dump_dir is not None
                        else None
                    )
                    profiles[name] = profile_scenario(ctx, name, dump_path=dump)
        return {"env": collect_env_info(ctx), "scenarios": scenarios, "profiles": profiles}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="兰台离线性能基线（v0.68 并行流 D）")
    ap.add_argument("--scale", type=int, nargs="*", default=[1000, 5000], help="语料规模档")
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--iterations", type=float, default=1.0, help="迭代倍率")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定场景")
    ap.add_argument("--profile", action="store_true", help="对读/写主场景附 cProfile")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="产物目录（默认 .scratch/v068-perf/artifacts/run-<时间戳>）",
    )
    args = ap.parse_args(argv)

    out_dir = args.out or (
        REPO_ROOT
        / ".scratch"
        / "v068-perf"
        / "artifacts"
        / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )

    scale_results = {}
    for n in args.scale:
        print(f"== 规模 {n} 条 ==")
        t0 = time.perf_counter()
        res = run_scale(
            n,
            seed=args.seed,
            iterations_scale=args.iterations,
            only=args.only,
            profile=args.profile,
            profile_dump_dir=out_dir,
        )
        scale_results[n] = res
        for name, m in res["scenarios"].items():
            print(
                f"  {name:24s} P50 {m['p50_ms']:>9.2f}ms  P95 {m['p95_ms']:>9.2f}ms"
                f"  ({m['iterations']} iters)"
            )
        print(f"  （用时 {time.perf_counter() - t0:.1f}s）")

    json_path, md_path = write_report(scale_results, out_dir)
    print(f"\n报告：{md_path}\n原始：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
