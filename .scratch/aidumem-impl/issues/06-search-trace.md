# 06 — search_trace 诊断

**What to build:** `/search` 加 `?trace=true` 参数，返回体加 `trace` 字段——每步诊断数组：`{step, elapsed_ms, candidate_count, score_range}`。按需开启，只记耗时和计数，不记中间结果内容，overhead < 1ms。不开独立端点。

**Blocked by:** 02 — 基础设施栈

**Status:** ready-for-agent

- [ ] `/search?trace=true` 返回 `trace` 数组，每步含 step/elapsed_ms/candidate_count/score_range
- [ ] 不传 `trace=true` 时无额外开销
- [ ] trace 只记耗时和计数，不记中间结果内容
- [ ] overhead < 1ms
- [ ] E2E 测试：trace 开启时返回完整步骤数组
