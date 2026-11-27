# 🧠 兰台记忆（Lantai）— AI Agent 长期记忆引擎

> **记忆不是记事，而是不忘过往的点点滴滴。**
>
> **不只是存储 — 是检索、演化、遗忘的完整闭环。**

**兰台（Lantai）**——取名自汉代皇家档案馆「兰台」，为 AI 建一座不忘过往的记忆档案库。英文代号 Lantai。

```
摄取 → 闸门 → 演化 → 遗忘 → 检索
让 AI 在对的时间，找到对的回忆。
```

[![Version](https://img.shields.io/badge/version-0.70.0-blue.svg)](https://github.com/JIUSHIQINGSHAN/Lantai)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-yellow.svg)](https://www.python.org/)
[![Tests](https://github.com/JIUSHIQINGSHAN/Lantai/actions/workflows/tests.yml/badge.svg)](https://github.com/JIUSHIQINGSHAN/Lantai/actions/workflows/tests.yml)
[![改编自](https://img.shields.io/badge/based%20on-aiduMEM-orange.svg)](https://github.com/monkey2jack/aiduMEM)

当前发布版：**v0.70.0「悬镜」**。更新与升级注意事项见 [CHANGELOG](CHANGELOG.md)，参与开发见 [贡献指南](CONTRIBUTING.md)。

---

## 兰台记忆是什么？

一个为 **AI Agent 提供持久化记忆** 的长期记忆管理系统。不是简单的键值存储，而是让 AI **会记忆、会演化、会遗忘**。

本项目**改编自 [aiduMEM（优忆思）](https://github.com/monkey2jack/aiduMEM)**——移植了相关性闸门、潮波并忆、Ebbinghaus 遗忘、Chronos 时间感知等设计思想（移植过程与决策见 [移植结果文档](docs/aidumem-port-results.md)），并在此基础上重新实现了存储层（SQLite + FTS5 + ChromaDB）、补全了 Shell Hook / MCP 双形态集成与完整安全加固。

| 层级 | 做什么 | 核心特性 |
|------|--------|----------|
| 🧠 **记忆** | 在对的时间找到对的回忆 | **四路混合检索**：向量语义 + jieba BM25 + FTS5 trigram 子串匹配 + 时效衰减 |
| 🔍 **闸门** | 只检索真正相关的内容 | 启发式相关性闸门拦截无关上下文：纠错/社交/热缓存/自我指代/明确回忆/延续/实质内容 |
| 🌊 **潮浪** | 批量 LLM 提取，不逐条调用 | Tidal Coalescing：短消息按 `user_id + lane` 缓冲合并，一次 LLM 调用处理多条 |
| ⚡ **直写** | 高频句型不走 LLM | Fastpath 白名单：自我声明/偏好表达/显式指令三类句型直接写入，宁 miss 不脏写 |
| 📊 **演化** | 知识生长与自我纠错 | Proposal → Apply 全流程，Checkpoint 快照可一键回滚 |
| ⏳ **遗忘** | 遗忘是特性，不是 bug | Ebbinghaus 指数衰减，低分自动归档；归档不参与检索、物理不删 |
| 🕰️ **克罗诺斯** | 时间感知的有效期 | 双时间轴（`valid_from` / `valid_to`），过期事实降权，未生效事实过滤 |
| 🧹 **去重** | 不写垃圾比事后清理便宜 | 余弦预筛 + 结构判别（ADR-0019）：改写直合 / 值变更走待审提案 / 中带 LLM 兜底（0.90 / 0.65 可配） |
| 🔗 **双形态** | 读有 Hook，写有 MCP | Shell Hook（按预算注入上下文）+ MCP server（标准 JSON-RPC 2.0） |
| 🛡️ **护盾** | 安全不是可选项 | 默认回环绑定、非回环强制鉴权、SSRF 防护、原子备份恢复、端点白名单 |
| 📡 **司天** | 系统自己会报告状态 | 后台监控面板：进程/存储/调度/请求遥测 + 规则告警 + Prometheus 出口，一屏看清「现在跑得怎么样」 |
| **悬镜** | 看清记忆关系与来源 | Web 控制台中的拓扑与时序溯源；支持 MCP Apps 的宿主可按权限显示沙箱面板 |

---

## 架构

```
┌──────────────────────────────────────────────────┐
│        🧠 兰台记忆（Lantai）— 记忆引擎           │
│        FastAPI REST API :8767（默认 127.0.0.1）   │
├──────────────────────────────────────────────────┤
│  api/       → 薄路由（逻辑下沉 service 层）        │
│  services/  → 业务逻辑层（门面铁律 ADR-0001）      │
│  gate/      → 启发式预过滤 + 三态去重 + 决策       │
│  ingestion/ → RSS/arxiv 适配器 + 潮波缓冲 + SSRF  │
│  evolution/ → 提案/应用/回滚 + Checkpoint 快照     │
│  retrieval/ → 四路融合检索 + 意图分类 + Reranker   │
│  memory/    → Ebbinghaus 遗忘 + 自动归档          │
│  observability/ → 请求遥测 + 检索事件 + 零召回报告 │
│  ops/       → 只读聚合（概览 / 司天监控 / 星图）   │
├──────────────────────────────────────────────────┤
│  SQLite（结构化 + FTS5 trigram 全文索引）          │
│  ChromaDB（向量存储，cosine）                      │
│  jieba BM25（词级关键词）                         │
└──────────────────────────────────────────────────┘
```

## 快速开始

### 方式一：源码克隆运行

需要 Python 3.11+；当前冻结环境与 CI 使用 Python 3.14。下面固定到已发布标签；参与开发请改走 [贡献指南](CONTRIBUTING.md)，已有 Chroma 0.6.x 数据先读 [迁移指南](docs/chroma-migration.md)，不要直接让新运行时打开旧向量目录。

```bash
# 克隆已发布版本
git clone --branch v0.70.0 https://github.com/JIUSHIQINGSHAN/Lantai.git
cd Lantai
```

**Windows PowerShell：**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
$env:LANTAI_HOME = 'D:\LantaiData'  # 换成独立的数据目录
```

**Linux / macOS（Bash）：**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
export LANTAI_HOME="$HOME/.local/share/lantai"
```

启动前编辑 `.env`：把 `API_KEY` 换成自己的长随机密钥，并按服务商配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL` 和 `EMBED_MODEL`。示例文件显式选用硅基流动，模型名必须与实际端点匹配；缺少模型密钥不妨碍进程启动，但模型提取和向量检索不可用，不能据此视为完整接入。需要冻结依赖时使用 [开发测试环境](docs/project/development-environment.md) 的 `uv sync --locked` 方式。

配置完成后，在同一终端初始化并启动：

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe scripts/init_db.py
.\.venv\Scripts\lantai-server.exe
```

```bash
# Linux / macOS（已激活 .venv）
python scripts/init_db.py
lantai-server
```

服务默认运行在 `http://127.0.0.1:8767`。浏览器访问 `http://127.0.0.1:8767/ui`，在连接设置中填写同一个 API 密钥；`GET /health` 是存活探针，不代表模型或检索链已验证。

### 方式二：Docker 容器运行

先准备已被 Git 忽略的 `.env`，填写自己的密钥和端点支持的模型（也可参照 [.env.example](.env.example)；已有配置请先核对，不要覆盖）：

```dotenv
API_KEY=replace-with-a-long-random-key
OPENAI_API_KEY=replace-with-your-provider-key
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
EMBED_MODEL=BAAI/bge-m3
```

使用已发布的 GHCR 镜像（下方续行语法为 Bash；PowerShell 请合并为一行）：

```bash
docker pull ghcr.io/jiushiqingshan/lantai:v0.70.0
docker run -d --name lantai -p 127.0.0.1:8767:8767 \
  --env-file .env -e LANTAI_HOME=/data \
  -v lantai-data:/data \
  ghcr.io/jiushiqingshan/lantai:v0.70.0
```

也可从上述标签的源码自行构建：

```bash
docker build -t lantai:0.70.0 .
# 将 docker run 的镜像名替换为 lantai:0.70.0
```

> 容器内部默认 `HOST=0.0.0.0`，**必须注入 `API_KEY`**：启动守卫（`assert_secure_binding`）会在非回环地址且无密钥时拒绝运行。示例只把宿主端口发布到 `127.0.0.1`，数据持久化到命名卷 `lantai-data`；改用目录挂载时须允许容器用户 UID 1001 写入。需要远程访问时另配 TLS 与访问控制。
> 
> ⚠️ **部署约束（单进程部署）**：当前版本的后台定时任务调度器（APScheduler）与嵌入式底座（SQLite journal_mode=delete + 内嵌 ChromaDB）按单进程架构设计。请在部署时保持单进程运行（`--workers 1`，默认行为）；多 Worker 运行会导致遗忘与蒸馏任务重复触发及向量库写锁争用。

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/add` | 添加记忆（自动走 fastpath / 潮波 / 三态去重） |
| `POST` | `/search?trace=true` | 混合检索，可返回逐步诊断（intent/vector/decay/final） |
| `POST` | `/gate` | 对候选执行闸门决策 |
| `GET` | `/candidates` `/proposals` | 候选/提案列表 |
| `POST` | `/proposals/{id}/decide` | 批准或拒绝提案 |
| `POST` | `/memory/{id}/rollback` | 回滚记忆到上一版本 |
| `POST` | `/feedback` | 记忆反馈（有用性 / 幻觉风险） |
| `GET` / `PUT` | `/core-memory` | CoreMemory（identity / task / policy） |
| `GET` / `POST` | `/sources` · `/ingest/run` | 摄取源管理 |
| `GET` / `POST` / `DELETE` | `/edges...` | 记忆关系与 supersedes 链 |
| `GET` | `/health` `/health/deep` `/stats` | 健康检查与统计 |
| `GET` | `/monitor/overview` `/monitor/series` | 司天监控快照与分钟级趋势 |
| `GET` | `/monitor/logs` `/monitor/config` | 请求遥测落库记录与生效配置（密钥打码） |
| `GET` | `/monitor/prometheus` | Prometheus 文本格式指标 |
| `POST` | `/monitor/workers/{name}/run` | 手动触发一次 worker（同名互斥） |

### 示例：搜索（带诊断）

以下示例使用 Bash，先将 `API_KEY` 设为与服务端一致的密钥；PowerShell 使用 `curl.exe` 并按其引号规则传 JSON。除公开存活探针外，已配置密钥的接口请求均须鉴权。

```bash
API_KEY='replace-with-the-same-server-key'
curl -s -X POST "http://127.0.0.1:8767/search?trace=true" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "上次我们聊的检索方案是什么？", "top_k": 5}'
```

### 示例：添加记忆

```bash
curl -s -X POST http://127.0.0.1:8767/add \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "项目截止", "content": "项目截止日期是3月15号", "lane": "fact"}'
```

## 兰台记忆的独特之处

### 🔍 相关性闸门（Relevance Gate · 移植自 aiduMEM）

普通 RAG 系统对每条消息都去搜索记忆。本系统的**相关性闸门**用启发式规则判断当前消息是否真的需要记忆检索：纯社交结束语、寒暄直接跳过；纠错、明确回忆、上下文延续立即命中——**无关查询零检索成本**，15 秒热缓存让追问零开销。

### 🌊 潮波并忆（Tidal Coalescing · 移植自 aiduMEM）

短消息不逐条调用 LLM。按 `user_id + lane` 分键缓冲，按 lane 档位（空闲超时/时间窗/条数/字符数）触发冲刷，一次 LLM 调用处理多条消息。

### 🧠 四路混合检索（FTS5 trigram 是亮点）

```
score = 0.6·向量语义 + 0.25·jieba BM25 + 0.05·FTS5 子串命中 + 0.1·时效衰减
```

**FTS5 trigram 子串匹配**补充词级 BM25：连续三字符片段可参与全文匹配，FTS 命中但向量漏掉的记忆可被**追加召回**。它不保证错别字或两字查询能匹配；短查询另有关键词降级路径。上式为默认基础权重，实际结果还受时间、权限、生命周期和可选精排影响。决策见 [ADR-0008](docs/adr/0008-fts5-parallel-recall.md)。

### ⏳ 遗忘曲线衰减（Ebbinghaus Decay · 移植自 aiduMEM）

记忆按 lane、重要度和使用信号计算保持强度，随未使用天数指数衰减；保持强度不等于固定半衰期。低分或超出工作记忆 TTL 的条目可自动归档，高重要度、语义类和程序性记忆有相应豁免。**归档不参与普通检索、物理不删**；Checkpoint 回滚用于撤销记忆版本变更，不是通用的归档撤销入口。

### 🕰️ 克罗诺斯双时间轴（Chronos · 移植自 aiduMEM）

`valid_from` / `valid_to` 时间窗口：未生效记忆直接过滤，过期记忆降权保留。设了时间窗的记忆自动受控。

### ⏱️ 更漏双时间轴（Genglou · 事件时间，ADR-0048）

`valid_from` / `valid_to` 管「何时为真」，`event_time`（+ 精度标记）管「现实何时发生」——两轴分离，迟到更正有锚点。`POST /search` **请求体**传 `as_of` / `as_of_recorded` / `time_from` / `time_to` 查任意历史时点的世界快照、圈时间窗（这些是请求体字段，不是 query 参数——写在 URL 上会被静默忽略、返回当前视图）；无 `event_time` 的记忆**永不硬排除**（宁 miss 不脏写，I4 不变式）。

### 🛰️ 注入回执链（ADR-0049）

每次注入带 `request_id`，宿主回执回填 `receipt_status`（acked / missed）——「AI 到底有没有收到这条记忆」从此可追溯，不是猜。

### 🔌 宿主矩阵（ADR-0051）

同一套记忆注入协议接多个 AI 宿主：Hermes（插件）、Claude Code hooks、Codex CLI——协议归一化在 `lantai/integrations/host_protocol.py`，接新宿主只加一个薄帧映射。安装出口 `scripts/install_host_hooks.py`（默认只打印、不写盘）。

### 📸 Checkpoint 回滚

每次记忆变更生成 `before/after` 快照。改错了？`/memory/{id}/rollback` 一键回到上一版本。提案（Proposal）可审可批——**知识写入有刹车**。

### 🔗 双形态集成

> 读有 Hook，写有 MCP——各走各的快路径。

- **Shell Hook**（上下文注入）：stdin 收 JSON，按 `SHELL_HOOK_TIMEOUT` 预算返回上下文；出厂默认 2 秒，安装器生成的命令默认给冷启动 8 秒。超短查询（默认 ≤3 字符）不检索，但已配置常驻画像时仍可返回画像帧。详见 [宿主协议](docs/host-hook-protocol.md)。
- **MCP server**（写，标准协议）：入口 `lantai-mcp`（`python -m lantai.cli.mcp`），标准 JSON-RPC 2.0，输入校验 + 异常隔离。默认档只暴露核心 6 工具（`search` / `add` / `feedback` / `offload_read` / `mem_recent` / `core_memory_get`，fail-closed）；`LANTAI_MCP_PROFILE=full` 显式放开全量 96 项
- **远程 MCP bridge**：入口 `lantai-mcp-bridge`，通过 HTTPS 将 stdio 工具调用转发至远程 Lantai；无本地 DB、`.env` 或代理发现，配置与安全约束见 [说明](docs/mcp-bridge.md)。
- **DSH 召回研究**：独立 `lantai-recall` 包已暂停启用，在副作用前拒绝自动召回；现有 MCP 逐调用归因适配器保持独立。原因与重新验收条件见 [bundle 文档](integrations/dsh-lantai-recall/README.md)。

Claude Code、Codex、DSH、Hermes、OpenClaw 与 Antigravity 的实际入口、配置和权限边界见 [MCP 客户端矩阵](docs/mcp-client-matrix.md)。标准 MCP 接通、自动注入、对话采集与内嵌面板是分别验收的能力；Antigravity 目前使用通用 MCP stdio 配置，不宣称专用安装器或自动采集。

### 悬镜与 MCP Apps

Web 控制台入口为 `http://127.0.0.1:8767/ui`，记忆拓扑与时序溯源入口为 `/ui#/xuanjing`。支持 MCP Apps 的宿主还可在沙箱中展示核心记忆和悬镜面板；后续查询依赖宿主提供 `serverTools` 能力，普通 MCP 客户端不因此自动获得内嵌界面。悬镜工具 `get_xuanjing_graph` 属于 full 档，显示面板不会扩大默认六工具权限。接入与验收边界见 [客户端矩阵](docs/mcp-client-matrix.md)。

同袍私有工作区与公共典籍的密钥绑定、接口及隔离范围见 [使用说明](docs/tongpao-workspace.md)。

### 🛡️ 护盾（安全基线）

> 神盾护住的不是代码，是代码背后的人。

- **默认回环绑定** `127.0.0.1`；非回环地址必须配置 `API_KEY`（`hmac` 恒时比较），否则拒绝启动
- **SSRF 防护**：外部抓取协议白名单 + DNS 解析后逐 IP 阻断私网/回环/link-local + 重定向逐跳复验 + 响应限长
- **备份/恢复原子化**：SQLite online backup 一致性快照 + manifest sha256 校验 + 路径限定 + 原子换入 + fail-closed 停服保护
- **端点白名单**：LLM/精排 base_url 域名 allowlist，独立最小权限 `RERANKER_API_KEY`

### 📡 司天（后台运行监控面板）

> 观天象、察灾异、报异常——系统自己会报告状态。

浏览器打开 `http://127.0.0.1:8767/ui` → 侧边栏「司天监控」，或直接调 REST：

```bash
# 全量快照（进程/存储/记忆/管道/调度/请求/安全 + 规则告警）
curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8767/monitor/overview | jq '.summary, .alerts'

# 分钟级趋势 / 端点耗时排行 / 错误与慢请求
curl -s -H "X-API-Key: $API_KEY" "http://127.0.0.1:8767/monitor/series?minutes=60" | jq '.series[-5:]'
curl -s -H "X-API-Key: $API_KEY" "http://127.0.0.1:8767/monitor/logs?only_problems=true" | jq '.items[:5]'

# 外部监控直接抓取（Prometheus 文本格式）
curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8767/monitor/prometheus | head -20

# worker 停摆时手动补跑一次（与案牍共用互斥锁）
curl -s -X POST -H "X-API-Key: $API_KEY" http://127.0.0.1:8767/monitor/workers/forgetting/run
```

面板一屏给出：运行时长 / 请求速率 / p95 延迟 / 5xx 错误率 / 记忆总量 / 待审积压 /
worker 逾期数 / 存储占用 / 零召回率；下方是规则告警、服务与依赖、请求趋势曲线（纯 SVG）、
端点耗时排行、记忆管道水位、调度器与 worker 明细、最近问题请求与生效配置（密钥打码）。

**告警规则**（阈值全部可配，见 `MONITOR_ALERT_*`）：调度器停摆、worker 逾期（超 2 个周期升
critical）、反思/参数建议任务失败、5xx 错误率、p95 延迟、零召回率、待审候选积压、SQLite
体积、非回环绑定无鉴权、缺 LLM Key。

**写入代价**：请求级指标只进内存环形缓冲；落 `operation_logs` 的只有 4xx/5xx、超过
`MONITOR_PERSIST_SLOW_MS` 的慢请求，以及 1/`MONITOR_PERSIST_SAMPLE` 的正常请求采样——
不给记忆主链路加写放大。`MONITOR_ENABLED=false` 整体关闭。

## 技术栈

- **运行时**：Python 3.11+、FastAPI、Uvicorn、APScheduler
- **结构化数据**：SQLModel + SQLite（+ FTS5 trigram 全文索引）
- **向量存储**：ChromaDB（cosine，内嵌零外部依赖）
- **检索**：jieba + rank-bm25，Reranker 可配置（兼容 OpenAI Rerank API）
- **大模型**：兼容任何 OpenAI 格式的 API（默认 bge-m3 embedding）

## 环境变量

所有配置通过环境变量 / `.env` 注入。多数有默认值，但非回环部署必须配置 `API_KEY`，使用外部模型也必须提供对应端点和凭据；默认值不代表所有能力开箱可用。完整清单见 [配置实现](lantai/core/settings.py)。

进程环境变量覆盖 `.env` 的同名配置；`.env` 固定读取 `lantai/core/settings.py` 对应的源码根，不随当前工作目录或 `LANTAI_HOME` 移动。下表为出厂默认值，`.env.example` 的硅基流动端点与模型属于显式示例覆盖。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST` / `PORT` | `127.0.0.1` / `8767` | 监听地址；非回环必须配 API_KEY |
| `API_KEY` | 空 | 环境变量密钥（请求头 `X-API-Key`）；非回环必填，命中后 principal=api_key/admin；与库内 Bearer 并存 |
| `OPENAI_API_KEY` | 空 | LLM 提取 + Embedding |
| `OPENAI_BASE_URL` / `LLM_MODEL` | `https://api.openai.com/v1` / `gpt-4o-mini` | OpenAI 兼容端点 |
| `EMBED_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `RERANKER_ENABLED` | `true` | 精排开关（失败自动降级） |
| `RERANKER_BASE_URL` | `https://api.siliconflow.cn/v1` | 精排端点，与 LLM 端点独立配置 |
| `RERANKER_API_KEY` | 空 | 独立精排密钥；空值仅在与 `OPENAI_BASE_URL` 同 host 时复用 `OPENAI_API_KEY`，跨服务商未配密钥则跳过精排 |
| `COALESCE_ENABLED` | `false` | 潮波并忆开关 |
| `DEDUP_MERGE_THRESHOLD` / `DEDUP_UPDATE_THRESHOLD` | `0.90` / `0.65` | fastpath 三态阈值；提取路径预筛 `DEDUP_PRESCREEN_MERGE`=0.95 + 结构判别（ADR-0019） |
| `GATE_CACHE_TTL` | `15.0` | 闸门热缓存秒数 |
| `LANTAI_HOME` | 自动探测后回退源码根 | 数据目录（DB/向量库/备份）；优先显式配置，其次旧名 `REMEMBRANCE_HOME`，再探测已有用户库 |
| `DATABASE_URL` / `CHROMADB_PATH` | 从数据目录推导 | 未显式设置时分别为数据目录下 `remembrance.db` / `.chromadb`；显式路径保持原值 |
| `ALLOWED_API_HOSTS` | `["api.openai.com", "api.siliconflow.cn"]` | 外部 API 域名白名单；精排另受客户端固定的这两个 host 限制 |
| `MONITOR_ENABLED` | `true` | 司天监控与请求遥测开关（关闭后中间件零开销直通） |
| `MONITOR_PERSIST_SLOW_MS` / `MONITOR_PERSIST_SAMPLE` | `800` / `20` | 慢请求阈值（必落库）与正常请求落库采样率 1/N |
| `MONITOR_RETENTION_DAYS` | `7` | `operation_logs` 保留天数 |
| `MONITOR_ALERT_*` | 见 settings | 告警阈值：错误率 / p95 / 零召回率 / 待审积压 / 库体积 |
| `CONSOLIDATION_AUDIT_MODE` | `off` | 沉潜巩固产物过审模式（ADR-0050）：`off` 现行直写 / `shadow` 影子对照 / `enforce` 一律提案待人工裁决；非法值 fail-loud 停摆 |
| `REFLECT_AUTO_APPLY` | `false` | 反思产物自动应用开关（ADR-0050 决策 7a）：默认关＝反思产物一律进 pending 待审 |

已有用户库的探测位置为 Windows 用户主目录下 `AppData/Local/remembrance-data`，Linux/macOS 为 `~/.remembrance-data`；只有目录及其中的 `remembrance.db` 均存在才自动沿用，否则回退源码根。需要隔离开发、测试或多个实例时，应显式设置 `LANTAI_HOME`，不要依赖自动探测。

## 测试

先按 [贡献指南](CONTRIBUTING.md) 与 [开发测试环境](docs/project/development-environment.md) 准备冻结依赖、真实 Chromium、Node.js 24.9+ 和隔离临时目录，再使用该环境的解释器运行：

```bash
python -m ruff check lantai/ tests/ scripts/
python -m ruff format --check lantai/ tests/ scripts/
python -m pytest tests/ -q --cov=lantai --cov-report=term-missing
```

发布验收还包含宿主真实库连接绊线和隔离运行的遗忘质量检查，完整口径见 [发布流程](docs/release-process.md)。测试徽章显示最近一次 CI 状态，不能替代当前改动的实跑记录。

## 已发布里程碑

- **v0.70.0「悬镜」**：拓扑与时序溯源、沙箱 MCP 应用面板、多宿主接入、同袍隔离工作区，以及核心并发、协议和配置边界收口。
- **v0.69.0 / v0.69.1**：逐调用会话归因、摄取阶段监控、远程 MCP 桥与浏览器交互修复。
- **v0.68.2**：依赖与嵌入式存储运行时升级；旧 Chroma 数据须按迁移规程另验。

完整版本历史与升级说明见 [CHANGELOG](CHANGELOG.md)。[历史路线计划](docs/plans/) 保留决策背景，不作为当前待办清单。

## 文档索引

- [CONTEXT](CONTEXT.md) — 领域词汇表（lane / gate / coalesce / fastpath / checkpoint…）
- [工程术语对照表（大白话版）](docs/glossary.md) — 契约 / 冒烟 / 门禁 / 回归，以及怎么看一份 Agent 汇报
- [贡献指南](CONTRIBUTING.md) · [开发入口](docs/project/DEVELOPMENT.md) · [标准开发流程](docs/development-workflow.md)
- [MCP 客户端矩阵](docs/mcp-client-matrix.md) · [宿主协议](docs/host-hook-protocol.md) · [远程 MCP 桥](docs/mcp-bridge.md)
- [DSH 核心记忆与对话采集](integrations/dsh-core-memory/README.md) · [同袍工作区](docs/tongpao-workspace.md)
- [Chroma 迁移规程](docs/chroma-migration.md) · [运维手册](docs/ops-runbook.md)
- [架构决策](docs/adr/) — 持续增补；[历史计划](docs/plans/) — 各版本执行方案
- [版本上传流程](docs/release-process.md) — 发布门禁与人工闸门
- [aiduMEM 移植记录](docs/aidumem-port-results.md) · [Agent 协作约定](AGENTS.md)

## 许可证

[MIT License](LICENSE)，版权归属见许可证原文。改编自 [aiduMEM](https://github.com/monkey2jack/aiduMEM)（MIT License）。

---

<p align="center">
  <sub>改编自 aiduMEM · 由 <a href="https://github.com/JIUSHIQINGSHAN">JIUSHIQINGSHAN</a> 构建</sub>
</p>
