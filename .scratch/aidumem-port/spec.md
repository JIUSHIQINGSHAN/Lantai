# Spec: aiduMEM 移植——Remembrance-System v0.3.0

**Status**: ready-for-agent
**Date**: 2026-08-02
**ADRs**: 0001-0007
**Map**: `.scratch/aidumem-port/map.md`

## Problem Statement

Remembrance-System v0.2.0 有完整的记忆链路（摄取→闸门→演化→检索→遗忘），但存在三类问题：

1. **写入无节流**：每条短消息都触发一次 LLM 提取，成本高、延迟大
2. **全链不可观测**：检索是黑盒，无法诊断哪步慢、哪步丢了候选
3. **数据无自治**：记忆只增不删，重复记忆堆积，衰减后仍留在检索池
4. **集成无标准**：没有 CLI 注入或 MCP 协议，Agent 接入只能走 REST
5. **部署无工程化**：无 Docker、无 CI/CD、无运维脚本
6. **代码有硬编码**：路径写死、P0 bug（`DEFAULT_LANE` 缺失）、死代码残留

## Solution

吸收 aiduMEM 的六组设计思想，以逐案确定的技术栈落地：

- **写入节流**：Tidal Coalescing（潮波合并）+ fastpath 白名单直写
- **全链可观测**：search_trace 每步诊断 + health/stats 端点 + perf baseline 工具
- **数据自治**：archived 归档（只降权不删）+ 余弦相似度去重
- **可插拔集成**：Shell Hook（零依赖 CLI 注入）+ MCP server（标准协议写操作）
- **克隆即跑**：Docker 多阶段构建 + GH Actions → GHCR + 运维脚本
- **架构有纪律**：门面铁律（只搬家不改语义）+ 零硬编码 + service 层下沉

## User Stories

### 地基（Phase 1）

1. 作为开发者，我希望 `settings.DEFAULT_LANE` 存在且有默认值 `"general"`，这样 `promoter.py` 不再报 `AttributeError`
2. 作为开发者，我希望 `settings.VECTOR_DIMENSION` 被删除，让 ChromaDB 自推断维度，这样切换 embedding 模型不需要手动改维度
3. 作为开发者，我希望所有路径通过 `REMEMBRANCE_HOME` 环境变量或 `__file__` 自解析，不硬编码绝对路径
4. 作为开发者，我希望 `settings.validate()` 只 warn 不 crash，这样缺少可选配置不会阻止启动
5. 作为开发者，我希望 `auth.py` 的三个死代码函数被删除，保持代码库整洁
6. 作为开发者，我希望路由 handler 的业务逻辑下沉到 service 层，handler 只做 HTTP 解析/返回
7. 作为开发者，我希望重构后所有旧 import 路径保持可用（门面铁律），这样不破坏已有调用方

### A. 写入节流（Phase 2A）

8. 作为 Agent，我希望短消息被缓冲合并后再提取，这样减少 LLM 调用次数和成本
9. 作为 Agent，我希望缓冲按 `user_id + lane` 分键，这样不同用户、不同类型的记忆不混在一起
10. 作为开发者，我希望通过 `COALESCE_ENABLED` 开关切换同步/异步路径，默认 false 向后兼容
11. 作为开发者，我希望不同 lane 有不同的冲刷参数（idle timeout / window / max_parts / max_chars），通过 `LANE_COALESCE_PROFILES` 配置
12. 作为 Agent，我希望「我叫张三」「我喜欢Python」「记住：明天开会」这类句型绕过 LLM 直接写入（fastpath），这样省一次提取调用
13. 作为开发者，我希望 fastpath 只覆盖三类句型（自我声明、偏好表达、显式指令），用正则匹配，precision ≥ 95%
14. 作为开发者，我希望 fastpath 命中后直接返回 `fastpath_candidate`，不入 coalesce 缓冲
15. 作为开发者，我希望 coalesce 冲刷参数可校准：用 aiduMEM 50 问句模拟输入，测合并率/批大小/P50/P95 延迟

### B. 全链可观测（Phase 2B）

16. 作为开发者，我希望 `/search?trace=true` 返回每步诊断数组（step / elapsed_ms / candidate_count / score_range），overhead < 1ms
17. 作为开发者，我希望 trace 按需开启，只记耗时和计数，不记中间结果内容
18. 作为运维者，我希望 `/health` 返回 `{ok: true}` 作为存活探针（Docker HEALTHCHECK 用，公开）
19. 作为运维者，我希望 `/health/deep` 检查 SQLite 可写、ChromaDB collection 存在、LLM 端点可达（需 API Key）
20. 作为运维者，我希望 `/stats` 返回记忆总数、按 lane/tier/status 分布、coalesce 缓冲水位、worker 上次运行时间（需 API Key）
21. 作为开发者，我希望有性能基线工具：用 aiduMEM 50 问中文中性样本跑 `POST /search`，输出 P50/P95 延迟

