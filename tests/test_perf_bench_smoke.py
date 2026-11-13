"""性能基线工具冒烟（不 mock 冒烟纪律的 harness 版）。

`scripts/perf_bench.py` 是 v0.68 性能基线的生产工具（票据
`.scratch/v068-perf/issues/01`）：它自建临时库、直调真实检索/摄取/演化实现。
harness 自己烂掉时基线数字静默失真，故给它一个不 mock 的冒烟——
小规模真实跑通主场景，断言统计结构完整、健全性自检通过。
"""

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "perf_bench.py"

REQUIRED_SCENARIOS = (
    "search_fts",
    "search_fts_bm25",
    "hybrid_search",
    "build_context",
    "ingest_add_memory",
    "ingest_fastpath",
    "apply_proposal",
    "apply_forgetting",
)


def _load_bench_module():
    spec = importlib.util.spec_from_file_location("perf_bench_smoke_mod", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_perf_bench_smoke_end_to_end(tmp_path):
    """小规模实跑 harness：主场景全绿、统计字段完整、外部替身单列。"""
    mod = _load_bench_module()
    res = mod.run_scale(
        60,
        seed=7,
        iterations_scale=0.15,
        profile=False,
    )

    assert res["env"]["vector_backend"].startswith("chromadb-hnsw") or res["env"][
        "vector_backend"
    ].startswith("hash-substitute")
    sanity = res["env"]["sanity"]
    assert sanity["fts_hits"] > 0, "FTS 通道零召回——harness 装配坏了"
    assert sanity["hybrid_results"] > 0, "hybrid 零召回——harness 装配坏了"
    assert sanity["build_context_retrieved"], "build_context 被闸门拦下——hit query 口径失效"

    for name in REQUIRED_SCENARIOS:
        m = res["scenarios"][name]
        assert m["iterations"] >= 1
        assert m["p50_ms"] > 0 and m["p95_ms"] >= m["p50_ms"]
        assert m["min_ms"] <= m["p50_ms"] <= m["max_ms"]

    # 替身成本单列且显著低于管线（本测试只验结构，不验具体数值——那是报告的事）
    assert "embed_fake_cost" in res["scenarios"]
    assert "vector_backend_search" in res["scenarios"]


def test_perf_bench_mcp_serve_smoke():
    """MCP serve 通道场景冒烟：真实 stdio JSON-RPC 子进程端到端（不 mock）。

    子进程经环境变量指向同一临时库、补同源确定性嵌入替身后跑 `lantai.cli.mcp`
    标准主循环——断言稳态 search 往返统计完整、冷启动/首查/ping 通道元数据齐。
    子进程收口由 run_scenario 的 close 钩子负责（stdio 断开 → 子进程自然退出）。
    """
    mod = _load_bench_module()
    res = mod.run_scale(
        60,
        seed=7,
        iterations_scale=0.1,
        only=["mcp_serve_search"],
        profile=False,
    )

    m = res["scenarios"]["mcp_serve_search"]
    assert m["iterations"] >= 1
    assert m["p50_ms"] > 0 and m["p95_ms"] >= m["p50_ms"]

    ch = res["mcp_channel"]
    assert ch is not None, "MCP 通道元数据缺失——工厂场景没装配子进程"
    assert ch["spawn_to_ready_ms"] > 0, "冷启动（spawn→initialize）未计时"
    assert ch["first_search_ms"] > 0, "首查（含子进程 chromadb 装载）未计时"
    assert ch["ping_ms_median"] > 0, "ping 通道底价未采样"


def test_perf_bench_profile_dumps_pstats(tmp_path):
    """--profile 的原始 pstats 落盘可被 pstats 回读（票 01 目标 3 的落点）。"""
    import pstats

    mod = _load_bench_module()
    dump = tmp_path / "40-hybrid_search.pstats"
    with mod.bench_env(40, seed=11, workdir=tmp_path / "env") as ctx:
        p = mod.profile_scenario(ctx, "hybrid_search", iterations=3, dump_path=dump)

    assert dump.exists() and dump.stat().st_size > 0
    assert p["pstats_dump"] == dump.name
    assert p["top_tottime"], "热点表为空——cProfile 没采到数据"
    st = pstats.Stats(str(dump))
    assert st.stats, "落盘的 pstats 回读为空——dump 格式坏了"


def test_perf_bench_env_restores_singletons(tmp_path):
    """bench_env 离开时必须还原生产单例（db.engine/get_session/settings/绑定），
    否则 harness 在测试进程里复用一次就污染全量套件。"""
    import lantai.storage.db as db_module
    from lantai.core.settings import settings

    mod = _load_bench_module()
    engine_before = db_module.engine
    get_session_before = db_module.get_session
    url_before = settings.DATABASE_URL
    home_before = settings.LANTAI_HOME
    from lantai.retrieval import hybrid as hybrid_mod

    embed_before = hybrid_mod.embed

    with mod.bench_env(20, seed=3, workdir=tmp_path / "env1"):
        assert db_module.engine is not engine_before
        assert url_before != settings.DATABASE_URL

    assert db_module.engine is engine_before
    assert db_module.get_session is get_session_before
    assert url_before == settings.DATABASE_URL
    assert home_before == settings.LANTAI_HOME
    assert hybrid_mod.embed is embed_before


def test_perf_bench_deterministic_corpus():
    """同种子语料逐字段一致——基线可重复的底线。"""
    mod = _load_bench_module()
    a = mod.build_corpus(200, seed=42)
    b = mod.build_corpus(200, seed=42)
    assert a == b
    c = mod.build_corpus(200, seed=43)
    assert a != c


@pytest.mark.parametrize(
    "filters,meta,expected",
    [
        ({"$or": [{"user_id": "u1"}, {"user_id": ""}]}, {"user_id": "u1"}, True),
        ({"$or": [{"user_id": "u1"}, {"user_id": ""}]}, {"user_id": "other"}, False),
        (
            {"$and": [{"lane": "fact"}, {"domain": "user"}]},
            {"lane": "fact", "domain": "user"},
            True,
        ),
        ({"$and": [{"lane": "fact"}, {"domain": "user"}]}, {"lane": "fact", "domain": "x"}, False),
        ({"lane": {"$in": ["fact", "rule"]}}, {"lane": "rule"}, True),
        ({"lane": {"$in": ["fact", "rule"]}}, {"lane": "chat"}, False),
        ({"lane": "fact"}, {}, False),
    ],
)
def test_hash_vector_store_filters(filters, meta, expected):
    """纯 Python 替身的过滤语义与 chroma where 对齐（$or/$and/$in/等值）。"""
    mod = _load_bench_module()
    store = mod.HashVectorStore()
    store.add(["m1"], [mod.fake_embed(["测试文本"])[0]], [meta])
    hits = store.search(mod.fake_embed(["测试文本"])[0], top_k=5, filters=filters)
    assert (len(hits) == 1) is expected


# ── v0.68 票 12 缺口 2：--only 口径闸（合并闸门项）──────────────────


def test_perf_bench_only_unknown_scenario_fails_loudly(tmp_path, capsys):
    """票 12 缺口 2（合并闸门项）：--only 拼错场景名必须非零退出并在 stderr
    列出合法场景名——禁止静默跑零场景、照写「成功」基线报告。"""
    mod = _load_bench_module()
    with pytest.raises(SystemExit) as ei:
        mod.main(["--scale", "60", "--only", "hybrid_sarch", "--out", str(tmp_path)])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "hybrid_sarch" in err, "报错没点名拼错的名字"
    for valid in ("hybrid_search", "ingest_add_memory", "apply_forgetting"):
        assert valid in err, "报错没列出合法场景名"


def test_perf_bench_only_valid_scenario_runs_report(tmp_path):
    """票 12 缺口 2 对照臂（现状即绿）：合法名单正常跑、报告只含点名场景。"""
    import json

    mod = _load_bench_module()
    rc = mod.main(
        [
            "--scale",
            "60",
            "--iterations",
            "0.1",
            "--only",
            "hybrid_search",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == 0
    report = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert set(report["60"]["scenarios"]) == {"hybrid_search"}


def test_perf_bench_write_stream_continues_across_resolves(tmp_path):
    """票 12 缺口 1：写场景唯一序号流挂在 ctx 上——timed 轮之后 profile 轮重新
    _resolve_call 必须续接新内容，而不是从 0 重放已摄取内容（重放会被
    rawdocument.content_hash 在场检查吸收成「复用文档」，profile 热点表把
    真实摄取热点系统性低估成去重分支）。"""
    import re
    import sqlite3

    mod = _load_bench_module()
    db_file = tmp_path / "env" / "bench.db"
    with mod.bench_env(20, seed=5, workdir=tmp_path / "env") as ctx:
        timed = mod._resolve_call(ctx, "ingest_add_memory", 3)
        for i in range(3):
            timed(i)
        profile = mod._resolve_call(ctx, "ingest_add_memory", 3)
        for i in range(3):
            profile(i)

    conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT content FROM rawdocument WHERE content LIKE '基线摄取样本%'"
        ).fetchall()
    finally:
        conn.close()
    seqs = sorted(int(re.search(r"基线摄取样本(\d{5})", r[0]).group(1)) for r in rows)
    assert seqs == list(range(6)), f"重放发生了：文档序号 {seqs}（应为 0..5 各一次）"


def test_perf_bench_profile_round_ingests_fresh_content(tmp_path):
    """票 12 缺口 1 端到端：run_scale(profile=True) 的 profile 轮对写场景续接
    唯一序号流——RawDocument 行数 = warmup + timed + profile 全部调用数，
    不因重放被 content_hash 在场检查吸收。"""
    import sqlite3

    mod = _load_bench_module()
    workdir = tmp_path / "env"
    res = mod.run_scale(
        60,
        seed=7,
        iterations_scale=0.15,
        profile=True,
        only=["ingest_add_memory"],
        workdir=workdir,
    )
    assert res["profiles"]["ingest_add_memory"], "profile 轮没跑"

    conn = sqlite3.connect(f"file:{(workdir / 'bench.db').as_posix()}?mode=ro", uri=True)
    try:
        n_docs = conn.execute(
            "SELECT COUNT(*) FROM rawdocument WHERE content LIKE '基线摄取样本%'"
        ).fetchone()[0]
    finally:
        conn.close()

    timed_iters = max(1, int(60 * 0.15))
    total_calls = 2 + timed_iters + 10  # warmup + timed + profile 默认 10 迭代
    assert n_docs == total_calls, (
        f"RawDocument {n_docs} 行 ≠ 全部 {total_calls} 次调用——profile 轮重放已摄取内容"
    )


@pytest.mark.parametrize(
    "scenario,prefix",
    [
        ("ingest_add_memory", "基线摄取样本"),
        ("ingest_fastpath", "我叫老周"),
    ],
    ids=["extract-path", "fastpath"],
)
def test_perf_bench_profile_no_content_replay(tmp_path, scenario, prefix):
    """票 12 缺口 1：run_scale 的 profile 列表覆盖 ingest_fastpath 后，两条写
    通道的 profile 轮都不得重放 timed 已摄取内容——断言落在真库行数：
    timed（warmup 2 + iters max(1, int(60*0.1))=6）共 8 条全新文档；
    profile 默认 10 迭代同为全新内容时共 18 条。"""
    import sqlite3

    mod = _load_bench_module()
    workdir = tmp_path / "env"
    mod.run_scale(20, seed=5, iterations_scale=0.1, only=[scenario], profile=True, workdir=workdir)
    assert (workdir / "bench.db").exists(), "workdir 缝未生效——真库落点不存在"
    conn = sqlite3.connect(workdir / "bench.db")
    try:
        (n_docs,) = conn.execute(
            "SELECT COUNT(*) FROM rawdocument WHERE content LIKE ?", (f"{prefix}%",)
        ).fetchone()
    finally:
        conn.close()
    assert n_docs == 18, (
        f"rawdocument {prefix}* 共 {n_docs} 行（期望 18 = timed 8 + profile 10）——"
        "profile 轮在重放已摄取内容"
    )
