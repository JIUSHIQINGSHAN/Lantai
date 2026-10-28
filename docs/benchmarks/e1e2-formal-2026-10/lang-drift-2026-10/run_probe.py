"""最小复现实验：提取语言漂移 → 中文查询双通道失配（v0.25 窗口票 02 步骤 1）。

因果链三环节，本脚本逐环取证：
  环节①（真 LLM）：现行 EXTRACT_SYS 下，中文 utterance → Qwen3-8B 摘要漂成英文。
  环节②（确定性，无 LLM）：英文摘要与中文查询在 FTS 字面 gram 与确定性
          3-gram hash 向量（harness 写入侧同款，deterministic_embed）双通道零交叠；
          中文摘要作阳性对照。
  环节③（真 LLM）：语言锁定版 EXTRACT_SYS（即拟入仓文案，逐字同
          lantai/llm/prompts.py 修改稿）下，同一 utterance 摘要回中文。

用法（仓库根）：
    PYTHONPATH=. python docs/benchmarks/e1e2-formal-2026-10/lang-drift-2026-10/run_probe.py

口径钉板：与基线 meta.json 同（Qwen/Qwen3-8B @ siliconflow，temperature 0.2，
chat_json 硬编码）；数据隔离用 staged_deterministic_env（独立 eval-data 目录）。
诚实护栏：提取 fallback（summary=原文前 400 字、confidence=0.3、claims 空）
不构成任何一环的证据，如实标记 fallback_suspect 并从结论中剔除。
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))

import lantai.parsing.extractor as extractor_mod  # noqa: E402
from lantai.eval.deterministic_embed import embed_for_eval  # noqa: E402
from lantai.eval.deterministic_env import staged_deterministic_env  # noqa: E402
from lantai.parsing.extractor import extract_candidate  # noqa: E402

# 拟入仓的语言锁定文案（与 lantai/llm/prompts.py 修改稿逐字一致，单处真源为 prompts.py）
LOCKED_EXTRACT_SYS = """You are a careful research analyst.
Extract STRUCTURED knowledge from the given article/paper text.
Return strict JSON with fields:
  summary: 2-4 sentence summary
  claims: list of concrete claims / conclusions
  methods: list of methods / mechanisms
  constraints: list of applicable conditions / limitations
  actions: list of actionable rules the reader can adopt as a procedure
  topic: 1-5 tags (lowercase)
  extractor_confidence: 0.0-1.0
Language rule: write every output field in the same language as the source text (Chinese source -> Chinese output, English source -> English output). Never translate the source into another language.
Only output JSON. No prose."""

# 环节①③输入：基线留证中确认漂移的中文 utterance（staged.py 语料原样）
UTTERANCES = {
    "u1": "用户的主数据库是 PostgreSQL 15，部署在公司内网",
    "u5": "用户对花生过敏",
    "u6": "用户每周三下午开团队例会",
}
# 环节②输入：基线真实查询 + 基线留证 run1 的英文摘要（e1_extraction_consistency.jsonl）
QUERIES = {"q_db": "用户的主数据库是什么", "q_allergy": "用户对花生过敏吗"}
CONSISTENCY_FILE = HERE.parent / "e1_extraction_consistency.jsonl"


def cjk_ratio(text: str) -> float:
    """CJK 字符占「字母+数字+CJK」的比例：英文正文≈0，中文正文≈1。"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    alnum = sum(1 for ch in text if ch.isascii() and ch.isalnum())
    denom = cjk + alnum
    return round(cjk / denom, 4) if denom else 0.0


def _sqrt(v: list[float]) -> float:
    return sum(x * x for x in v) ** 0.5


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = _sqrt(a), _sqrt(b)
    return round(dot / (na * nb), 4) if na and nb else 0.0


def grams3(text: str) -> set:
    return {text[i : i + 3] for i in range(max(0, len(text) - 2))} or {text}


def gram_overlap(q: str, doc: str) -> int:
    return len(grams3(q) & grams3(doc))


def _fallback_suspect(data: dict, text: str) -> bool:
    """三征共现判 fallback（extractor.py 降级分支：原文前 400 字 + 0.3 + 空 claims）。"""
    return (
        (data.get("summary") or "") == text[:400]
        and data.get("extractor_confidence") == 0.3
        and not data.get("claims")
    )


def _extract_one(sid: str, text: str) -> dict:
    time.sleep(1.5)  # 供应商 RPM 退避（正式跑测同款）
    t0 = time.time()
    try:
        data = extract_candidate(sid, text)
        summary = (data.get("summary") or "").strip()
        return {
            "summary": summary,
            "cjk_ratio": cjk_ratio(summary),
            "extractor_confidence": data.get("extractor_confidence"),
            "n_claims": len(data.get("claims") or []),
            "fallback_suspect": _fallback_suspect(data, text),
            "latency_s": round(time.time() - t0, 2),
            "ok": bool(summary),
        }
    except Exception as exc:  # noqa: BLE001 —— 如实留错，不中断其余样本
        return {
            "summary": "",
            "ok": False,
            "error": repr(exc)[:300],
            "latency_s": round(time.time() - t0, 2),
        }


def _recorded_english_summaries() -> dict:
    """基线留证 run1 摘要（含英文漂移原样证据）；缺文件则返回空。"""
    out = {}
    if CONSISTENCY_FILE.exists():
        for line in CONSISTENCY_FILE.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            runs = d.get("runs") or []
            if runs:
                out[d["sid"]] = (runs[0].get("summaries") or [""])[0]
    return out


