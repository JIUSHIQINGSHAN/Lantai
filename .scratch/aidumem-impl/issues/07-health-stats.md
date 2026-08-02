# 07 — Health + stats 端点

**What to build:** 三个独立端点：`/health` 返回 `{ok: true}`（存活探针，公开，Docker HEALTHCHECK 用）；`/health/deep` 检查 SQLite 可写、ChromaDB collection 存在、LLM 端点可达（需 API Key）；`/stats` 返回记忆总数、按 lane/tier/status 分布、coalesce 缓冲水位、worker 上次运行时间（需 API Key）。不检查 Reranker，不暴露 LLM 调用计数。

**Blocked by:** 04 — Tidal Coalescing 缓冲

**Status:** ready-for-agent

- [ ] `/health` 返回 `{ok: true}`，公开，不需要 Key
- [ ] `/health/deep` 检查 SQLite/ChromaDB/LLM，需要 Key
- [ ] `/stats` 返回记忆分布 + coalesce 水位 + worker 时间，需要 Key
- [ ] 现有 `/health` 兼容（返回格式不变或向后兼容）
- [ ] E2E 测试：三个端点各返回正确结构
