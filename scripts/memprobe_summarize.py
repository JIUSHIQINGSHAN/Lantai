"""MemProbe 票 03 汇总与对账：report.json + 逐题回执 → summary.json / verify.json。

口径（预注册 metric_policy）：
- 官方指标块直接调 MemProbe compute_metrics.compute_all_metrics（本体指标，
  非 TWIST 清单）；分层维度补 by_paradigm（复用其 accuracy_over/build_episode_meta）。
- 归因：同 reader 参照（full_context / naive_rag）与兰台逐探针对齐——参照对而
  兰台错 → 检索/存储面线索；参照也错 → reader/考卷面线索。只作方向性计数。
- 对账（票 01 verify 口径）：56 题三键齐全、每题 answers 数=探针数、回执逐
  session complete、幂等重导探针 all_present、gate_blocked 计数。
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MP_SRC = REPO_ROOT / ".scratch/memprobe-suite/MemProbe/MemProbe-main/src/analysis"
sys.path.insert(0, str(MP_SRC))

import compute_metrics as CM  # noqa: E402, N812 — MemProbe 官方聚合器（外部套件，只读）

EVID = REPO_ROOT / "docs/benchmarks/memprobe-2026-10"
REPORT = EVID / "report.json"
EPISODES = REPO_ROOT / ".scratch/memprobe-suite/MemProbe/MemProbe-main/data/suite56/episodes.jsonl"

KEYS = ["lantai_verbatim_full", "full_context", "naive_rag"]


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    episodes = CM.load_episodes(str(EPISODES))
    meta = CM.build_episode_meta(episodes)
    n_probes = {e["episode_id"]: len(e["probes"]) for e in episodes}

    # ---- 官方指标块（MemProbe 本体口径） ----
    official = CM.compute_all_metrics(report, episodes)

    # ---- 补分层：by_paradigm ----
    def acc_over(key, pred):
        answers = []
        for eid, ep in report.get(key, {}).items():
            if isinstance(ep, dict) and "answers" in ep:
                answers.extend(
                    (a.get("probe_type", ""), a.get("match", ""), eid, a) for a in ep["answers"]
                )
        return CM.accuracy_over(answers, pred=pred)

    by_paradigm = {}
    for key in KEYS:
        if key not in report:
            continue
        by_paradigm[key] = {}
        for paradigm in ("interference", "misinformation", "consolidation", "reconsolidation"):
            c, t, p = acc_over(
                key, lambda pt, m, e, r, _p=paradigm: meta.get(e, {}).get("paradigm") == _p
            )
            by_paradigm[key][paradigm] = {"correct": c, "total": t, "accuracy": round(p, 4)}

    # ---- 逐探针对齐与归因（兰台 vs full_context，四格显式计数） ----
    alignment = {}
    gate_blocked = 0
    probe_retrievals = 0
    empty_context = 0
    for eid, ep in report.get("lantai_verbatim_full", {}).items():
        if not (isinstance(ep, dict) and "answers" in ep):
            continue
        fc = report.get("full_context", {}).get(eid, {}).get("answers", [])
        for i, a in enumerate(ep["answers"]):
            if i >= len(fc):
                continue
            pt = a.get("probe_type", "")
            cell = alignment.setdefault(
                pt,
                {
                    "n": 0,
                    "lantai_ok_fc_ok": 0,
                    "lantai_miss_fc_ok": 0,
                    "lantai_ok_fc_miss": 0,
                    "lantai_miss_fc_miss": 0,
                },
            )
            cell["n"] += 1
            lm = a.get("match") == "correct"
            fm = fc[i].get("match") == "correct"
            if lm and fm:
                cell["lantai_ok_fc_ok"] += 1
            elif not lm and fm:
                cell["lantai_miss_fc_ok"] += 1
            elif lm and not fm:
                cell["lantai_ok_fc_miss"] += 1
            else:
                cell["lantai_miss_fc_miss"] += 1

    # ---- 回执对账 ----
    receipts_dir = EVID / "ingest_receipts"
    receipt_report = {}
    for e in episodes:
        eid = e["episode_id"]
        p = receipts_dir / f"{eid}_receipts.jsonl"
        recs = (
            [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            if p.exists()
            else []
        )
        ing = [r for r in recs if r.get("kind") == "session_ingest"]
        pres = [r for r in recs if r.get("kind") == "store_presence_reimport"]
        prb = [r for r in recs if r.get("kind") == "probe_retrieve"]
        probe_retrievals += len(prb)
        gate_blocked += sum(1 for r in prb if r.get("gate_blocked"))
        empty_context += sum(
            1 for r in prb if not r.get("gate_blocked") and r.get("n_results") == 0
        )
        report_answers = report.get("lantai_verbatim_full", {}).get(eid, {})
        n_ans = len(report_answers.get("answers", [])) if isinstance(report_answers, dict) else 0
        receipt_report[eid] = {
            "receipts_present": p.exists(),
            "session_ingests": len(ing),
            "all_sessions_complete": bool(ing) and all(r.get("complete") for r in ing),
            "store_presence_all_present": bool(pres) and all(r.get("all_present") for r in pres),
            "store_presence_errors": [r for r in pres if not r.get("all_present")],
            "probe_retrieves": len(prb),
            "report_answers": n_ans,
            "answers_match_probes": n_ans == n_probes[eid],
            "sessions_expected": len(e["sessions"]),
        }

    # ---- verify.json ----
    complete_keys = {
        k: len(
            [
                e
                for e in episodes
                if isinstance(report.get(k, {}).get(e["episode_id"]), dict)
                and "answers" in report[k][e["episode_id"]]
            ]
        )
        for k in KEYS
    }
    missing = {
        k: [
            e["episode_id"]
            for e in episodes
            if not isinstance(report.get(k, {}).get(e["episode_id"]), dict)
            or "answers" not in report[k][e["episode_id"]]
        ]
        for k in KEYS
    }
    answers_mismatch = [eid for eid, r in receipt_report.items() if not r["answers_match_probes"]]
    sessions_bad = [eid for eid, r in receipt_report.items() if not r["all_sessions_complete"]]
    presence_bad = [eid for eid, r in receipt_report.items() if not r["store_presence_all_present"]]
    match_counts = {}
    for k in KEYS:
        if k not in report:
            continue
        mc = {}
        for e in episodes:
            ep = report[k].get(e["episode_id"], {})
            for a in ep.get("answers", []) if isinstance(ep, dict) else []:
                mc[a.get("match", "?")] = mc.get(a.get("match", "?"), 0) + 1
        match_counts[k] = mc
    verify = {
        "episodes_total": 56,
        "complete_per_key": complete_keys,
        "missing_per_key": {k: v for k, v in missing.items() if v},
        "answers_mismatch": answers_mismatch,
        "sessions_incomplete": sessions_bad,
        "store_presence_not_all_present": presence_bad,
        "match_counts_per_key": match_counts,
        "probe_retrievals_total": probe_retrievals,
        "gate_blocked_total": gate_blocked,
        "retrieved_empty_not_gate_blocked": empty_context,
        "verdict": "OK"
        if not any(missing.values())
        and not answers_mismatch
        and not sessions_bad
        and not presence_bad
        else "NEEDS_ATTENTION",
    }
    (EVID / "verify.json").write_text(
        json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- summary.json ----
    summary = {
        "official_metrics": official,
        "by_paradigm": by_paradigm,
        "probe_level_alignment_lantai_vs_full_context": alignment,
        "gate_and_coverage": {
            "probe_retrievals_total": probe_retrievals,
            "gate_blocked_total": gate_blocked,
            "retrieved_empty_not_gate_blocked": empty_context,
        },
        "verify_verdict": verify["verdict"],
    }
    (EVID / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(verify, ensure_ascii=False, indent=2))
    print("[done] summary.json / verify.json ->", EVID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
