# ADR-0033: 潜移——基于事件驱动的异步记忆摄取管道

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴 Zep 异步事件驱动 Ingestion 架构；彻底消除多轮对话与记忆提纯过程中的大模型网络阻塞与卡顿。

---

## 背景

在以往的兰台设计中，向系统写入一段长对话时（`POST /dialogue`），服务端需要在请求周期内同步等待：
1. 大模型提取（1~3秒）；
2. 候选披沙精炼（1~2秒）；
3. 向量 Embedding 生成（0.5~1秒）；
4. 数据库校雠去重与写入。

整体同步请求耗时常达到 3~6 秒，严重阻塞客户端交互。

---

## 决策

引入**「潜移」（Qianyi）** 异步事件驱动摄取管道：

### 核心机制

1. **非阻塞任务分发**：
   - 客户端调用 `POST /dialogue/async`；
   - 管道立即生成并返回 `task_id` 与状态 `queued`，响应耗时 < 10ms；
   - 后台线程池异步拉起任务执行完整流水线（提取 -> 披沙 -> 去重 -> 入库）。
2. **任务状态追踪与优雅降级（宁 miss 不脏写）**：
   - 任务状态流转：`queued` -> `processing` -> `completed` / `failed`；
   - 异常捕获并记录错误原因，防止主进程崩溃。
3. **接口面开放**：
   - REST：`POST /dialogue/async`、`GET /dialogue/tasks/{task_id}`
   - MCP：`dialogue_add_async`、`dialogue_task_status`

---

## 理由

1. **名实相副**：「潜移」出自《文心雕龙》“潜移暗引，莫之能知”，后台静默处理，前端交互行云流水。
2. **极致低延迟**：将前端阻塞时间从数秒降至数毫秒，彻底消除大模型网络抖动对前端的负面影响。

---

## 影响

- 服务：新增 `lantai/services/async_ingest_service.py`。
- 路由与工具：更新 `lantai/api/routes_dialogue.py`，新增 MCP `dialogue_add_async` 与 `dialogue_task_status`。
- 测试：`tests/test_async_ingest.py`（真实不 mock 冒烟单测）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 潜移命名登记
- [ADR-0030](0030-candidate-refine.md) — 披沙候选递归精炼
- [CONTEXT.md](../../CONTEXT.md) — 潜移词汇定义
