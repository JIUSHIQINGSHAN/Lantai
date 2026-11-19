"""MemProbe 票 03 决策一微验证：assistant/tool 行在两条灌库通道下的保真。

背景（.scratch/v0.25-next-window-2026-10/memprobe-compat-notes.md §四.6）：
suite56 语料 role 分布 user 2693 / assistant 2676 / tool_output 935；
source 探针要答「值由谁引入」（user/assistant/tool/stale_tool）。
ADR-0018 预告对话链导入「只喂 user 行，assistant 行跳过」——本脚本用真实
API（不 mock、不直连库）小样本验证该预告，并为通道决策留证。

对照设计（同一隔离评测库内三段）：
  1. /dialogue 喂 user 口吻事实（fastpath 白名单形态）→ 查检索面；
  2. /dialogue 喂 assistant 口吻事实（Assistant: 前缀原文）→ 看提取后
     说话人是否保留、是否立即可检索；
  3. /import/jsonl 逐字直存同两条（User:/Assistant:/[tool] 前缀）→ 查检索面。
证据落 docs/benchmarks/memprobe-2026-10/decision_channel_microverify.json。

纪律：双闸（独立 LANTAI_HOME + 断言 ≠ 生产路径）；API 单通道；凭据生产
.env 同源最小集；本脚本只做决策验证，不产正式数字。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

# 票 30 A2：报告面 stdout 钉 UTF-8——cp936 宿主 print GBK 外字符（emoji/U+2028/罕用字）直接崩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.memprobe_server_main import (  # noqa: E402
    _assert_isolated_home,
    _launch_owned_server,
    _production_home,
    _required_setting,
    _stop_owned_process,
)

OUT = ROOT / "docs/benchmarks/memprobe-2026-10"
MV_HOME = ROOT / ".scratch/memprobe-suite/mv-home"
PORT = 8792
BASE = f"http://127.0.0.1:{PORT}"
STARTUP_TIMEOUT = 120.0


def _dotenv() -> dict[str, str]:
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


def _assert_double_gate() -> Path:
    ev = _assert_isolated_home(MV_HOME)
    print("[双闸通过] 评测目录已与受保护生产目录隔离")
    return ev


def _server_env() -> dict:
    production = _production_home()
    dotenv = _dotenv()
    env = {
        "PATH": os.environ.get("PATH", ""),
        # 子进程 stdio/日志句柄钉 UTF-8（票 30 A3）：不钉则中文日志按 cp936 落 server.log
        "PYTHONIOENCODING": "utf-8",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
        "HOMEPATH": os.environ.get("HOMEPATH", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "OPENAI_API_KEY": _required_setting("OPENAI_API_KEY", dotenv),
        "OPENAI_BASE_URL": _required_setting("OPENAI_BASE_URL", dotenv),
        "LLM_MODEL": os.environ.get("LLM_MODEL") or _dotenv().get("LLM_MODEL", "Qwen/Qwen3-8B"),
        "EMBED_MODEL": os.environ.get("EMBED_MODEL") or _dotenv().get("EMBED_MODEL", "BAAI/bge-m3"),
        "API_KEY": _required_setting("API_KEY", dotenv),
        "REMEMBRANCE_PROD_HOME": str(production),
        "PORT": str(PORT),
        "LANTAI_HOME": str(MV_HOME.resolve()),
        "DATABASE_URL": f"sqlite:///{(MV_HOME.resolve() / 'remembrance.db').as_posix()}",
        "CHROMADB_PATH": str(MV_HOME.resolve() / ".chromadb"),
        "LANTAI_INTENT_OFF": "1",
        "LANTAI_RUN_SCHEDULER": "false",
        "MONITOR_ENABLED": "false",
        "VERBATIM_IN_RECALL": "true",
        "RERANKER_ENABLED": "false",
    }
    return env


def _headers():
    return {"X-API-Key": _required_setting("API_KEY", _dotenv())}


def _start_server() -> subprocess.Popen:
    env = _server_env()
    MV_HOME.mkdir(parents=True, exist_ok=True)
    log_fh = open(MV_HOME / "server.log", "ab")  # noqa: SIM115 — 进程输出句柄
    try:
        proc = _launch_owned_server(
            [sys.executable, str(ROOT / "scripts/memprobe_server_main.py")],
            cwd=str(ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            timeout=STARTUP_TIMEOUT,
            expected_port=PORT,
        )
    except RuntimeError as exc:
        raise SystemExit(f"评测 API 未通过子进程就绪校验（见 server.log），拒跑: {exc}") from exc
    finally:
        log_fh.close()
    print("[横幅] owned child readiness confirmed", flush=True)
    return proc


def _search(query: str, top_k: int = 8) -> dict:
    r = requests.post(
        f"{BASE}/search",
        json={"query": query, "top_k": top_k, "use_rerank": False},
        headers=_headers(),
        timeout=600,
    )
    r.raise_for_status()
    return r.json()


def _fmt_results(sj: dict) -> list[dict]:
    out = []
    for r in sj.get("results", []):
        mem = r.get("memory", {})
        out.append(
            {
                "lane": mem.get("lane"),
                "memory_type": mem.get("memory_type"),
                "created_at": mem.get("created_at"),
                "tags": mem.get("tags"),
                "content": (mem.get("content") or "")[:200],
            }
        )
    return out


def main(argv=None) -> int:
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    _assert_double_gate()
    server_env = _server_env()
    OUT.mkdir(parents=True, exist_ok=True)
    if MV_HOME.exists():
        import shutil

        shutil.rmtree(MV_HOME, ignore_errors=True)
    evidence: dict = {
        "purpose": "票 03 决策一微验证：assistant/tool 行在对话链 vs 逐字通道下的保真",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {"llm_model": server_env["LLM_MODEL"], "port": PORT, "home": str(MV_HOME)},
        "steps": [],
    }
    proc = _start_server()
    try:
        # 1. user 口吻事实走对话链（fastpath 形态）
        t0 = time.time()
        r1 = requests.post(
            f"{BASE}/dialogue",
            json={"text": "记住：我的健身房换成了 CrossFit Central，以后都在那里训练。"},
            headers=_headers(),
            timeout=600,
        )
        step1 = {
            "call": "POST /dialogue user-fact",
            "latency_s": round(time.time() - t0, 1),
            "status": r1.status_code,
            "resp": r1.json(),
        }
        sj1 = _search("用户的健身房是哪家？")
        step1["search_query"] = "用户的健身房是哪家？"
        step1["search_gate"] = sj1.get("gate")
        step1["search_results"] = _fmt_results(sj1)
        evidence["steps"].append(step1)

        # 2. assistant 口吻事实走对话链（原文带 Assistant: 前缀，模拟会话中 assistant 行）
        t0 = time.time()
        r2 = requests.post(
            f"{BASE}/dialogue",
            json={"text": "Assistant: 你的健身房会员已升级为 premium，从下周一生效。"},
            headers=_headers(),
            timeout=600,
        )
        step2 = {
            "call": "POST /dialogue assistant-fact",
            "latency_s": round(time.time() - t0, 1),
            "status": r2.status_code,
            "resp": r2.json(),
        }
        sj2 = _search("我的会员升级到什么级别了？谁告诉我升级这件事的？")
        step2["search_query"] = "我的会员升级到什么级别了？谁告诉我升级这件事的？"
        step2["search_gate"] = sj2.get("gate")
        step2["search_results"] = _fmt_results(sj2)
        evidence["steps"].append(step2)

        # 3. 同两条事实走 /import/jsonl 逐字直存（会话语义：user/assistant/tool 行各自一条）
        lines = [
            {
                "content": "[Session 1] User: 我的健身房换成了 CrossFit Central，以后都在那里训练。",
                "created_at": "2026-01-01T10:00:00Z",
                "lane": "general",
                "tags": ["MP", "MV-EP01", "sess0", "encoding", "user"],
            },
            {
                "content": "[Session 1] Assistant: 你的健身房会员已升级为 premium，从下周一生效。",
                "created_at": "2026-01-01T10:00:30Z",
                "lane": "general",
                "tags": ["MP", "MV-EP01", "sess0", "encoding", "assistant"],
            },
            {
                "content": "[Session 1] [tool] gym_system: membership check returned tier=premium effective 2026-01-06.",
                "created_at": "2026-01-01T10:01:00Z",
                "lane": "general",
                "tags": ["MP", "MV-EP01", "sess0", "encoding", "tool_output"],
            },
        ]
        payload = "\n".join(json.dumps(l, ensure_ascii=True) for l in lines)
        t0 = time.time()
        r3 = requests.post(
            f"{BASE}/import/jsonl", json={"text": payload}, headers=_headers(), timeout=1800
        )
        step3 = {
            "call": "POST /import/jsonl verbatim",
            "latency_s": round(time.time() - t0, 1),
            "status": r3.status_code,
            "resp": r3.json(),
        }
        sj3 = _search("我的健身房是哪家？")
        step3["search_query"] = "我的健身房是哪家？"
        step3["search_gate"] = sj3.get("gate")
        step3["search_results"] = _fmt_results(sj3)
        sj4 = _search("会员升级是谁说的？")
        step3["search_query_2"] = "会员升级是谁说的？"
        step3["search_results_2"] = _fmt_results(sj4)
        evidence["steps"].append(step3)

        evidence["conclusions_raw"] = {
            "dialogue_user_fact": {
                "ingested": step1["resp"],
                "searchable_immediately": bool(step1["search_results"]),
            },
            "dialogue_assistant_fact": {
                "ingested": step2["resp"],
                "searchable_immediately": bool(step2["search_results"]),
            },
            "verbatim": {
                "imported": step3["resp"].get("imported"),
                "duplicates": step3["resp"].get("duplicates"),
                "invalid": len(step3["resp"].get("invalid", [])),
            },
        }
        (OUT / "decision_channel_microverify.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(evidence["conclusions_raw"], ensure_ascii=False, indent=2))
        print(
            "[done] evidence -> docs/benchmarks/memprobe-2026-10/decision_channel_microverify.json"
        )
        return 0
    finally:
        _stop_owned_process(proc)


if __name__ == "__main__":
    sys.exit(main())