### C. 数据自治（Phase 2C）

22. 作为系统，我希望 decay_score 降到极低时自动将记忆转为 archived 状态
23. 作为系统，我希望 archived 记忆不参与检索（`WHERE status='active'`），但物理不删除
24. 作为系统，我希望保持现有 `_lane_strength` 指数衰减不变
25. 作为系统，我不做 GC——单用户 SQLite 存储量不是瓶颈（10 万条 < 100MB）
26. 作为系统，我希望在 MemoryCandidate 创建时、gate 之前做余弦相似度去重
27. 作为系统，我希望高相似度候选直接 merge/update，不需要再过 gate
28. 作为系统，我希望去重用余弦相似度而非 Jaccard（对中文分词不敏感），阈值需实测标定

### D. 可插拔集成（Phase 2D）

29. 作为 Agent 框架，我希望通过 Shell Hook（stdin JSON → stdout Markdown 上下文）注入记忆，零依赖
30. 作为 Agent 框架，我希望 Shell Hook 2 秒超时返回空 `{}`，不阻塞
31. 作为 Agent 框架，我希望 Shell Hook 对 ≤ 3 字符的输入不注入
32. 作为 Agent 框架，我希望 Shell Hook 固定 top_k=5，不开 rerank（低延迟优先）
33. 作为 Agent 框架，我希望 Shell Hook 返回 Markdown 列表格式，带分数标注
34. 作为 Claude Desktop 用户，我希望通过 MCP server 做 `search`/`add`/`feedback` 三个操作
35. 作为开发者，我希望 Shell Hook 和 MCP server 并存：Hook 做读（注入），MCP 做写（操作）

### E. 工程化（Phase 2E）

36. 作为运维者，我希望有 `Dockerfile`：`python:3.11-slim` 多阶段构建，volume 挂载 `/data`
37. 作为运维者，我希望有 GH Actions：tag → wheel → Docker → GHCR（amd64 起步）
38. 作为运维者，我希望 Docker `HEALTHCHECK` 指向 `/health` 端点
39. 作为运维者，我希望有 `scripts/backup.py` 备份 SQLite + ChromaDB + .env.example
40. 作为运维者，我希望有 `scripts/restore.py` 停服→覆盖文件→重启
41. 作为运维者，我希望有 `scripts/upgrade_check.py` 检查 schema 迁移、向量维度变更、配置项新增
42. 作为运维者，我希望有 `scripts/reextract.py` 从 RawDocument 重新跑提取链路（默认 dry-run）

### 基础设施

43. 作为系统，我希望保留 ChromaDB 作为向量存储，删除 `MemoryItem.embedding` JSON 列消除冗余
44. 作为系统，我希望 BM25 中文分词用 jieba（`content.split()` → `jieba.lcut()`）
45. 作为系统，我希望 embedding 模型统一为 BAAI/bge-m3
46. 作为系统，我不引入 mem0 组件（与现有 gate/evolution/forgetting 链路冲突）
47. 作为系统，我不引入 Qdrant（需要外部进程，当前阶段不引入运维负担）

## Implementation Decisions

### 技术栈

| 组件 | 决策 | 来源 |
|------|------|------|
| 向量存储 | 保留 ChromaDB，删 `MemoryItem.embedding` 列 | 票据 01 / ADR-0004 |
| 中文分词 | jieba | 票据 01 / ADR-0004 |
| embedding | BAAI/bge-m3 统一 | 票据 01 / ADR-0004 |
| 外部组件 | 不引入 mem0 / Qdrant | 票据 01 / ADR-0004 |

### 架构约束

- **门面铁律**（ADR-0001）：重构只搬家不改语义，旧 import 路径保持可用
- **零硬编码**（ADR-0002）：`REMEMBRANCE_HOME` 外部变量 + `__file__` 自解析 + `validate()` 只 warn
- **service 层**：路由 handler 业务逻辑下沉到 service 函数，handler 只做 HTTP 解析/返回
- **死代码清理**：`auth.py` 三个未使用函数删除

### Coalesce 设计

- 缓冲键 = `user_id + lane`（不引入 session）
- lane 替代三档 profile，新增 `LANE_COALESCE_PROFILES` 配置
- `/add` 开关切换：`COALESCE_ENABLED` 默认 false（向后兼容）
- 初版全用 aiduMEM 默认值（idle 4s / window 12s / max_parts 8 / max_chars 2000）
- fastpath 命中直接返回 `fastpath_candidate`，不入缓冲

### Fastpath 设计

- 三类句型：自我声明、偏好表达、显式指令
- 3-5 条正则覆盖，放 `parsing/fastpath.py`
- precision ≥ 95%，不设 recall（宁 miss 不脏写）
- 缓冲前判断

### 可观测性设计

- `search_trace`：数组，每步 `{step, elapsed_ms, candidate_count, score_range}`
- `/search?trace=true` 按需开启，overhead < 1ms
- `/health`：简单 `{ok: true}`，公开
- `/health/deep`：检查 SQLite / ChromaDB / LLM，需 Key
- `/stats`：记忆分布 + coalesce 水位 + worker 时间，需 Key

