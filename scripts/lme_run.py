"""LongMemEval 跑测编排（票 02 步骤④）：API 单通道灌库 + 逐题 checkpoint +
三配置消融查询 + 状态落盘。

纪律（票 B）：
- 灌库双闸：独立 LANTAI_DATA_DIR（环境变量指评测目录）+ 启动断言 ≠ 生产路径
  （.env 的 LANTAI_HOME=C:\\Users\\Asus\\AppData\\Local\\remembrance-data）；
  只走正式 API（/import/jsonl、/search）单通道，本脚本不 import
  chroma/Session/向量库（grep 可验）；API key 走环境最小集。
- 逐题 checkpoint：题内每 chunk（本实现题=chunk，逐 session 由 API 内部落库）
  落盘 progress.jsonl，绑定 config_hash；续跑先对账（半题→清题重灌）。
- 长跑可见性：status.json 实时落盘；失败显式报（非零退出码 + status.error）。

用法：
    PYTHONPATH=. python scripts/lme_run.py pilot            # 1 题实测吞吐/费用
    PYTHONPATH=. python scripts/lme_run.py ingest --n 50    # 灌库（真 embed）
    PYTHONPATH=. python scripts/lme_run.py query --config vector-only
    PYTHONPATH=. python scripts/lme_run.py report
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from lantai.eval.longmemeval import (
    aggregate_metrics,
    assert_prereg_subset,
    config_hash,
    coverage_audit,
    half_question_detect,
    import_lines_for_question,
    load_dataset,
    result_session_ids,
    session_recall_hit,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/benchmarks/longmemeval-2026-10"
DATA = ROOT / "data/longmemeval/longmemeval_s_cleaned.json"
EVAL_DATA_DIR = OUT / "eval-data"
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
PROD_LANTAI_HOME = r"C:\Users\Asus\AppData\Local\remembrance-data"  # .env 实配生产库

CONFIGS = {
    "vector-only": {
        "RETRIEVAL_W_VECTOR": "1.0",
        "RETRIEVAL_W_BM25": "0.0",
        "RETRIEVAL_W_FTS": "0.0",
    },
    "bm25-only": {"RETRIEVAL_W_VECTOR": "0.0", "RETRIEVAL_W_BM25": "1.0", "RETRIEVAL_W_FTS": "0.0"},
    "hybrid-default": {
        "RETRIEVAL_W_VECTOR": "0.6",
        "RETRIEVAL_W_BM25": "0.25",
        "RETRIEVAL_W_FTS": "0.05",
    },
}
COMMON_ENV = {"VERBATIM_IN_RECALL": "true", "RERANKER_ENABLED": "false"}


def _load_all(n: int):
    data = load_dataset(DATA)
    prereg = json.loads((OUT / "preregistration.json").read_text(encoding="utf-8"))
    ids = prereg["question_ids"][:n]
    assert_prereg_subset(ids, data)
    by_id = {q["question_id"]: q for q in data}
    return [by_id[i] for i in ids], prereg


def _dotenv() -> dict[str, str]:
    """极简 .env 解析（KEY=VALUE，忽略注释/空行）——凭据只在生产 .env，不在进程环境。"""
    out: dict[str, str] = {}
    p = ROOT / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().split(" #")[0].strip()
    return out


def _server_env(config: dict | None, qid: str | None) -> dict:
    """服务器子进程最小环境（诚实边界：subprocess 传最小 env）。

    题级隔离：qid 给定时 LANTAI_HOME 指该题专属评测库（q_<qid> 子目录）——
    检索隔离不靠 lanes（API 密钥 allowed_lanes=DEFAULT_LANES 收窄），靠每题
    独立库；闸二由 _assert_double_gate 断言 ≠ 生产。
    凭据来源：进程环境优先，缺省回落 .env（首跑 KeyError('OPENAI_API_KEY')
    的根因：shell 未 export .env 值）。
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        # Windows home 解析链（缺省会让 chromadb/sentence-transformers 报
        # "Could not determine home directory" → 向量通道静默降级关键词，
        # pilot 实测发现，必须带上）
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
        "HOMEPATH": os.environ.get("HOMEPATH", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        # 真 embed/chat 凭据（生产 .env 同源三件）
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or _dotenv()["OPENAI_API_KEY"],
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL") or _dotenv()["OPENAI_BASE_URL"],
        "LLM_MODEL": os.environ.get("LLM_MODEL") or _dotenv().get("LLM_MODEL", "Qwen/Qwen3-8B"),
        "EMBED_MODEL": os.environ.get("EMBED_MODEL") or _dotenv().get("EMBED_MODEL", "BAAI/bge-m3"),
        "API_KEY": os.environ.get("API_KEY")
        or _dotenv().get("API_KEY", "lantai-local-sync-secret-2026-10"),
        "PORT": str(PORT),
        "LANTAI_HOME": str(_q_dir(qid) if qid else EVAL_DATA_DIR),
        "LANTAI_INTENT_OFF": "1",  # 意图分类走 LLM；召回口径关掉（如实记录）
        "LANTAI_RUN_SCHEDULER": "false",  # 评测库不跑遗忘/蒸馏 worker（防删评测数据）
        "MONITOR_ENABLED": "false",
        **{k: str(v) for k, v in COMMON_ENV.items()},
    }
    if config:
        env.update({k: str(v) for k, v in config.items()})
    return env


