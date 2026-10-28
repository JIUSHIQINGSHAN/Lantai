# MemProbe 预注册执行补记（2026-10-28，全量起跑后、消融轮前）

承 `preregistration.json`（缩题规则与决策程序已预注册）。本文件记录 pilot
实测、吞吐归因与缩题决策，全部在正式消融轮开跑**之前**落盘。

## Pilot（PILOT-01A，hybrid-default，独立 pilot tag 文件与 hash）

- 结果 3/4 correct；ingest 702.1s（21 session），总 743.9s；ingest_errors=0。
- coverage 探针 21/21 duplicate → coverage_full=true（`telemetry_hybrid-default-pilot.jsonl`）。
- gate 4/4 过闸，检索 15/15 非空（0.17–0.28s/次）。
- reader Qwen/Qwen3-8B：`enable_thinking=False` 注入生效，无 `<think>` 泄漏，
  4/4 JSON 可解析（官方 scorer 全部消化）。
- 留证：`results_lantai-hybrid-default-pilot.json`、`pilot_summary.json`、
  `tests/test_memprobe_harness.py` 6 passed。

## 吞吐与归因（每条 ~33s 的去向）

- 服务器分段计时 + 交错对照实验（同秒内）：
  - 兰台服务器进程内（uvicorn，/import/jsonl 路径）embed 单调用 33–37s，
    稳定可复现（pilot 21 条、一次性探针服务器 3 条、交错实验 2 条）；
  - **同一时刻**独立进程裸 SDK（同端点同模型同参数）0.09–1.2s；
  - 排除项：端点拥塞（同刻裸 SDK 快）、代理（env 无 *_PROXY、winhttp 直连）、
    IPv6 回退（getaddrinfo AF_INET 在前、IPv4 连接 0.03–0.05s）、遥测写
    （`record_llm_outcome` 0.05s）、线程化本身（plain script 子线程快）、
    重复导入路径（跳过 embed，0.0s）。
- 结论：慢在「uvicorn 服务器进程请求路径内的外呼 HTTPS」，属兰台生产导入
  链路的性能问题（LongMemEval 轮 31.75s/session 同象未诊），**不在本票
  范围内修改生产代码**——作为发现上报，证据链：本文件 + 交错实验记录 +
  `pilot_summary.json` 归因节。
- 影响：灌库吞吐 ~33s/条（非内容大小相关）；全 suite 1243 条 ≈ 11.4h/配置。

## 缩题决策（预注册网格 8/16/24/32/40/48/56）

- 决策规则：hybrid-default 先跑（n=56 起跑，官方 resume 逐题落盘）；在其
  报告达到 **16 集网格点**时评估实际节奏——若端点快窗口成常态（节奏 <5s/条）
  则放行到 24 或更高；否则停在 16，vector-only / bm25-only / refs（参照
  baseline）均以 `--n 16` 接力（**三配置共用同一前缀，只减不换**），分析层
  公共前缀=全集。
- 主数字口径：三配置公共 episode 前缀（`mp_report_layers.py` 自动计算）；
  hybrid-default 若超跑出更多集，超出的部分只作单配置描述性附报。
- 通道完整性：hybrid-default 轮起跑于 explain 增强前，事后对其 16 集逐集
  重放 4 probe 检索（explain=true，只读审计，不改库）补通道分项证据；
  vector-only / bm25-only 轮 adapter 已带 `explain=true`，逐 probe 通道分项
  直接入 telemetry。
