# 08 — 性能基线工具

**What to build:** 移植 aiduMEM 的 50 问中文中性样本，适配 `POST /search` + lanes，输出 P50/P95 延迟。初期只跑 Test 1（基线搜索），Test 2/3 依赖其他能力。放 `scripts/perf_baseline.py`。

**Blocked by:** 06 — search_trace 诊断

**Status:** ready-for-agent

- [ ] `scripts/perf_baseline.py` 存在，包含 aiduMEM 50 问中文样本
- [ ] 对 `POST /search` 跑基线，输出 P50/P95 延迟
- [ ] 支持按 lane 分组输出
- [ ] 可重复运行，结果稳定