def main() -> int:
    results = {
        "purpose": "v0.25 窗口票 02 步骤1：提取语言漂移最小复现（环节①漂移/②双通道失配/③锁定回中文）",
        "model_identity": {},
        "live_current_prompt": {},
        "channel_analysis": {},
        "live_locked_prompt": {},
        "verdict": {},
    }
    with staged_deterministic_env(data_dir=HERE / "eval-data", real_llm_chat=True):
        from lantai.core.settings import settings
        from lantai.llm.client import chat_json

        results["model_identity"] = {
            "llm_model_setting": settings.LLM_MODEL,
            "embed_model_setting": settings.EMBED_MODEL,
            "base_url": settings.OPENAI_BASE_URL,
            "temperature": 0.2,
            "temperature_source": "lantai/llm/client.py chat_json temperature=0.2",
        }
        # 探活（同正式跑测 _probe_model_identity 口径）
        t0 = time.time()
        probe = chat_json("你是回声服务，原样返回 JSON。", '{"ping": 1}')
        results["model_identity"]["probe_response_keys"] = (
            sorted(probe.keys()) if isinstance(probe, dict) else str(probe)[:80]
        )
        results["model_identity"]["probe_latency_s"] = round(time.time() - t0, 2)
        print("probe ok:", results["model_identity"], flush=True)

        # ── 环节①：现行 prompt 真提取（预期：英文漂移复现）─────────────
        print("== 环节① 现行 prompt 真提取 ==", flush=True)
        for sid, text in UTTERANCES.items():
            rec = _extract_one(sid, text)
            results["live_current_prompt"][sid] = {"utterance": text, **rec}
            print(f"  {sid}: cjk={rec.get('cjk_ratio')} {str(rec.get('summary'))[:90]}", flush=True)

        # ── 环节②：双通道失配（确定性，无 LLM）────────────────────────
        print("== 环节② 双通道失配（确定性）==", flush=True)
        recorded = _recorded_english_summaries()
        pairs = {
            "q_db": {"english": recorded.get("u1", ""), "chinese": UTTERANCES["u1"]},
            "q_allergy": {"english": recorded.get("u5", ""), "chinese": UTTERANCES["u5"]},
        }
        for qid, q in QUERIES.items():
            ev = {"query": q}
            for face, doc in pairs[qid].items():
                ev[face] = {
                    "summary": doc,
                    "cosine_3gram_hash": cosine(embed_for_eval([q])[0], embed_for_eval([doc])[0])
                    if doc
                    else None,
                    "gram3_overlap": gram_overlap(q, doc) if doc else None,
                }
            results["channel_analysis"][qid] = ev
            print(
                f"  {qid}: en_cos={ev['english']['cosine_3gram_hash']} en_gram={ev['english']['gram3_overlap']} "
                f"zh_cos={ev['chinese']['cosine_3gram_hash']} zh_gram={ev['chinese']['gram3_overlap']}",
                flush=True,
            )

        # ── 环节③：语言锁定 prompt 真提取（预期：摘要回中文）──────────
        print("== 环节③ 语言锁定 prompt 真提取 ==", flush=True)
        old = extractor_mod.EXTRACT_SYS
        extractor_mod.EXTRACT_SYS = LOCKED_EXTRACT_SYS
        try:
            for sid, text in UTTERANCES.items():
                rec = _extract_one(sid, text)
                results["live_locked_prompt"][sid] = {"utterance": text, **rec}
                print(
                    f"  {sid}: cjk={rec.get('cjk_ratio')} {str(rec.get('summary'))[:90]}",
                    flush=True,
                )
        finally:
            extractor_mod.EXTRACT_SYS = old

    # ── 判定 ──────────────────────────────────────────────────────────
    cur = [
        r
        for r in results["live_current_prompt"].values()
        if r.get("ok") and not r.get("fallback_suspect")
    ]
    lock = [
        r
        for r in results["live_locked_prompt"].values()
        if r.get("ok") and not r.get("fallback_suspect")
    ]
    drift_confirmed = (
        bool(cur) and sum(1 for r in cur if (r.get("cjk_ratio") or 0) < 0.3) / len(cur) >= 0.5
    )
    lock_holds = (
        bool(lock) and sum(1 for r in lock if (r.get("cjk_ratio") or 0) > 0.5) / len(lock) >= 0.5
    )
    mismatch = False
    if results["channel_analysis"] and all(
        ev["english"]["summary"] and ev["chinese"]["summary"]
        for ev in results["channel_analysis"].values()
    ):
        # 判据口径：FTS 面看字面 gram 交叠（0=通道死）；向量面看相对信号强度
        # （512 维 hash 有碰撞噪声，英文摘要 cos 绝对值不趋零，但显著弱于中文对照）
        mismatch = all(
            ev["english"]["gram3_overlap"] == 0
            and (ev["english"]["cosine_3gram_hash"] or 0)
            < (ev["chinese"]["cosine_3gram_hash"] or 0)
            and (ev["chinese"]["gram3_overlap"] or 0) > 0
            for ev in results["channel_analysis"].values()
        )
    results["verdict"] = {
        "drift_confirmed": drift_confirmed,
        "lock_holds": lock_holds,
        "channel_mismatch_confirmed": mismatch,
        "valid_current_samples": len(cur),
        "valid_locked_samples": len(lock),
        "note": "环节①③各 3 次真调用；fallback_suspect 样本已剔除，不计入判定",
    }
    (HERE / "probe_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
