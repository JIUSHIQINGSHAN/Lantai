"""E1/E2 正式跑测 CLI（票 02 步骤③）：真 LLM 轮 + 替身 CI 对照轮各一，
提取段 3 报一致率，JSONL 留证。

用法：
    PYTHONPATH=. python scripts/run_e1_e2_formal.py [--out-dir docs/benchmarks/e1e2-formal-2026-10]

跑测边界（如实钉入 meta）：
- 提取段真 LLM：chat 面（extractor/闸门矛盾/提案）走 OPENAI_BASE_URL/LLM_MODEL，
  temperature=0.2（lantai/llm/client.py chat_json 硬编码）；embed 面恒为确定性
  512 维替身——harness 写入侧固定 512（staged.py ④ 段），放真 embed（1024）
  即混维拒查，故「真 LLM」限定 chat 面。
- 替身 CI 对照轮：chat_json 全替身（同 tests/conftest 方案甲）。
- 双闸：settings（LANTAI_HOME/DATABASE_URL/CHROMADB_PATH）与 db.engine/
  vector_store 必须都已指向独立评测目录才起跑，断言不过即退出码 3。
"""

import argparse
import json
import sys
import time
from pathlib import Path

from lantai.core.settings import settings
from lantai.eval.deterministic_env import staged_deterministic_env
from lantai.eval.e2_temporal_cases import run_e2_temporal_eval
from lantai.eval.staged import CORPUS, run_staged_eval
from lantai.parsing.extractor import extract_candidate

DEFAULT_OUT = Path("docs/benchmarks/e1e2-formal-2026-10")


def _real_extract_fn(sid: str, text: str, expect: dict) -> list[dict]:
    """提取段真 LLM：直调生产 extract_candidate（chat_json → 真供应商）。

    返回形状与 default_extract_fn 相同（summary/claims/confidence/lane）；
    摘要为空视为提取失败（[]）——如实计 extraction_missing。
    """
    data = extract_candidate(sid, text)
    summary = (data.get("summary") or "").strip()
    if not summary:
        return []
    return [
        {
            "summary": summary,
            "claims": data.get("claims") or [],
            "confidence": float(data.get("extractor_confidence", 0.5)),
            "lane": "general",
        }
    ]


def _probe_model_identity() -> dict:
    """钉模型名/版本/base_url/温度：一次探测调用取网关回报的 resp.model。"""
    from lantai.llm.client import chat_json

    t0 = time.time()
    try:
        # chat_json 内部 model=settings.LLM_MODEL、temperature=0.2（硬编码）
        resp = chat_json("你是回声服务，原样返回 JSON。", '{"ping": 1}')
        return {
            "llm_model_setting": settings.LLM_MODEL,
            "embed_model_setting": settings.EMBED_MODEL,
            "base_url": settings.OPENAI_BASE_URL,
            "temperature": 0.2,
            "probe_response_keys": sorted(resp.keys())
            if isinstance(resp, dict)
            else str(resp)[:80],
            "probe_latency_s": round(time.time() - t0, 2),
            "ok": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": repr(exc)[:300],
            "probe_latency_s": round(time.time() - t0, 2),
        }