def _q_dir(qid: str | None) -> Path:
    """题级专属评测库目录（q_<qid>）；无 qid 返回公共根（仅占位/日志）。"""
    d = EVAL_DATA_DIR / f"q_{qid}" if qid else EVAL_DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / ".chromadb").mkdir(exist_ok=True)
    return d


def _assert_double_gate(qid: str | None = None) -> Path:
    """闸：评测数据目录 ≠ 生产路径；目录解析后前缀比对（防相对路径绕过）。"""
    prod = Path(PROD_LANTAI_HOME).resolve()
    ev = _q_dir(qid).resolve()
    if ev == prod or str(prod) in str(ev) or str(ev) in str(prod):
        raise SystemExit(f"双闸失败：评测目录 {ev} 与生产路径 {prod} 重叠，拒跑")
    print(f"[双闸通过] 评测库: {ev}（≠ 生产 {prod}）")
    return ev


def _start_server(config: dict | None, qid: str | None = None) -> subprocess.Popen:
    ev = _assert_double_gate(qid)
    env = _server_env(config, qid)
    print(
        f"[横幅] 评测 API 启动：PORT={PORT} LANTAI_HOME={ev} config={config or 'ingest'} qid={qid}",
        flush=True,
    )
    log_fh = open(OUT / "server.log", "ab")  # noqa: SIM115 — 生命周期=子进程，随 proc.kill() 由 OS 回收
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lantai.api.app:app", "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        try:
            if requests.get(f"{BASE}/health", timeout=3).ok:
                print("[横幅] /health 就绪", flush=True)
                return proc
        except requests.RequestException:
            pass
        time.sleep(1)
    proc.kill()
    raise SystemExit("评测 API 120s 未就绪（见 server.log），拒跑")


def _api_headers():
    # 环境变量 API_KEY 的鉴权形态是 X-API-Key（lantai/core/auth.py:147-149），
    # Bearer 是库内 key 通道——评测库无库内 key，用 Bearer 会 401。
    return {
        "X-API-Key": os.environ.get("API_KEY")
        or _dotenv().get("API_KEY", "lantai-local-sync-secret-2026-10")
    }


def _status(**kw):
    p = OUT / "status.json"
    st = {}
    if p.exists():
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            st = {}
    st.update(kw)
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _ingest_checkpoint_path():
    return OUT / "ingest_progress.jsonl"


