# 🧠 Remembrance-System — AI Agent 长期记忆引擎

> **记忆不是记事，而是不忘过往的点点滴滴。**
>
> **不只是存储 — 是检索、演化、遗忘的完整闭环。**

```
摄取 → 闸门 → 演化 → 遗忘 → 检索
让 AI 在对的时间，找到对的回忆。
```

[![Version](https://img.shields.io/badge/version-0.3.7-blue.svg)](https://github.com/JIUSHIQINGSHAN/Remembrance-System)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-yellow.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-120%2F120-green.svg)](docs/aidumem-port-results.md)
[![改编自](https://img.shields.io/badge/based%20on-aiduMEM-orange.svg)](https://github.com/monkey2jack/aiduMEM)

---

## Remembrance-System 是什么？

一个为 **AI Agent 提供持久化记忆** 的长期记忆管理系统。不是简单的键值存储，而是让 AI **会记忆、会演化、会遗忘**。

本项目**改编自 [aiduMEM（优忆思）](https://github.com/monkey2jack/aiduMEM)**——移植了相关性闸门、潮波并忆、Ebbinghaus 遗忘、Chronos 时间感知等设计思想（移植过程与决策见 [移植结果文档](docs/aidumem-port-results.md)），并在此基础上重新实现了存储层（SQLite + FTS5 + ChromaDB）、补全了 Shell Hook / MCP 双形态集成与完整安全加固。

| 层级 | 做什么 | 核心特性 |
|------|--------|----------|
| 🧠 **记忆** | 在对的时间找到对的回忆 | **四路混合检索**：向量语义 + jieba BM25 + FTS5 trigram 子串容错 + 时效衰减 |
| 🔍 **闸门** | 只检索真正相关的内容 | 启发式相关性闸门拦截无关上下文：纠错/社交/热缓存/自我指代/明确回忆/延续/实质内容 |
| 🌊 **潮浪** | 批量 LLM 提取，不逐条调用 | Tidal Coalescing：短消息按 `user_id + lane` 缓冲合并，一次 LLM 调用处理多条 |
| ⚡ **直写** | 高频句型不走 LLM | Fastpath 白名单：自我声明/偏好表达/显式指令三类句型直接写入，宁 miss 不脏写 |
| 📊 **演化** | 知识生长与自我纠错 | Proposal → Apply 全流程，Checkpoint 快照可一键回滚 |
| ⏳ **遗忘** | 遗忘是特性，不是 bug | Ebbinghaus 指数衰减，低分自动归档；归档不参与检索、物理不删 |
| 🕰️ **克罗诺斯** | 时间感知的有效期 | 双时间轴（`valid_from` / `valid_to`），过期事实降权，未生效事实过滤 |
| 🧹 **去重** | 不写垃圾比事后清理便宜 | 余弦三态判定：`merge / update / insert`（0.80 / 0.65 可配） |
| 🔗 **双形态** | 读有 Hook，写有 MCP | Shell Hook（零依赖 CLI 注入，2s 硬超时）+ MCP server（标准 JSON-RPC 2.0） |
| 🛡️ **护盾** | 安全不是可选项 | 默认回环绑定、非回环强制鉴权、SSRF 防护、原子备份恢复、端点白名单 |

---

## 架构

```
┌──────────────────────────────────────────────────┐
│        🧠 Remembrance-System — 记忆引擎           │
│        FastAPI REST API :8767（默认 127.0.0.1）   │
├──────────────────────────────────────────────────┤
│  api/       → 薄路由（逻辑下沉 service 层）        │
│  services/  → 业务逻辑层（门面铁律 ADR-0001）      │
│  gate/      → 启发式预过滤 + 三态去重 + 决策       │
│  ingestion/ → RSS/arxiv 适配器 + 潮波缓冲 + SSRF  │
│  evolution/ → 提案/应用/回滚 + Checkpoint 快照     │
│  retrieval/ → 四路融合检索 + 意图分类 + Reranker   │
│  memory/    → Ebbinghaus 遗忘 + 自动归档          │
├──────────────────────────────────────────────────┤
│  SQLite（结构化 + FTS5 trigram 全文索引）          │
│  ChromaDB（向量存储，cosine）                      │
│  jieba BM25（词级关键词）                         │
└──────────────────────────────────────────────────┘
```

## 快速开始

### 方式一：源码克隆运行

```bash
# 1. 克隆
git clone https://github.com/JIUSHIQINGSHAN/Remembrance-System.git
cd Remembrance-System

# 2. 虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. 安装
pip install -e .

# 4. 配置
cp .env.example .env
# 填入 OPENAI_API_KEY（必填）；API_KEY 为非回环部署必填

# 5. 初始化并启动
python scripts/init_db.py
python api_server.py
# API 运行在 http://127.0.0.1:8767
```

### 方式二：Docker 容器运行

```bash
docker build -t remembrance:0.3.6 .
docker run -d -p 8767:8767 \
  -e API_KEY=your-admin-key \
  -e OPENAI_API_KEY=sk-xxx \
  -v /your/data:/data \
  remembrance:0.3.6
```

> 容器默认 `HOST=0.0.0.0` 对外暴露，**必须注入 `API_KEY`**——启动守卫（`assert_secure_binding`）会在非回环地址且无密钥时拒绝运行。

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

### 示例：搜索（带诊断）

```bash
curl -s -X POST "http://127.0.0.1:8767/search?trace=true" \
  -H "Content-Type: application/json" \
  -d '{"query": "上次我们聊的检索方案是什么？", "top_k": 5}'
```

### 示例：添加记忆

```bash
curl -s -X POST http://127.0.0.1:8767/add \
  -H "Content-Type: application/json" \
  -d '{"title": "项目截止", "content": "项目截止日期是3月15号", "lane": "fact"}'
```

## Remembrance-System 的独特之处

### 🔍 相关性闸门（Relevance Gate · 移植自 aiduMEM）

普通 RAG 系统对每条消息都去搜索记忆。本系统的**相关性闸门**用启发式规则判断当前消息是否真的需要记忆检索：纯社交结束语、寒暄直接跳过；纠错、明确回忆、上下文延续立即命中——**无关查询零检索成本**，15 秒热缓存让追问零开销。

### 🌊 潮波并忆（Tidal Coalescing · 移植自 aiduMEM）

短消息不逐条调用 LLM。按 `user_id + lane` 分键缓冲，按 lane 档位（空闲超时/时间窗/条数/字符数）触发冲刷，一次 LLM 调用处理多条消息。

### 🧠 四路混合检索（FTS5 trigram 是亮点）

```
score = 0.6·向量语义 + 0.25·jieba BM25 + 0.05·FTS5 子串命中 + 0.1·时效衰减
```

**FTS5 trigram 子串匹配**对中文错别字、插入删除有天然容错（"向良"能撞上"向量"）——这是 jieba 分词 BM25 给不了的；两者互补，FTS 命中但向量漏掉的记忆会被**追加召回**。决策见 [ADR-0008](docs/adr/0008-fts5-parallel-recall.md)。

### ⏳ 遗忘曲线衰减（Ebbinghaus Decay · 移植自 aiduMEM）

记忆有保质期。按 lane 分轨的指数衰减：`fact` 半衰期 30 天、`chat` 仅 3 天、`preference` 15 天。decay 低于阈值自动转 archived——**归档不参与检索、物理不删**，可回滚。

### 🕰️ 克罗诺斯双时间轴（Chronos · 移植自 aiduMEM）

`valid_from` / `valid_to` 时间窗口：未生效记忆直接过滤，过期记忆降权保留。设了时间窗的记忆自动受控。

### 📸 Checkpoint 回滚

每次记忆变更生成 `before/after` 快照。改错了？`/memory/{id}/rollback` 一键回到上一版本。提案（Proposal）可审可批——**知识写入有刹车**。

### 🔗 双形态集成

> 读有 Hook，写有 MCP——各走各的快路径。

- **Shell Hook**（读，零依赖）：stdin 收 JSON，2s 硬超时返回 Markdown 上下文，≤3 字符不注入
- **MCP server**（写，标准协议）：`search` / `add` / `feedback` 三工具，标准 JSON-RPC 2.0，输入校验 + 异常隔离

### 🛡️ 护盾（安全基线）

> 神盾护住的不是代码，是代码背后的人。

- **默认回环绑定** `127.0.0.1`；非回环地址必须配置 `API_KEY`（`hmac` 恒时比较），否则拒绝启动
- **SSRF 防护**：外部抓取协议白名单 + DNS 解析后逐 IP 阻断私网/回环/link-local + 重定向逐跳复验 + 响应限长
- **备份/恢复原子化**：SQLite online backup 一致性快照 + manifest sha256 校验 + 路径限定 + 原子换入 + fail-closed 停服保护
- **端点白名单**：LLM/精排 base_url 域名 allowlist，独立最小权限 `RERANKER_API_KEY`

## 技术栈

- **运行时**：Python 3.11+、FastAPI、Uvicorn、APScheduler
- **结构化数据**：SQLModel + SQLite（+ FTS5 trigram 全文索引）
- **向量存储**：ChromaDB（cosine，内嵌零外部依赖）
- **检索**：jieba + rank-bm25，Reranker 可配置（兼容 OpenAI Rerank API）
- **大模型**：兼容任何 OpenAI 格式的 API（默认 bge-m3 embedding）

## 环境变量

所有配置通过环境变量 / `.env` 注入，**全部可选**——不设置就走安全默认值。完整清单见 `remembrance/core/settings.py`。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST` / `PORT` | `127.0.0.1` / `8767` | 监听地址；非回环必须配 API_KEY |
| `API_KEY` | 空 | REST 鉴权（空 = 本机无鉴权模式） |
| `OPENAI_API_KEY` | 空 | LLM 提取 + Embedding |
| `OPENAI_BASE_URL` / `LLM_MODEL` | openai / gpt-4o-mini | OpenAI 兼容端点 |
| `EMBED_MODEL` | `BAAI/bge-m3` | Embedding 模型 |
| `RERANKER_ENABLED` | `true` | 精排开关（失败自动降级） |
| `COALESCE_ENABLED` | `false` | 潮波并忆开关 |
| `DEDUP_MERGE/UPDATE_THRESHOLD` | `0.80` / `0.65` | 三态去重阈值 |
| `GATE_CACHE_TTL` | `15.0` | 闸门热缓存秒数 |
| `REMEMBRANCE_HOME` | 仓库根 | 数据目录（DB/向量库/备份） |
| `ALLOWED_API_HOSTS` | openai/siliconflow | 外部 API 域名白名单 |

## 测试

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
# 120 passed —— 全绿（含 FTS/SSRF/备份恢复/MCP 协议/Shell Hook 超时）
```

## 路线图

- [x] v0.3.1 审计修复（P0 仓库卫生 / 绑定鉴权 / 测试基线）
- [x] v0.3.2 FTS5 schema + Chronos 时区 + BM25 兼容修复
- [x] v0.3.3 P1 安全收口（SSRF / 备份恢复 / MCP 校验）
- [x] v0.3.4 FTS5 并列接入 + BM25 缓存（ADR-0008）
- [x] v0.3.5 测试全绿 120/120 · v0.3.6 供应链加固（Actions 锁 SHA / 非 root）
- [ ] salience 冲突降权与 contradiction gate 整合（Fog）
- [ ] autodream 7 天周期记忆蒸馏（Fog）
- [ ] checkpoint 五段会话快照（Fog）
- [ ] 去重阈值实测校准（bge-m3 中文样本）

## 文档索引

- `CONTEXT.md` — 领域词汇表（lane / gate / coalesce / fastpath / checkpoint…）
- `docs/adr/` — 架构决策记录 0001-0008
- `docs/plans/` — 各版本执行方案
- `docs/aidumem-port-results.md` — aiduMEM 移植结果与审计修复记录
- `AGENTS.md` — Agent 协作约定（issue tracker / 测试纪律）

## 许可证

MIT（改编自 [aiduMEM](https://github.com/monkey2jack/aiduMEM)，MIT License）。

---

<p align="center">
  <sub>改编自 aiduMEM · 由 <a href="https://github.com/JIUSHIQINGSHAN">JIUSHIQINGSHAN</a> 构建</sub>
</p>
