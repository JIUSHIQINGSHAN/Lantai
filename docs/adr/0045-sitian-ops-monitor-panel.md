# ADR-0045: 司天——后台运行监控面板（进程/存储/调度/请求遥测 + 规则告警）

**日期**: 2026-09-06
**状态**: Accepted
**决策者**: 大哥
**来源**: 悬镜（ADR-0038）解决了「记忆内容」的可视化管理，但「系统自身跑得怎么样」仍要靠人肉
`curl /stats` + 翻日志；ADR-0040 第 3 条承诺的 `OperationLog` 遥测表建表至今从未被写入。

---

## 背景

兰台已有的可观测面是分散的：

| 已有 | 回答什么 | 缺口 |
|------|----------|------|
| `GET /health` `/health/deep` | 活没活、依赖通不通 | 无历史、无趋势，探活一次 LLM 有成本 |
| `GET /stats` `/usage` | 记忆总量与分布 | 无进程/存储/请求维度 |
| `/ui/pulse` 脉搏页 | 存量、分布、worker 上次运行 | 静态快照，无告警、无请求耗时 |
| `scheduler_run` / `reflect_run` | worker 跑没跑、跑得怎么样 | 要自己算逾期，跨表拼 |
| `RetrievalEvent` + `recall_report` | 零召回率、token 粗估 | 与运行指标不同源，看板不合并 |
| `OperationLog`（ADR-0040） | —— | **建表后从未写入，死表** |

结果是：接口变慢、worker 停摆、待审堆积、SQLite 膨胀、非回环裸奔——这些「运维必须第一时间知道」
的事实，系统内部其实都有记录，但没有任何一处把它们拼成一张能下判断的图。

## 决策

新增 **「司天」（Sitian）后台运行监控面板**：一处装配、三处消费（Web 面板 / REST / Prometheus）。

### 1. 采集层（`lantai/observability/metrics.py`）

- 进程内 `MetricsCollector`：最近 N 条请求环形缓冲 + 分钟级聚合桶，一次 `record()` 喂两个视图；
- 零第三方依赖（不引入 psutil / prometheus_client）：RSS 读 `/proc/self/status`，
  非 Linux 退 `getrusage` 峰值；uptime 用 `time.monotonic()`；
- `normalize_route` 把 `/memory/mem_01J8…` 归一成 `/memory/{id}`，杜绝高基数打散统计；
- 采集失败只记日志，绝不冒泡（宁 miss 不脏写）。

### 2. 遥测层（`lantai/observability/telemetry.py`）

- 纯 ASGI 中间件 `TelemetryMiddleware`（不用 `BaseHTTPMiddleware`：少一层 task/流包装）；
- 补齐 ADR-0040 的 `OperationLog`：**只采样落库**——5xx/4xx 与超过
  `MONITOR_PERSIST_SLOW_MS` 的请求必留，其余按 `MONITOR_PERSIST_SAMPLE` 取 1/N；
  后台线程每 `MONITOR_FLUSH_SECONDS` 批量写，顺带按 `MONITOR_RETENTION_DAYS` 清理。
  理由：每请求一次 SQLite 写入会把写放大直接压到记忆主链路上。

### 3. 聚合层（`lantai/ops/monitor.py`）

- `build_monitor_snapshot(session)` 一次装配：process / storage / memories / review /
  pipeline / scheduler / requests / quality / security / dependency；
- **口径复用而非复制**（ADR-0001 门面铁律）：记忆分布用 `ops.overview.build_overview`，
  worker 周期用 `work_item_service.worker_schedule_specs`，零召回用 `recall_report`；
- 逾期判定上收为 `core.scheduler.worker_staleness` 纯函数，**案牍与司天共用同一口径**
  （此前该规则内联在 `project_work_items` 里，两处实现必然漂移）；