### 遗忘与去重设计

- decay_score 极低 → 自动转 archived
- archived 不参与检索（`WHERE status='active'`）
- 不做 GC，永不物理删除
- 去重用余弦相似度（不用 Jaccard），在 candidate 创建时、gate 之前
- 预测阈值 0.80/0.65（需实测标定）

### 集成设计

- **Shell Hook**（ADR-0006）：stdin `{user_message, ...}` → stdout `{context: "..."}` 或 `{}`
  - 2s 超时返回空，`SHELL_HOOK_TIMEOUT` 可配置
  - ≤ 3 字符不注入
  - top_k=5，不开 rerank
  - Markdown 列表格式：`- [0.92] 内容`
- **MCP server**（ADR-0007）：提供 `search`/`add`/`feedback` 三个 tool
- 两者并存：Hook 读，MCP 写
- manifest.json 不需要（MCP 自带约定）

### 部署设计

- `python:3.11-slim` 多阶段构建
- volume 挂载 `/data`（`remembrance.db` + `.chromadb/`）
- `REMEMBRANCE_HOME` 在 Docker 内指向 `/data`
- GH Actions：tag → wheel → Docker → GHCR（amd64 起步）
- `HEALTHCHECK CMD curl -f http://localhost:8767/health || exit 1`

### 运维脚本

- `scripts/backup.py` — 备份 SQLite + ChromaDB + .env.example
- `scripts/restore.py` — 停服 → 覆盖 → 重启
- `scripts/upgrade_check.py` — schema diff + 向量维度 + 配置项
- `scripts/reextract.py` — 从 RawDocument 重新跑提取（默认 dry-run）

## Testing Decisions

### 缝隙策略

| 缝隙 | 层级 | 适用范围 | 现有模式 |
|------|------|----------|----------|
| **E2E / API** | 最高 | 所有 HTTP 端点、coalesce、gate、search、trace、health/stats | `test_e2e.py` 的 `TestClient(app)` + 内存 SQLite + mock LLM/vector |
| **纯逻辑单元** | 低 | fastpath 正则、coalesce buffer 管理、decay 计算、dedup 阈值 | `test_prefilter.py` 直接调函数 |

优先用 E2E 缝隙（最高位），纯逻辑函数（无外部依赖）才用单元缝隙。不新增第三种。

### 测试原则

- 只测外部行为，不测实现细节
- E2E 测试用 `TestClient(app)` + 内存 SQLite + mock LLM/vector_store，不连真实服务
- 单元测试只覆盖无外部依赖的纯函数
- perf baseline 工具不是测试——是基准脚本，放 `scripts/`

### Prior Art

- `test_e2e.py`：E2E 模式（TestClient + mock + 内存 DB）
- `test_auth.py`：鉴权模式（patch settings + TestClient）
- `test_prefilter.py`：纯函数单元测试模式

## Out of Scope

- v13 联邦多 Agent（remembrance 当前单用户单实例）
- instinct_graduation（记忆蒸馏成 skill）
- autodream 7 天周期蒸馏
- checkpoint 五段会话快照
- salience 冲突降权（反义词对碰撞）
- Qdrant 迁移
- mem0 组件引入
- CLI 子命令框架（click/typer）
- arm64 Docker 镜像
- LLM 调用计数埋点

## Further Notes

### 实施顺序

1. **地基**：16 门面 + 13 零硬编码（含 P0 修 `DEFAULT_LANE`）
2. **A 写入节流**：02 coalesce · 03 参数 · 04 fastpath
3. **B 可观测**：05 trace · 06 health/stats · 07 perf baseline
4. **C 数据治理**：08 遗忘 · 09 去重
5. **D 集成部署**：10 Shell Hook · 11 MCP · 12 manifest（关闭）
6. **E 工程化**：14 Docker · 15 运维脚本

### 依赖关系

- 02 依赖 13（`LANE_COALESCE_PROFILES` 需要 settings 支撑）
- 03 依赖 02（参数校准需要 coalesce 实现）
- 04 依赖 02（fastpath 在 coalesce 缓冲前判断）
- 08 依赖 01（遗忘语义需要确定技术栈）
- 09 依赖 01（去重用余弦需要 embedding 模型确定）
- 12 依赖 11（manifest 形态取决于 MCP 决策）
- 14 依赖 01（Dockerfile 取决于技术栈）
- 05、06 独立
- 07 独立
- 10、11 独立
- 15 独立

### Fog（待后续出票）

- salience 冲突降权与 contradiction gate 整合
- autodream 7 天周期蒸馏
- checkpoint 五段会话快照

### 参照系

aiduMEM 代码在 `C:\Users\Asus\Desktop\aiduMEM`（只读参照，移植设计思想与参数区间，代码自己写）。
