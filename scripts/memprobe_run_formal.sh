#!/usr/bin/env bash
# MemProbe 票 03 正式跑测分段启动器（预注册 run_plan：3 键 × 4 段，每段 14 题）
# 用法：memprobe_run_formal.sh <lantai|full_context|naive_rag> <seg 0..3>
# resume 语义：官方 runner 按 episode_id 跳过 report.json 已完成题，段可安全重跑。
set -euo pipefail
KEY="${1:?usage: memprobe_run_formal.sh <lantai|full_context|naive_rag> <seg 0..3>}"
SEG="${2:?usage: memprobe_run_formal.sh <lantai|full_context|naive_rag> <seg 0..3>}"

REPO="D:/Projects/记忆"
MP="$REPO/.scratch/memprobe-suite/MemProbe/MemProbe-main"
EVID="$REPO/docs/benchmarks/memprobe-2026-10"

case "$SEG" in
  0) ONLY="PILOT-01A PILOT-01B PILOT-02A PILOT-02B PILOT-02C PILOT-03A PILOT-03B PILOT-04A PILOT-04B PILOT-03C PILOT-03D PILOT-04C PILOT-04D PILOT-05A" ;;
  1) ONLY="PILOT-05B PILOT-06A PILOT-06B PILOT-06C PILOT-07A PILOT-07B PILOT-08A PILOT-08B PILOT-09A PILOT-09B PILOT-10A PILOT-10B PILOT-11A PILOT-11B" ;;
  2) ONLY="PILOT-12A PILOT-12B PILOT-13A PILOT-13B PILOT-14A PILOT-14B PILOT-14C PILOT-15A PILOT-15B PILOT-15C PILOT-16A PILOT-16B PILOT-17A PILOT-17B" ;;
  3) ONLY="PILOT-17C PILOT-17D PILOT-18A PILOT-18B PILOT-18C PILOT-18D PILOT-19A PILOT-19B PILOT-19C PILOT-19D PILOT-20A PILOT-20B PILOT-20C PILOT-20D" ;;
  all) ONLY="" ;;
  *) echo "seg must be 0..3 or all" >&2; exit 2 ;;
esac

export OPENAI_API_KEY OPENAI_BASE_URL
OPENAI_API_KEY="$(grep '^OPENAI_API_KEY=' "$REPO/.env" | cut -d= -f2-)"
OPENAI_BASE_URL="$(grep '^OPENAI_BASE_URL=' "$REPO/.env" | cut -d= -f2-)"
export LANTAI_PYTHON="D:/Programs/Python312/python.exe"
# openai SDK 新版会读 WinINET 系统代理（127.0.0.1:7897），该代理对 siliconflow
# 偶发隧道挂死（02:11 一次 read 挂 >10min）；兰台服务器侧 requests 直连同端点
# 一直 0.3-3.9s。runner 侧豁免代理走直连（实测 NO_PROXY=* 后零代理连接）。
export NO_PROXY="*" no_proxy="*" HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy=
export MEMPROBE_PORT=8793
export MEMPROBE_STORE_ROOT="$REPO/.scratch/memprobe-suite/stores"
export MEMPROBE_EVIDENCE_DIR="$EVID/ingest_receipts"
mkdir -p "$MEMPROBE_EVIDENCE_DIR" "$REPO/.scratch/memprobe-suite/formal-logs"

ARGS=(--reader-backend openai --reader-model Qwen/Qwen3-8B --output "$EVID/report.json")
if [ -n "$ONLY" ]; then
  ARGS+=(--only $ONLY)
fi
if [ "$KEY" = "lantai" ]; then
  ARGS=("--adapter" "$REPO/scripts/memprobe_lantai_adapter.py:LantaiVerbatimAdapter" "--name" "lantai_verbatim" "${ARGS[@]}")
else
  ARGS=("--system" "$KEY" "${ARGS[@]}")
fi

"$REPO/.scratch/memprobe-suite/venv/Scripts/python.exe" \
  "$MP/src/eval/run_eval.py" "${ARGS[@]}" \
  > "$REPO/.scratch/memprobe-suite/formal-logs/${KEY}_seg${SEG}.log" 2>&1