- `evaluate_alerts(snapshot)`：13 条规则告警——调度器停摆 / worker 逾期（超 2 个周期升 critical）/ 反思失败 / 反思降级 / 参数建议失败 / 5xx 错误率 / p95 延迟 / 零召回率 / 待审候选积压 / SQLite 体积 / 鉴权 dev 回退放行 / 非回环绑定无 API Key / 缺 LLM Key；
  只依据可核对的指标，阈值全部走 settings（ADR-0002 零硬编码）；
- `render_prometheus(snapshot)`：同一快照的文本视图，外部监控可直接抓取；
- `safe_settings_view()`：运行配置只读视图，**密钥类一律打码，DATABASE_URL 只留文件名**。

### 4. 接口层（`lantai/api/routes_monitor.py`，走 `get_current_user` 鉴权）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/monitor/overview` | 全量快照 + 告警（`?quality=false` 走轻量口径） |
| `GET` | `/monitor/series?minutes=60` | 分钟级趋势（吞吐/错误/平均延迟） |
| `GET` | `/monitor/logs?only_problems=true` | `operation_logs` 落库遥测 |
| `GET` | `/monitor/config` | 生效配置（密钥打码） |
| `GET` | `/monitor/prometheus` | Prometheus 文本格式 |
| `POST` | `/monitor/workers/{name}/run` | 手动触发 worker（复用 `worker_operation_service`，同名互斥） |

### 5. 展示层（`lantai/api/ui/monitor.js`）

- 延续 ADR-0025：零构建原生 ES Module，由 `/ui/assets/` 同源托管，不新增 npm/CDN；
- 悬镜工作台新增「司天监控」视图：指标卡 / 告警 / 服务与依赖 / 请求趋势（纯 SVG，
  不引图表库）/ 端点耗时排行 / 记忆管道 / 调度器与 worker（可一键手动运行）/ 问题请求 / 运行配置；
- 侧边栏徽标用 `?quality=false` 轻量轮询，10 秒自动刷新、页面隐藏即暂停。

## 备选方案

1. **接入 Prometheus + Grafana**：能力最强，但违背「本地优先、零外部依赖、单进程开箱即用」，
   个人部署要额外起两个服务。→ 只保留 `/monitor/prometheus` 文本出口，想接的人自取。
2. **在 `/stats` 上继续加字段**：单端点越来越胖，且请求级遥测无处安放。→ 拆独立聚合层。
3. **前端引 Chart.js/ECharts**：与 ADR-0025 的零供应链约束冲突。→ 手写 60 行 SVG。

## 影响

- **写入侧**：新增内存采集（可忽略）+ 采样落库（默认 1/20 + 全部错误/慢请求）；
  `MONITOR_ENABLED=false` 可整体关闭，中间件零开销直通。
- **兼容性**：`OperationLog` 表结构不变（`endpoint` 存 `"POST /add"`），老库无需迁移；
  `/stats` `/usage` `/ui/pulse` 全部保留。
- **重构面**：`project_work_items` 的逾期规则改为调用 `worker_staleness`，行为不变
  （周期 + max(25%, 15min) 宽限，超 2 个周期升 critical），既有案牍测试全绿为准。
- **测试**：`tests/test_monitor.py`（采集器/聚合/告警/Prometheus/中间件真实 HTTP 链路，
  不 mock 内部逻辑）+ `tests/test_monitor_ui.py`（静态资源、接口契约、DOM 选择器一致性）。
  后者顺带把「JS 引用了 index.html 不存在的 id」这类静默致命 bug 挡在门外
  （本次就修掉了 `#systemRefresh` 缺失导致 `init()` 抛错、控制台交互全废的回归）。

## 相关

- [ADR-0013](0013-naming-system.md) — 司天命名登记
- [ADR-0025](0025-console-no-build-modules.md) — 无构建 ES Modules 约束
- [ADR-0038](0038-visual-management-studio.md) — 悬镜可视化控制台
- [ADR-0040](0040-auth-telemetry-f13.md) — 鉴权与遥测（本 ADR 补齐其第 3 条）
- [CONTEXT.md](../../CONTEXT.md) — 司天词汇定义
- [规格](../../.scratch/monitor-panel/spec.md) — 行为与验收
