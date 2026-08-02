# 健康探针与 stats 端点范围

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 有健康探针和 stats 端点。remembrance 当前只有一个 `{ok: true}` 的 `/health`。

需要决定：

1. **健康探针深度**：检查哪些依赖？LLM（ping embed 端点）、embedding（维度校验）、ChromaDB（collection 存在）、SQLite（可写）、Reranker（可选）？
2. **stats 端点暴露什么**：记忆总数、按 lane/tier/status 分布、coalesce 缓冲状态、worker 上次运行时间、LLM 调用计数？
3. **鉴权**：stats/health 是否需要 API Key？还是公开？
4. **实现形式**：一个端点还是分多个（`/health`、`/stats`、`/health/dependencies`）？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

四项决议（grilling 2026-08-02 与用户确认）：

### 1. 健康探针深度 → 两级

- `/health`：简单 `{ok: true}`（存活探针，Docker HEALTHCHECK 用）
- `/health/deep`：检查 SQLite 可写、ChromaDB collection 存在、LLM 端点可达
- 不检查 Reranker（可选组件）

### 2. stats 暴露 → 记忆总数 + 分布 + coalesce 水位 + worker 时间

- 记忆总数、按 lane/tier/status 分布
- coalesce 缓冲水位
- worker 上次运行时间
- 不暴露 LLM 调用计数（需要额外埋点，留给后续）

### 3. 鉴权 → /health 公开，其余需 Key

- `/health` 公开（Docker HEALTHCHECK 用）
- `/health/deep` 和 `/stats` 需要 API Key

### 4. 实现形式 → 三个端点

- `/health`、`/health/deep`、`/stats` 三个独立端点，用途不同不合并