def _assert_double_gate(data_dir: Path) -> dict:
    """双闸确认：跑测前 settings 与 engine/store 都必须落在独立评测目录。"""
    import lantai.storage.db as db_module
    import lantai.storage.vector_store as vs_module

    checks = {
        "gate1_settings_lantai_home": str(settings.LANTAI_HOME),
        "gate1_settings_database_url": str(settings.DATABASE_URL),
        "gate1_settings_chromadb_path": str(settings.CHROMADB_PATH),
        "gate2_db_engine_url": str(db_module.engine.url),
        "gate2_vector_store_none_until_reinit": vs_module._store is None,
    }
    target = str(data_dir.resolve())
    in_memory_ok = checks["gate2_db_engine_url"] == "sqlite:///:memory:"
    ok = (
        target in str(Path(checks["gate1_settings_lantai_home"]).resolve())
        and target in str(Path(checks["gate1_settings_chromadb_path"]).resolve())
        and "eval.db" in checks["gate1_settings_database_url"]
        and (in_memory_ok or target in checks["gate2_db_engine_url"])
        and checks["gate2_vector_store_none_until_reinit"]
    )
    checks["gate2_db_engine_note"] = (
        "内存库（独立于宿主 LANTAI_HOME 真实库）" if in_memory_ok else "文件库落独立目录"
    )
    checks["double_gate_ok"] = ok
    return checks


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--skip-consistency", action="store_true", help="跳过提取段 3 报一致率（调试用）"
    )
    ap.add_argument(
        "--phase",
        choices=("all", "consistency", "e1", "e2"),
        default="all",
        help="分阶段跑（真 LLM 调用慢且有限流，长跑可拆段分别落同一留证目录）",
    )
    args = ap.parse_args(argv)

    def do(*names: str) -> bool:
        return args.phase == "all" or args.phase in names

    out: Path = args.out_dir
    data_dir = out / "eval-data"
    problems: list[str] = []

    # ── 0. 双闸确认（先于任何跑测）────────────────────────────────────────
    with staged_deterministic_env(data_dir=data_dir, real_llm_chat=True):
        gate = _assert_double_gate(data_dir)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    if not gate["double_gate_ok"]:
        print("双闸未落位，拒跑。", file=sys.stderr)
        return 3

    meta_path = out / "meta.json"
    meta = {}
    if meta_path.exists():  # 分阶段跑：merge 已有 meta，不冲掉先落的一致率等
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update(
        {
            "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "double_gate": gate,
            "model_identity": _probe_model_identity(),
            "harness": {
                "e1": "lantai.eval.staged.run_staged_eval",
                "e2": "lantai.eval.e2_temporal_cases.run_e2_temporal_eval",
                "real_extract": "lantai.parsing.extractor.extract_candidate (chat_json 真调)",
                "embed_face": "确定性 512 替身（deterministic_embed 单一真源；真 embed 1024 与写入侧混维，不放）",
                "temperature_source": "lantai/llm/client.py chat_json temperature=0.2",
            },
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not meta["model_identity"]["ok"]:
        print(f"模型探测失败：{meta['model_identity']}", file=sys.stderr)
        return 4

    # ── 1. E1 提取段 3 报一致率（真 LLM，供应商非确定性）──────────────────
    # 注：必须在 env 内跑——extract_candidate→get_prompt 要查库，env 外的
    # db.engine 指向宿主真实库（本机不可达）会瞬时失败被吞成 fallback。
    if not args.skip_consistency and do("consistency"):
        consistency: list[dict] = []
        with staged_deterministic_env(data_dir / "consistency", real_llm_chat=True):
            for sid, text, kind, _expect in CORPUS:
                if kind != "fact":  # 一致率口径=事实提取；噪音面归 E1 各轮 NFR
                    continue
                runs: list[list[str]] = []
                run_meta: list[dict] = []
                for i in range(3):
                    time.sleep(1.5)  # 供应商 RPM 限流退避（实测 429）
                    t0 = time.time()
                    try:
                        data = extract_candidate(sid, text)
                        summaries = [s.strip() for s in [(data.get("summary") or "")] if s.strip()]
                        # fallback 特征（extractor.py:10-19）：summary=原文前 400 字、
                        # confidence=0.3、claims 空——短文本真摘要可能与原文同，须三征共现
                        fallback = (
                            summaries == [text[:400]]
                            and data.get("extractor_confidence") == 0.3
                            and not data.get("claims")
                        )
                        rec = {
                            "run": i + 1,
                            "latency_s": round(time.time() - t0, 2),
                            "ok": True,
                            "extractor_confidence": data.get("extractor_confidence"),
                            "n_claims": len(data.get("claims") or []),
                            "fallback_suspect": fallback,
                        }
                    except Exception as exc:  # noqa: BLE001
                        summaries = []
                        rec = {
                            "run": i + 1,
                            "latency_s": round(time.time() - t0, 2),
                            "ok": False,
                            "error": repr(exc)[:200],
                            "fallback_suspect": True,
                        }
                    runs.append(sorted(summaries))
                    run_meta.append(rec | {"summaries": summaries})
                consistent = runs[0] == runs[1] == runs[2] and not any(
                    r.get("fallback_suspect") for r in run_meta
                )
                consistency.append(
                    {
                        "sid": sid,
                        "kind": kind,
                        "consistent_across_3_runs": consistent,
                        "runs": run_meta,
                    }
                )
                print(f"  一致率扫描 {sid}: {'一致' if consistent else '不一致'}", flush=True)
        fact_n = len(consistency)
        cons_n = sum(1 for c in consistency if c["consistent_across_3_runs"])
        _write_jsonl(out / "e1_extraction_consistency.jsonl", consistency)
        rate = round(cons_n / fact_n, 4) if fact_n else None
        print(f"提取段 3 报一致率（fact {fact_n} 条）: {cons_n}/{fact_n} = {rate}")
        meta["extraction_consistency_rate"] = rate
        all_fb = all(r.get("fallback_suspect") for c in consistency for r in c["runs"])
        if fact_n and all_fb:
            problems.append(
                "全部提取疑似降级 fallback（网络/限额失败嫌疑），数字不可信；"
                "若仅部分不一致=供应商非确定性，如实报告"
            )

    # ── 2. E1 两轮：真 LLM 提取 + 替身 CI 对照────────────────────────────
    if do("e1"):
        for tag, real, extract in (
            ("e1_real_llm", True, _real_extract_fn),
            ("e1_stub_ci", False, None),
        ):
            with staged_deterministic_env(data_dir / tag, real_llm_chat=real) as captor:
                result = run_staged_eval(extract_fn=extract)
            record = {
                "tag": tag,
                "corpus_hash": result["corpus_hash"],
                "stages": result["stages"],
                "anchors": result["anchors"],
                "root_cause_chains": result["root_cause_chains"],
                "guard_log": [ln for ln in captor.records if "獬豸" in ln],
            }
            _write_jsonl(out / f"{tag}.jsonl", [record])
            ok = all(a["achieved"] for a in result["anchors"])
            print(
                f"{tag}: 锚点 {'全 achieved' if ok else '存在 miss'}，"
                f"段数字 {[(st['stage'], st['success_rate']) for st in result['stages']]}"
            )

    # ── 3. E2 两轮：真 LLM chat 面 + 替身 CI 对照──────────────────────────
    if do("e2"):
        for tag, real in (("e2_real_llm", True), ("e2_stub_ci", False)):
            with staged_deterministic_env(data_dir / tag, real_llm_chat=real) as captor:
                result = run_e2_temporal_eval()
            record = {"tag": tag, **result, "guard_log": captor.records[:20]}
            _write_jsonl(out / f"{tag}.jsonl", [record])
            print(
                f"{tag}: queries={result['queries']} correct={result['correct']} accuracy={result['accuracy']}"
                + (f" failures={len(result['failures'])}" if result["failures"] else "")
            )

    meta["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("问题：" + "; ".join(problems), file=sys.stderr)
        return 5
    print(f"留证目录: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