def _ingest_one(proc_server, q: dict, resume_state: dict) -> dict:
    """灌一题（题=checkpoint 粒度）：/import/jsonl 单通道。

    半题对账：应灌 N=len(lines)，API 报告 imported+duplicates+errors 之和 M；
    M<N 或 errors 非空 → 半题 → 清题重灌（lane 级 delete 由正式 API 无对应端点，
    重灌依赖内容 sha 幂等：同内容重复导入记 duplicate，不产生脏写）。
    """
    lines = import_lines_for_question(q)
    expected = len(lines)
    qid = q["question_id"]
    payload = "\n".join(json.dumps(l, ensure_ascii=False) for l in lines)
    t0 = time.time()
    r = requests.post(
        f"{BASE}/import/jsonl",
        json={"text": payload},
        headers=_api_headers(),
        timeout=1800,
    )
    r.raise_for_status()
    rep = r.json()
    done = rep.get("imported", 0) + rep.get("duplicates", 0) + len(rep.get("invalid", []))
    half = half_question_detect(expected, done)
    rec = {
        "question_id": qid,
        "expected_n": expected,
        "imported": rep.get("imported"),
        "duplicates": rep.get("duplicates"),
        "errors": rep.get("errors", [])[:3],
        "invalid_n": len(rep.get("invalid", [])),
        "half_question": half,
        "latency_s": round(time.time() - t0, 1),
    }
    if half:
        # 清题重灌一次（幂等：已 imported 的同内容行将记 duplicate）
        r2 = requests.post(
            f"{BASE}/import/jsonl",
            json={"text": payload},
            headers=_api_headers(),
            timeout=1800,
        )
        r2.raise_for_status()
        rep2 = r2.json()
        done2 = rep2.get("imported", 0) + rep2.get("duplicates", 0) + len(rep2.get("invalid", []))
        rec["retried_done"] = done2
        rec["half_question"] = half_question_detect(expected, done2)
    return rec


