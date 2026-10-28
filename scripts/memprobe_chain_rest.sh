#!/usr/bin/env bash
# MemProbe 票 03 剩余分段链式跑测（段边界落盘，resume 按 episode_id 跳过已完成）。
# 段 1（lantai seg0）由先行 runner 完成；本链跑其余 11 段 + 三键各一趟全量补漏趟。
# 用法：nohup bash memprobe_chain_rest.sh >chain.log 2>&1 & disown
set -u
REPO="D:/Projects/记忆"
SCRIPT="$REPO/scripts/memprobe_run_formal.sh"
LOG="$REPO/.scratch/memprobe-suite/formal-logs"

step() { # step <key> <seg|all> <描述>
  echo "[$(date +%H:%M:%S)] START $3" >> "$LOG/chain.log"
  bash "$SCRIPT" "$1" "$2" || echo "[$(date +%H:%M:%S)] EXIT $? $3" >> "$LOG/chain.log"
  echo "[$(date +%H:%M:%S)] DONE  $3" >> "$LOG/chain.log"
}

for seg in 0 1 2 3; do step lantai "$seg" "lantai seg$seg"; done
for seg in 0 1 2 3; do step full_context "$seg" "full_context seg$seg"; done
for seg in 0 1 2 3; do step naive_rag "$seg" "naive_rag seg$seg"; done
# 补漏趟：无 --only 全量 56 题，resume 跳过已完成，只补缺口/失败重试
step lantai all "lantai completion-pass"
step full_context all "full_context completion-pass"
step naive_rag all "naive_rag completion-pass"
echo "[$(date +%H:%M:%S)] CHAIN-COMPLETE" >> "$LOG/chain.log"
