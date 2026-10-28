# 并行工作线探索性产物（2026-10-28 归档）

本目录是同日另一条并行会话对票 03 的探索性实现产物，**被绑定预注册
（`../preregistration.json`）的正式方案取代**，不进报告数字表（正式数字
唯一来源 = `report.json` + `summary.json`，见预注册 exploratory_declaration）。

保留原因：其中 33s/条吞吐问题的独立诊断与交错对照实验是
`preregistration.json` `lantai_config.telemetry_nop_patch` 结论的旁证
（两条线独立复现同一现象：服务器路径内 embed 步 33s、裸 SDK 同刻 0.1s）。

## 内容

- `results_lantai-hybrid-default-pilot.json` / `telemetry_hybrid-default-pilot.jsonl`：
  探索性 pilot（PILOT-01A，3/4 correct，coverage 探针 21/21，行格式为
  **每 session 一行**——与正式方案的每消息一行不同，故不可合并）
- `results_lantai-hybrid-default.json` / `telemetry_hybrid-default.jsonl`：
  正式方案定稿前误起的全量跑（跑至 1 集后主动终止，config_hash ca7c9083）
- `pilot_summary.json` / `prereg_addendum_2026-10-28.md`：当时的吞吐归因与
  缩题决策草案（缩题方案已被正式预注册「全量跑不缩题」取代；归因的最终
  机制以正式预注册与并行线票 03 Comments 2026-10-28 02:58 诊断为准——
  灌库 33s/条 = sqlite RESERVED 锁上遥测写等满 busy_timeout 30s；reader
  间歇挂死 = openai SDK 读 WinINET 系统代理 7897 隧道偶发挂死 + 官方
  `_call_openai` 无 timeout。本目录文中「端点拥塞/间歇排队」为当时误判，
  保留作诊断过程留痕，勿引用）
- `server.log`：上述跑测的服务器日志

对应脚本（scripts/mp_*.py 与 tests/test_memprobe_harness.py）归档于
`.scratch/memprobe-suite/parallel-mp-superseded/`。