def cmd_pilot(args) -> int:
    qs, prereg = _load_all(1)
    q = qs[0]
    qid = q["question_id"]
    proc = _start_server(None, qid)
    try:
        rec = _ingest_one(proc, q, {})
        # 1 题查询（默认配置）验证映射与隔离
        sr = requests.post(
            f"{BASE}/search",
            json={"query": q["question"], "top_k": 5, "use_rerank": False},
            headers=_api_headers(),
            timeout=600,
        )
        sr.raise_for_status()
        sj = sr.json()
        sids = result_session_ids(sj.get("results", []))
        pilot = {
            "question_id": q["question_id"],
            "ingest": rec,
            "search_gate_skipped": bool(sj.get("gate", {}).get("needs_memory") is False),
            "retrieved_sessions": sids,
            "gold_sessions": q["answer_session_ids"],
            "hit_at_2": session_recall_hit(q, sids, 2),
            "hit_at_5": session_recall_hit(q, sids, 5),
            "sample_result_keys": sorted(sj["results"][0].keys()) if sj.get("results") else [],
            "sample_memory_keys": sorted(sj["results"][0]["memory"].keys())
            if sj.get("results")
            else [],
        }
        (OUT / "pilot.json").write_text(
            json.dumps(pilot, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(pilot, ensure_ascii=False, indent=2, default=str))
        # ×3 外推
        per_q = rec["latency_s"]
        print(f"pilot 单题灌库 {per_q}s → 50 题×3 ≈ {per_q * 50 * 3 / 3600:.2f}h")
        _status(pilot_done=True, pilot_question=q["question_id"], pilot_latency_s=per_q)
        return 0
    finally:
        proc.kill()


def cmd_ingest(args) -> int:
    qs, prereg = _load_all(args.n)
    chash = config_hash(
        {
            "revision": prereg["dataset_revision"],
            "stage": "ingest",
            "ids": prereg["question_ids"][: args.n],
        }
    )
    ck = _ingest_checkpoint_path()
    done_ids = set()
    if ck.exists():
        for line in ck.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("config_hash") == chash and not rec.get("half_question"):
                    done_ids.add(rec["question_id"])
            except json.JSONDecodeError:
                continue  # 最后一行可能半写（崩溃痕迹）——半题对账：不在 done_ids 即重灌
    with ck.open("a", encoding="utf-8") as f:
        for i, q in enumerate(qs):
            if q["question_id"] in done_ids:
                continue
            _status(
                stage="ingest", done=i, total=len(qs), current=q["question_id"], config_hash=chash
            )
            proc = _start_server(None, q["question_id"])  # 每题独立库
            try:
                rec = _ingest_one(proc, q, {})
            finally:
                proc.kill()
            rec["config_hash"] = chash
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            f.flush()
            print(
                f"[{i + 1}/{len(qs)}] {q['question_id']}: "
                f"imported={rec['imported']} half={rec['half_question']} "
                f"{rec['latency_s']}s",
                flush=True,
            )
    _status(stage="ingest", done=len(qs), total=len(qs), config_hash=chash, finished=True)
    return 0


def cmd_query(args) -> int:
    if args.config not in CONFIGS:
        raise SystemExit(f"未知配置 {args.config}，可选：{list(CONFIGS)}")
    qs, prereg = _load_all(args.n)
    cfg = {**CONFIGS[args.config], **COMMON_ENV}
    chash = config_hash(
        {
            "revision": prereg["dataset_revision"],
            "config": args.config,
            "env": cfg,
            "ids": prereg["question_ids"][: args.n],
        }
    )
    out_path = OUT / f"results_{args.config}.jsonl"
    done_ids = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("config_hash") == chash:
                    done_ids.add(json.loads(line)["question_id"])
            except json.JSONDecodeError:
                continue
    records = []
    with out_path.open("a", encoding="utf-8") as f:
        for i, q in enumerate(qs):
            qid = q["question_id"]
            if qid in done_ids:
                continue
            _status(
                stage=f"query:{args.config}", done=i, total=len(qs), current=qid, config_hash=chash
            )
            proc = _start_server(CONFIGS[args.config], qid)  # 每题独立库 + 本配置权重
            t0 = time.time()
            try:
                try:
                    sr = requests.post(
                        f"{BASE}/search",
                        json={"query": q["question"], "top_k": 5, "use_rerank": False},
                        headers=_api_headers(),
                        timeout=600,
                    )
                    sr.raise_for_status()
                    sj = sr.json()
                    gate_skipped = sj.get("gate", {}).get("needs_memory") is False
                    sids = result_session_ids(sj.get("results", []))
                    # 打分前覆盖审计：金标会话是否都灌进了该题专属库（经检索面可见）
                    imported_all = [l["tags"][0] for l in import_lines_for_question(q)]
                    audit = coverage_audit(q, sids)
                    audit_full = not audit["missing"] and set(q["answer_session_ids"]) <= set(
                        imported_all
                    )
                    rec = {
                        "question_id": qid,
                        "question_type": q["question_type"],
                        "gate_skipped": gate_skipped,
                        "retrieved_sessions": sids[:5],
                        "hit_at_2": session_recall_hit(q, sids, 2),
                        "hit_at_5": session_recall_hit(q, sids, 5),
                        "gold_in_retrieved_any": bool(set(q["answer_session_ids"]) & set(sids)),
                        "coverage_full": audit_full,
                        "coverage_audit": audit,
                        "abstention": q["question_type"] == "abstention",
                        "latency_s": round(time.time() - t0, 1),
                    }
                except Exception as exc:  # noqa: BLE001 —— 单题失败显式记录不静默
                    rec = {
                        "question_id": qid,
                        "question_type": q["question_type"],
                        "error": repr(exc)[:300],
                        "gate_skipped": True,
                        "hit_at_2": False,
                        "hit_at_5": False,
                        "abstention": False,
                        "coverage_full": False,
                    }
                rec["config_hash"] = chash
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()
                records.append(rec)
                print(f"[{i + 1}/{len(qs)}] {qid} hit5={rec.get('hit_at_5')}", flush=True)
            finally:
                proc.kill()
    _status(stage=f"query:{args.config}", finished=True, config_hash=chash)
    return 0


def cmd_report(args) -> int:
    summary = {}
    for name in CONFIGS:
        p = OUT / f"results_{name}.jsonl"
        if not p.exists():
            continue
        recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        chashes = {r.get("config_hash") for r in recs}
        if len(chashes) != 1:
            raise SystemExit(f"{name}: config_hash 混杂 {chashes}（防跨配置污染断言失败）")
        # 聚合前断言每题恰属一个配置（这里=每题一行；重复 id 即脏）
        ids = [r["question_id"] for r in recs]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"{name}: 存在重复题记录（半题/重灌残留），先清理")
        summary[name] = aggregate_metrics(recs)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args) -> int:
    """醒来自愈对账（票 B：勘合指纹键只护 600s 窗口，隔夜续跑已出窗）。

    逐题 checkpoint 对账：应灌 N（题内 session 数）vs 实测 M（imported+
    duplicates+invalid）；半题 → 从 checkpoint 剔除该题（重跑 ingest 即清题
    重灌，内容 sha 幂等保证已灌行记 duplicate 不脏写）。缺题=未跑完，列出。
    退出码：0=全对账通过可进 query；2=存在半题/缺题（先重跑 ingest --n N）。
    """
    qs, prereg = _load_all(args.n)
    chash = config_hash(
        {
            "revision": prereg["dataset_revision"],
            "stage": "ingest",
            "ids": prereg["question_ids"][: args.n],
        }
    )
    ck = _ingest_checkpoint_path()
    recs: dict[str, dict] = {}
    if ck.exists():
        for line in ck.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # 半写行（崩溃痕迹）——对应题视为未完成
            if r.get("config_hash") == chash:
                recs[r["question_id"]] = r  # 后写覆盖前写（重灌以最新为准）
    half: list[str] = []
    missing: list[str] = []
    for q in qs:
        qid = q["question_id"]
        r = recs.get(qid)
        expected = len(import_lines_for_question(q))
        if r is None:
            missing.append(qid)
        elif r.get("half_question") or (
            (r.get("imported", 0) + r.get("duplicates", 0) + r.get("invalid_n", 0)) < expected
        ):
            half.append(qid)
    report = {
        "config_hash": chash,
        "total": len(qs),
        "complete_n": sum(
            1 for q in qs if q["question_id"] in recs and q["question_id"] not in half
        ),
        "half_questions": half,
        "missing_questions": missing,
        "verdict": "OK" if not half and not missing else "NEEDS_REINGEST",
        "resume_command": "PYTHONPATH=. python scripts/lme_run.py ingest --n "
        f"{args.n}  # 半题/缺题自动清题重灌（checkpoint 剔除后）",
    }
    (OUT / "verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if half:
        # 从 checkpoint 文件剔除半题行，重跑 ingest 即清题重灌
        keep = [
            l
            for l in ck.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("question_id") not in set(half)
        ]
        ck.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        print(f"已从 checkpoint 剔除半题 {len(half)} 题，重跑 ingest 将清题重灌")
    return 0 if not half and not missing else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pilot")
    pi = sub.add_parser("ingest")
    pi.add_argument("--n", type=int, default=50)
    qu = sub.add_parser("query")
    qu.add_argument("--config", required=True)
    qu.add_argument("--n", type=int, default=50)
    ve = sub.add_parser("verify")
    ve.add_argument("--n", type=int, default=25)
    sub.add_parser("report")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    _status(stage=args.cmd, started=True)
    try:
        return {
            "pilot": cmd_pilot,
            "ingest": cmd_ingest,
            "query": cmd_query,
            "verify": cmd_verify,
            "report": cmd_report,
        }[args.cmd](args)
    except Exception as exc:  # noqa: BLE001 —— 长跑可见性：失败显式报
        _status(stage=args.cmd, error=repr(exc)[:500])
        raise


if __name__ == "__main__":
    sys.exit(main())
