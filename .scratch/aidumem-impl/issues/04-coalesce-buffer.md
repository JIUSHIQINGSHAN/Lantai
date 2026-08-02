# 04 — Tidal Coalescing 缓冲

**What to build:** 实现 Tidal Coalescing（潮波合并）——短消息异步缓冲合并，减少 LLM 提取调用。缓冲键 = `user_id + lane`，按 lane 分档定义冲刷参数（`LANE_COALESCE_PROFILES`）。`/add` 开关切换（`COALESCE_ENABLED` 默认 false 向后兼容），一个入口自动分流同步/异步路径。

**Blocked by:** 02 — 基础设施栈

**Status:** ready-for-agent

- [ ] `LANE_COALESCE_PROFILES` 配置存在，初版全用 aiduMEM 默认值（idle 4s / window 12s / max_parts 8 / max_chars 2000）
- [ ] 缓冲键 = `user_id + lane`，不同用户/lane 不混
- [ ] `COALESCE_ENABLED` 默认 false（向后兼容），true 时走缓冲路径
- [ ] `/add` 一个入口自动分流同步/异步
- [ ] 缓冲冲刷后调用 LLM 提取，合并多条短消息
- [ ] E2E 测试：coalesce 开启时多条短消息合并为一次提取
