"""MemProbe suite56 × 兰台 verbatim 通道 adapter（票 03）。

协议（MemProbe 官方 runner，src/eval/run_eval.py::MemorySystemAdapter）：
  reset(episode_id)   → 每题独立兰台评测库（q_<eid>，双闸）+ 独立服务器进程
  ingest(session)     → 该题 sessions 按序逐个 /import/jsonl 逐字直存（零 LLM）
  retrieve(question)  → /search top-15，命中 verbatim 原文拼给共享 reader

灌库通道决策（decision_channel_microverify.json 留证）：suite56 语料 role 分布
user 2676+2693 / tool_output 935；对话链通道（ADR-0018）只喂 user 行、且候选
需 evolve worker 才可检索（微验证：fastpath/提取两种事实均不可立即检索、
assistant 提取 48.3s/次）——source 探针（谁引入的值）在对话链通道结构性不可
答。故选逐字通道：测兰台的存储/检索保真，不测维护行为（报告明示）。

行格式（会话语义钉进每行，reader 可见说话人与时序）：
  [Session {i+1} of {N} | {session_type}] User: {text}
  [Session {i+1} of {N} | {session_type}] Assistant: {text}
  [Session {i+1} of {N} | {session_type}] [tool] {text}
时间戳：suite 无墙钟，用逻辑序（2026-01-01 + session_idx 小时 + 行内分钟），
与官方 Graphiti adapter 的 base+hours 同构；ADR-0018「保留原始时间戳」在此
语义 = 保留 suite 时序不压平。

纪律：双闸（每题独立 LANTAI_HOME + 启动器断言 ≠ 生产）；API 单通道（无直连
库写入）；逐 session 回执落盘（宁 miss 不脏写：mismatch 显式留痕不静默）；
每题收尾做幂等重导探针（全 duplicate ⇔ 在库，票 01 coverage 口径）。
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
# 双闸保护对象：历史实配路径 + 本机当前用户默认库（2026-10-28 事故后补：
# 8793 曾被一台lane收窄的兰台实例占用，health 探测撞上它导致 ingest 打错门；
# 该实例拒收未绑定 lane、零落库——见票 03 Comments）
PROD_LANTAI_HOMES = (
    r"C:\Users\Asus\AppData\Local\remembrance-data",
    r"C:\Users\董红阳\AppData\Local\remembrance-data",
)
TS_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

SPEAKER_PREFIX = {"user": "User", "assistant": "Assistant", "tool_output": "[tool]"}


def _dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    p = REPO_ROOT / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().split(" #")[0].strip()
    return out


def build_session_lines(episode_id: str, session: dict, n_sessions: int) -> list[dict]:
    """session → /import/jsonl 行 dict 列表（纯函数，测试直调）。

    每条消息一行（行内带 session 序号/类型/说话人前缀）；时间戳按逻辑序单调。
    """
    sidx = int(session.get("session_idx", 0))
    stype = session.get("session_type", "session")
    lines = []
    for pos, m in enumerate(session.get("dialogue", [])):
        role = m.get("role", "user")
        text = (m.get("content") or "").strip()
        if not text:
            continue
        speaker = SPEAKER_PREFIX.get(role, role)
        sep = " " if speaker.startswith("[") else ": "
        content = f"[Session {sidx + 1} of {n_sessions} | {stype}] {speaker}{sep}{text}"
        ts = TS_BASE + timedelta(hours=sidx, minutes=pos)
        lines.append(
            {
                "content": content,
                "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lane": "general",
                "tags": ["MP", episode_id, f"sess{sidx}", stype, role],
            }
        )
    return lines


def payload_of(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l, ensure_ascii=True) for l in lines)


def format_retrieval(search_json: dict, top_k: int) -> tuple[str, dict]:
    """/search 响应 → (reader 上下文文本, 元信息)。纯函数，测试直调。

    gate 拦截（needs_memory=False）如实返回空串并记 gate_blocked——reader 无
    记忆作答，按 MemProbe 正常计分（miss 走全题分母，不伪造上下文）。
    """
    gate = search_json.get("gate") or {}
    results = search_json.get("results") or []
    meta = {
        "gate_needs_memory": gate.get("needs_memory"),
        "gate_reason": gate.get("reason"),
        "gate_blocked": gate.get("needs_memory") is False,
        "n_results": len(results),
        "top_k": top_k,
    }
    if meta["gate_blocked"]:
        return "", meta
    parts = []
    for r in results[:top_k]:
        mem = r.get("memory") if isinstance(r, dict) else None
        content = (mem or {}).get("content") or ""
        if content:
            parts.append(f"- {content}")
    return "\n".join(parts), meta


class LantaiVerbatimAdapter:
    """黑盒 adapter：兰台 API 单通道（/import/jsonl、/search）。"""

    context_header = "Memory items retrieved from the user's long-term memory store:"

    def __init__(self, work_dir: str = None, **kw):
        self.store_root = Path(
            os.environ.get("MEMPROBE_STORE_ROOT")
            or (work_dir or REPO_ROOT / ".scratch/memprobe-suite/stores")
        )
        self.evidence_dir = Path(
            os.environ.get("MEMPROBE_EVIDENCE_DIR")
            or REPO_ROOT / ".scratch/memprobe-suite/evidence"
        )
        self.port = int(os.environ.get("MEMPROBE_PORT", "8793"))
        self.base = f"http://127.0.0.1:{self.port}"
        self.top_k = int(os.environ.get("MEMPROBE_TOP_K", "15"))
        self.server_python = os.environ.get("LANTAI_PYTHON") or sys.executable
        self._proc = None
        self._ep = None
        self._lines: list[dict] = []
        self._store: list[str] = []
        self._probe_meta: list[dict] = []

    # ---------- server lifecycle ----------

    def _q_dir(self, eid: str) -> Path:
        return self.store_root / f"q_{eid}"

    def _assert_double_gate(self, qdir: Path) -> None:
        ev = str(qdir.resolve())
        for p in PROD_LANTAI_HOMES:
            prod = str(Path(p).resolve())
            if ev == prod or prod in ev or ev in prod:
                raise SystemExit(f"双闸失败：评测目录 {ev} 与生产路径 {prod} 重叠，拒跑")

    def _server_env(self) -> dict:
        d = _dotenv()
        env = {
            "PATH": os.environ.get("PATH", ""),
            # launcher 以脚本路径起 uvicorn(import string)，sys.path[0]=scripts/
            # 不含 repo 根 → "Could not import module lantai.api.app"（2026-10-28）
            "PYTHONPATH": str(REPO_ROOT),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
            "HOMEPATH": os.environ.get("HOMEPATH", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or d["OPENAI_API_KEY"],
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL") or d["OPENAI_BASE_URL"],
            "LLM_MODEL": os.environ.get("LLM_MODEL") or d.get("LLM_MODEL", "Qwen/Qwen3-8B"),
            "EMBED_MODEL": os.environ.get("EMBED_MODEL") or d.get("EMBED_MODEL", "BAAI/bge-m3"),
            "API_KEY": os.environ.get("API_KEY")
            or d.get("API_KEY", "lantai-local-sync-secret-2026-10"),
            # 检索权重钉板 = LongMemEval hybrid-default（0.6/0.25/0.05）
            "RETRIEVAL_W_VECTOR": "0.6",
            "RETRIEVAL_W_BM25": "0.25",
            "RETRIEVAL_W_FTS": "0.05",
            "VERBATIM_IN_RECALL": "true",
            "RERANKER_ENABLED": "false",
            "LANTAI_INTENT_OFF": "1",
            "LANTAI_RUN_SCHEDULER": "false",
            "MONITOR_ENABLED": "false",
            "PORT": str(self.port),
        }
        return env

    def _headers(self) -> dict:
        d = _dotenv()
        return {
            "X-API-Key": os.environ.get("API_KEY")
            or d.get("API_KEY", "lantai-local-sync-secret-2026-10")
        }

    def _start_server(self, eid: str) -> subprocess.Popen:
        qdir = self._q_dir(eid)
        self._assert_double_gate(qdir)
        log_fh = open(qdir / "server.log", "ab")  # noqa: SIM115 — 生命周期=子进程
        env = self._server_env()
        env["LANTAI_HOME"] = str(qdir)
        proc = subprocess.Popen(
            [self.server_python, str(REPO_ROOT / "scripts/memprobe_server_main.py")],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        for _ in range(120):
            try:
                if requests.get(f"{self.base}/health", timeout=3).ok:
                    if proc.poll() is not None:
                        # health 有响应但我们的进程已退出 ⇒ 端口被外来服务器占答
                        # （2026-10-28 事故形态），宁拒跑不打错门
                        raise SystemExit(
                            f"health 来自外来进程（我们的服务器已退出，见 {qdir / 'server.log'}），拒跑 episode {eid}"
                        )
                    return proc
            except requests.RequestException:
                pass
            time.sleep(1)
        proc.kill()
        raise RuntimeError(f"评测 API 120s 未就绪（{qdir / 'server.log'}），拒跑 episode {eid}")

    def _kill_server(self) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                subprocess.run(
                    f"taskkill /F /T /PID {self._proc.pid}",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self._proc = None
        # 兜底清理占用该端口的孤儿监听进程
        try:
            out = subprocess.check_output(
                f"netstat -ano | findstr :{self.port}",
                shell=True,
                text=True,
                errors="ignore",
            )
            for line in out.splitlines():
                parts = line.strip().split()
                if (
                    len(parts) >= 5
                    and parts[1].endswith(f":{self.port}")
                    and parts[3] == "LISTENING"
                ):
                    subprocess.run(
                        f"taskkill /F /PID {parts[4]}",
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception:
            pass

    # ---------- MemProbe protocol ----------

    def reset(self, episode_id: str, episode=None, spec=None) -> None:
        self._kill_server()
        # 端口残活检查（宁 miss 不脏写）：前轮崩溃残留的服务器占着端口时，
        # _start_server 的 health 探活会打到**旧库**（新 uvicorn 绑定失败但
        # 请求命中旧进程）——跨题串库。显式拒跑，先清理再重跑。
        # 给子进程退出释放端口最多 5 秒宽限期（Windows 异步回收）
        for _ in range(10):
            try:
                if not requests.get(f"{self.base}/health", timeout=1).ok:
                    break
            except requests.RequestException:
                break
            time.sleep(0.5)
        else:
            raise SystemExit(
                f"端口 {self.port} 已有活服务器（前轮残活且5s未退出），拒跑 episode {episode_id}；先清理后重跑"
            )
        qdir = self._q_dir(episode_id)
        shutil.rmtree(qdir, ignore_errors=True)
        qdir.mkdir(parents=True, exist_ok=True)
        self._ep = episode_id
        self._n_sessions = len((episode or {}).get("sessions", []))
        self._lines = []
        self._store = []
        self._probe_meta = []
        self._receipts_path = self.evidence_dir / f"{episode_id}_receipts.jsonl"
        self._receipts_path.parent.mkdir(parents=True, exist_ok=True)
        self._receipts_path.write_text("", encoding="utf-8")
        self._t_reset = time.time()
        self._proc = self._start_server(episode_id)

    def ingest(self, session: dict) -> None:
        lines = build_session_lines(self._ep, session, self._n_sessions)
        if not lines:
            return
        self._lines.extend(lines)
        self._store.extend(l["content"] for l in lines)
        payload = payload_of(lines)
        t0 = time.time()
        r = requests.post(
            f"{self.base}/import/jsonl",
            json={"text": payload},
            headers=self._headers(),
            timeout=1800,
        )
        r.raise_for_status()
        rep = r.json()
        expected = len(lines)
        errs = rep.get("errors") or []
        done = (
            rep.get("imported", 0)
            + rep.get("duplicates", 0)
            + len(rep.get("invalid", []))
            + len(errs)
        )
        if done != expected or errs:
            # 半段对账：显式重灌一次（幂等：已落库行记 duplicate），仍不符则留痕继续
            r2 = requests.post(
                f"{self.base}/import/jsonl",
                json={"text": payload},
                headers=self._headers(),
                timeout=1800,
            )
            r2.raise_for_status()
            rep = r2.json()
            errs = rep.get("errors") or []
            done = (
                rep.get("imported", 0)
                + rep.get("duplicates", 0)
                + len(rep.get("invalid", []))
                + len(errs)
            )
        rec = {
            "kind": "session_ingest",
            "session_idx": session.get("session_idx"),
            "session_type": session.get("session_type"),
            "expected_n": expected,
            "imported": rep.get("imported"),
            "duplicates": rep.get("duplicates"),
            "invalid_n": len(rep.get("invalid", [])),
            # errors=服务端逐行拒收（如 lane 越界 403 语义）——必须留痕，不静默
            "errors_n": len(errs),
            "errors_sample": [str(e.get("reason", "?"))[:120] for e in errs[:3]],
            "complete": done == expected and not errs and not rep.get("invalid"),
            "latency_s": round(time.time() - t0, 1),
        }
        self._append_receipt(rec)

    def retrieve(self, question: str) -> str:
        t0 = time.time()
        r = requests.post(
            f"{self.base}/search",
            json={"query": question, "top_k": self.top_k, "use_rerank": False},
            headers=self._headers(),
            timeout=600,
        )
        r.raise_for_status()
        ctx, meta = format_retrieval(r.json(), self.top_k)
        meta.update({"question": question, "latency_s": round(time.time() - t0, 2)})
        self._probe_meta.append(meta)
        self._append_receipt({"kind": "probe_retrieve", **meta})
        return ctx

    def stored_memories(self) -> list:
        # 留证以回执 + 幂等重导探针为准（receipts 文件），不把全库原文塞进 report.json
        return []

    def close(self) -> None:
        # 收尾探针（票 01 口径）：同 payload 幂等重导，全 duplicate ⇔ 全部在库
        if self._lines and self._proc is not None:
            payload = payload_of(self._lines)
            try:
                r = requests.post(
                    f"{self.base}/import/jsonl",
                    json={"text": payload},
                    headers=self._headers(),
                    timeout=1800,
                )
                r.raise_for_status()
                rep = r.json()
                rec = {
                    "kind": "store_presence_reimport",
                    "expected_n": len(self._lines),
                    "duplicates": rep.get("duplicates"),
                    "imported": rep.get("imported"),
                    "invalid_n": len(rep.get("invalid", [])),
                    "errors_n": len(rep.get("errors") or []),
                    "all_present": rep.get("duplicates") == len(self._lines)
                    and not rep.get("invalid")
                    and not rep.get("errors")
                    and not rep.get("imported"),
                }
                self._append_receipt(rec)
            except Exception as exc:  # noqa: BLE001 — 探针失败显式留痕，不影响评分
                self._append_receipt({"kind": "store_presence_reimport", "error": repr(exc)[:200]})
        self._append_receipt({"kind": "episode_done", "episode_id": self._ep})
        self._kill_server()

    def _append_receipt(self, rec: dict) -> None:
        rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._receipts_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
