# 兰台记忆（Lantai）v0.21 白皮书

> **AI Agent 长期记忆引擎 — 摄取、闸门、演化、检索、遗忘的完整闭环**
>
> 2026-09-17 · 基于 [aiduMEM（优忆思）](https://github.com/monkey2jack/aiduMEM) 改编

---

## 一、项目概述

**兰台（Lantai）** 取名自汉代皇家档案馆「兰台」，是一个面向 AI Agent 的持久化长期记忆管理引擎。它解决的核心问题是：让 AI **在对的时间，找到对的回忆**——不是简单的键值存储，而是一个会记忆、会演化、会遗忘的认知系统。

### 上游关系

Lantai 改编自 **aiduMEM（优忆思，现改名 aiduMEI）**，移植了四项核心认知设计：

| 移植能力 | 说明 |
|----------|------|
| 相关性闸门（Relevance Gate） | 启发式拦截无关查询，避免 LLM 资源浪费 |
| 潮波合并（Tidal Coalescing） | 短消息按 `user_id + lane` 缓冲批处理 |
| 艾宾浩斯遗忘（Ebbinghaus Forgetting） | 指数衰减 + 自动归档，遗忘是特性不是 bug |
| Chronos 时间感知 | 双时间轴（`valid_from` / `valid_to`），过期事实降权 |

在此基础上，Lantai **重新实现了存储层**（SQLite + FTS5 + ChromaDB 替代 Qdrant），补全了 Shell Hook / MCP 双形态集成与完整安全加固，并走出了一条独立的演化路线。

### 与上游的关键差异

| 维度 | aiduMEI v21.2 | Lantai v0.21 |
|------|---------------|--------------|
| 存储引擎 | SQLite + Qdrant（可选 mem0） | SQLite + ChromaDB（嵌入式，零外部依赖） |
| 向量服务 | 双引擎自动挡（Cloud/Local/Auto 三模式） | 单模式外部 Embedding API + 本地 FTS 降级 |
| 前端控制台 | 零构建 Web UI（`/ui`） | 「悬镜」零构建 Web 工作台（吉金/漏窗双主题） |
| 认知层 | 认知治理（出身标签、知识溯源） | 认知闭环（反思→信念→规则晋升状态机） |
| 测试规模 | 2137 例 | 849 例 |
| 部署方式 | Docker + 一行 Prompt 部署 | Docker + 源码 + pip 包 |
| 命名体系 | 内部英文命名为主 | 中文典籍意象命名体系（ADR-0013） |
| MCP 工具数 | 41 | 55 |
| 联邦协作 | 多智能体 MoE 共享记忆 | ACL lane 级隔离（不做联邦） |

---

## 二、从 v0.1 到 v0.21：完整演化历程

### 2.1 第一阶段：奠基与安全加固（v0.1 → v0.3.7）

**v0.1.0（2026-06-20）** — 初始发布，从 aiduMEM 移植核心能力：

- 四路混合检索引擎：向量语义 + jieba BM25 + FTS5 trigram 子串 + 时效衰减
- 相关性闸门、潮波合并、Fastpath 白名单直写、Ebbinghaus 遗忘
- Shell Hook + MCP 双模式集成
- 安全基线：回环绑定、SSRF 防护、原子备份恢复、端点白名单

**v0.3.1 → v0.3.7** — 安全加固与稳定性收口：

- P0 审计修复：仓库清洁、绑定鉴权强制、测试基线建立
- SSRF 加固：外部拉取协议白名单 + DNS 解析 IP 拦截
- 原子备份恢复：在线备份 + manifest SHA256 校验
- MCP 输入校验 + 异常隔离
- FTS5 trigram 并行召回 + BM25 缓存（ADR-0008）
- **原文直存（Raw Drawer）**：零 LLM 直写通道，sha256 幂等去重（ADR-0009）
- **冲突消解确定性层**：规则集优先 + LLM 回落双通道，ConflictEvent 账本可裁决（ADR-0010）
- MCP 工具扩容第一批：8 → 12 工具
- 供应链加固：GitHub Actions 锁定 commit SHA
- 关键 bug 修复：FTS5 短词毒化 AND 链、`apply_proposal` 数据丢失、SQLite 自死锁

### 2.2 第二阶段：命名治理与认知架构（v0.6 → v0.14）

**内部包名统一为 `lantai`**，确立中文典籍意象命名体系（ADR-0013），每个新模块/机制必须先登记词汇表再使用。

#### 存储与可观测性

| 版本 | 能力 | 说明 |
|------|------|------|
| v0.6 | Schema 版本化迁移 | `PRAGMA user_version` 增量迁移链（v1→v20） |
| v0.6 | 遗忘质量离线门禁 | 六维指标 + 中文评测集 v1（13 case）+ CI 自动门禁 |
| v0.6 | supersedes 边感知排序 | 被取代旧值降权，新值同在时压到其下 |
| v0.6 | 检索透明（Search Transparency） | evidence 来源说明 + search_trace 诊断 |
| v0.6 | 零召回率监控 + token 成本估算 | RetrievalEvent 窗口聚合 + 按 lane/intent 分组 |

#### 认知与知识图谱

| 版本 | 能力 | 中文名 | 说明 |
|------|------|--------|------|
| v0.7 | 记忆分类树 | — | 父子节点 + 路径唯一 + depth 前缀查询 |
| v0.7 | 技能结晶 | crystal | 高频共现聚类 → SkillCrystal 候选 → 人工裁决 |
| v0.8 | autodream 蒸馏 | — | 后台周期合成 → 待审提案（宁 miss 不脏写） |
| v0.9 | 记忆星图 | graph | 零依赖内联 SVG 放射布局 + scene 聚簇 |
| v0.10 | 目识（Vision 多模态） | 目识 | 截屏/图片 → vision_caption → 记忆入库 |
| v0.11 | 烽燧（recall_chain） | 烽燧 | 种子 BFS 传播多跳联想检索（max_depth=3） |

#### 记忆语义与治理

| 版本 | 能力 | 说明 |
|------|------|------|
| v0.6 | 场景聚合层（scene） | embedding 聚类 + LLM 命名/摘要 + 增量聚类 |
| v0.6 | 对话写入通道（Dialogue Ingest） | fastpath/闲聊/LLM 提取三路分流 |
| v0.6 | 候选可见队列（Candidate Review） | REJECT 不静默丢弃，TTL 自动归档 |
| v0.6 | provenance 提取来源 | 哪套 prompt / 哪个模型 / 何时产出 |
| v0.6 | Skill 资产化 | actions → structure.steps → procedural 永不衰减 |
| v0.6 | Shell Hook 召回预算 | 单条/总量字符上限 + 记忆使用指南注入 |
| v0.6 | lane 级 ACL | 按 agent_id 绑定 lane 集，跨域拒绝 |
| v0.6 | 冷启动导入 | JSONL 批量原文直存 + 对话链模拟摄取 |
| v0.6 | 上下文卸载（offload） | 超长记忆落盘，会话只注入摘要+路径 |
| v0.6 | 记忆 Wiki | 场景/技能 → docs/ 页面 + wikilink + overview |
| v0.6 | Obsidian 双链 | `[[页面]]` 解析 → 实体 + MemoryEdge |

#### 外部借鉴吸收

Lantai 在 v0.6 之后系统性地研究并选择性吸收了多个开源记忆项目的设计思想：

| 来源项目 | 吸收能力 | 兰台实现 |
|----------|----------|----------|
| aiduMEI | MAP 记忆星图、控制台、广播链、视觉记忆 | 窄版移植，去除违反宁 miss 的自动合并/删除 |
| TencentDB Agent Memory | 场景聚合、冷启动导入、上下文卸载、mem: 命令、ACL | 窄版移植，Fixed Binding + 二级路径 |
| Mem0 | 辨域三维隔离（user/session/agent domain） | 数据库字段扩展 + 检索层隔离 |
| Letta / MemGPT | 札记（Working Memory Scratchpad） | 1000 字符上限暂存夹 + 首轮注入 |
| Zep | 潜移（异步摄取管道） | 后台线程池 + TaskRegistry 非阻塞调度 |
| Cognee | 贯珠（图谱二度联想） | BFS 1~2 hop 关系扩散 + 路径可解释 |

**v0.14.0（缥缃）** — 版本上传规范流程（`release_check.py` 门禁）、双主题换肤（吉金 + 漏窗）。

### 2.3 第三阶段：记忆精炼与认知闭环（v0.15 → v0.16）

**v0.15.0 → v0.15.2（绳墨）** — 校准、门禁与收口：

- **底本（会话快照）**：五段块（在做/下一步/工作区/决策/待办），会话启动自动注入，>30 天标注陈旧
- **三态去重升级（校雠 ADR-0019）**：余弦预筛 + 结构判别两相位（36 对回归样本）
- **单字否定对候选探测（参商 ADR-0024）**：token 级子串交叉 → LLM 矛盾裁决
- **校雠扩展信号（ADR-0023）**：锚点比非对称修复 + 实质新词判定
- **反思校准口径修复**：curator 零产出根因 + 观察期校准 + 竞态修复
- **案牍控制台 Phase 1**：七类工作项只读投影 + 批量处置 + 确定性分区排序
- **候选两阶段审批**：approve 只创建 pending 提案，不再立即应用
- **中文评测集 v2 → v3**：13 → 50 → 80 case，五类覆盖
- **开发工作流标准化**：六阶段研发规范（ADR-0013 起）

**v0.16.0（更漏）** — 四维借鉴闭环 + 认知治理精炼：

- **器识（Persona 人格基座）**：L/G/E 三层认知模型（言语风格/行为准则/认知底色）
- **披沙（候选精炼）**：模糊置信度候选 LLM 二次指代消解与口语提纯
- **沙汰（入队信噪分离）**：`CANDIDATE_MIN_CONFIDENCE=0.15` 地板过滤
- **考功（价值演化）**：长程使用反馈 + 采纳率驱动记忆升降
- **拾遗（检索韧性降级）**：Embedding 异常平滑降级本地 FTS + BM25 + LIKE 子串
- **察窗（观察期滑动窗口）**：反思阈值校准的数据收集窗口

### 2.4 第四阶段：协同记忆与认知自治（v0.18 → v0.21）

**v0.18.0** — 四维借鉴闭环一次性落地：

| 能力 | 中文名 | 借鉴来源 | 核心设计 |
|------|--------|----------|----------|
| 图谱二度联想 | 贯珠 | Cognee | BFS 1~2 hop + 路径可解释 + 环路过滤 |
| 三维硬隔离 | 辨域 | Mem0 | user/session/agent domain + 跨域召回 |
| 异步摄取管道 | 潜移 | Zep | 线程池 + TaskRegistry + <10ms 提交 |
| 工作区暂存 | 札记 | Letta/MemGPT | 1000 字符便签 + 首轮协同注入 |

**v0.19.0 — 沉潜（闲时夜梦沉淀）**：

- 高重合碎片聚类（≥3 条/domain+lane 分组）→ LLM 归纳提纯 → 折叠归档
- 衰减突触修剪：decay_score < 0.05 且无高采纳反馈 → archived
- 每日凌晨 03:30 自动执行（北京时间）

**v0.20.0 — 探颐（记忆主动探针与消歧）**：

- 检索命中冲突记忆时生成自然语言求证探针
- 用户次轮答复自动识别（肯定/否定/纠正）→ 冲突闭环消解
- MCP 工具 55 个

**v0.21.0 — 悬镜（全功能可视化管理控制台）**：

- **Lantai Studio** 单页工作台：器识与札记在线工作室、沉潜仪表盘、四路检索演练场（Playground）
- 吉金（青铜深色）与漏窗（园林浅色）双典籍主题 + 移动端响应式

### 2.5 未发布：最新修复（Unreleased）

当前 master 分支包含两项 P0 级修复：

1. **鉴权双轨断裂（P0）**：`X-API-Key` 与环境变量 `API_KEY` 从未生效。空库 + 非回环可零鉴权写记忆。已统一鉴权链路为 `X-API-Key` → 库内 Bearer → DEV MODE（仅回环+无 API_KEY+空库）。
2. **启动入口缺失（P0）**：`lantai-server` 命令此前无 `main()` 函数；Docker CMD 引用不存在的 `api_server.py`。已修复。

#### 司天（运行监控面板 ADR-0044）

全新后台运行监控体系：

- **采集层**：进程内 MetricsCollector（环形缓冲 + 分钟级聚合桶），零第三方依赖采集 uptime/RSS/线程/CPU/fd
- **遥测层**：ASGI TelemetryMiddleware + 采样落库（4xx/5xx 必留，正常 1/N 采样，批量写入防写放大）
- **聚合层**：八域事实装配（进程/存储/记忆/管道/调度/请求/安全/依赖）+ 13 条规则告警 + Prometheus 文本出口
- **前端**：悬镜工作台「司天监控」视图（指标卡/告警/趋势/端点排行/水位/worker 补跑/运行配置），10 秒自动刷新

其他未发布修复：控制台 DOM 选择器契约测试、调度器 ConflictingIdError 修复（`replace_existing=True`）、退出路径幂等化、`jieba`/`rank-bm25` 依赖补声明。

---

## 三、架构总览

### 3.1 系统分层

```
┌─────────────────────────────────────────────────────────┐
│                  接入层 (Integration)                      │
│  Shell Hook (CLI 注入)  ·  MCP Server (JSON-RPC 2.0)    │
│  REST API (FastAPI :8767)  ·  Hermes 插件 (lantai-hook)  │
├─────────────────────────────────────────────────────────┤
│                    路由层 (API)                            │
│  27 核心路由 + 扩展路由（obsidian/wiki/terminal/…）       │
│  ACL 依赖注入  ·  鉴权中间件  ·  遥测中间件              │
├─────────────────────────────────────────────────────────┤
│                  服务层 (Services)                        │
│  memory · candidate · conflict · persona · checkpoint    │
│  evolution · consolidation · crystal · probing · wiki …  │
├─────────────────────────────────────────────────────────┤
│                  核心引擎 (Core Engines)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ 检索引擎  │  │ 闸门引擎  │  │ 遗忘引擎  │  │ 认知引擎│ │
│  │ hybrid    │  │ gate     │  │ forgetting│  │cognition│ │
│  │ 4路融合   │  │ 3态去重   │  │ Ebbinghaus│  │反思→规则│ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
├─────────────────────────────────────────────────────────┤
│                  存储层 (Storage)                         │
│  SQLite (WAL) + FTS5 trigram  ·  ChromaDB (cosine HNSW)  │
│  Schema v20 增量迁移  ·  APScheduler 持久化 jobstore     │
└─────────────────────────────────────────────────────────┘
```

### 3.2 数据流

```
原始输入 → 提取(LLM/fastpath) → 候选(candidate)
    → 闸门(novelty/dedup/conflict) → 提案(proposal)
    → 晋升(promoter) → 活跃记忆(MemoryItem)

查询 → 意图分类 → 四路混合检索(vector+BM25+FTS+decay)
    → supersedes 排序 → 重排(reranker) → 结果 + evidence
```

### 3.3 后台工作流

| Worker | 周期 | 职能 |
|--------|------|------|
| forgetting | 每日 | 衰减计算 + 低分归档 |
| digest | 每日 | 知识简报 + 候选 TTL 清理 |
| reflect | 每日 | 模式检测 → 信念 → 规则晋升 |
| evolve | 每日 | 消化期提案处理 + 场景增量聚类 |
| autodream | 每 7 天 | 同 lane 聚类蒸馏 → 待审提案 |
| consolidation | 每日 03:30 | 碎片折叠 + 衰减突触修剪 |
| param_advice | 按需 | 参数优化建议（论文信号驱动） |

---

## 四、核心能力详解

### 4.1 四路混合检索

兰台的检索引擎融合四种互补信号：

1. **向量语义检索**（ChromaDB，bge-m3）— 语义相似度
2. **jieba BM25 词级检索** — 关键词精确匹配
3. **FTS5 trigram 子串检索** — 容错子串召回（解决中文分词边界问题）
4. **时效衰减加权** — 新记忆优先，过期降权

权重可配，支持 lane boost（人格偏好增益 1.05x）、reranker 乘数、supersedes 边感知降权。

**拾遗降级**：Embedding API 异常时平滑降级至本地 FTS5 + BM25 + LIKE 子串三级兜底，杜绝零召回击穿。

### 4.2 三态去重（校雠）

| 阶段 | 机制 | 结果 |
|------|------|------|
| 余弦预筛（提取前） | cosine ≥ 0.95 → 直合；< 0.65 → insert | 高/低相似度快速分流 |
| 结构判别（提取后） | 锚点 + 归一化值规则 + 中带 LLM 兜底 | merge / update 提案 / insert |

36 对中文样本实测证明：单一余弦阈值无法分离 merge/update，更新类 5/12 被误判 merge 静默吞掉新值。两相位设计彻底解决此问题。

### 4.3 认知闭环

```
任务失败 → FailureRecord
    → ReflectionEngine 模式检测 → CognitivePattern(candidate)
    → 观测积累达标 → Belief(active)
    → 信度持续验证 → Rule(promoted)
```

知识生命状态机：`candidate → Active → Weakened → Superseded → Retired`，与管道状态（active/candidate/archived）正交。

### 4.4 记忆治理体系

| 机制 | 中文名 | 原则 |
|------|--------|------|
| 候选入队 | 锦囊 | REJECT 不静默丢弃，进待审队列 |
| 沙汰过滤 | 沙汰 | conf < 0.15 自动淘汰噪音 |
| 候选精炼 | 披沙 | 模糊带候选 LLM 二次提纯 |
| 价值评定 | 考功 | 长程反馈驱动升降 |
| 碎片折叠 | 沉潜 | 夜间归纳 + 突触修剪 |
| 冲突消解 | 参商/探颐 | 规则层 + LLM 裁决 + 主动探针求证 |
| 版本回滚 | checkpoint | 每次变更前快照，可一键回滚 |

**铁律：宁 miss 不脏写** — 校验失败不静默丢弃、不自动修正，候选进待审队列交用户裁决。

### 4.5 悬镜（可视化控制台）

零构建原生 ES Module 单页应用（`/ui`），FastAPI 同源托管：

| 模块 | 功能 |
|------|------|
| 案牍工作台 | 七类待办汇总 + 批量审批/拒绝/延期 |
| 器识工作室 | L/G/E 人格基座在线编辑与保存 |
| 札记便签 | 工作区暂存夹读写 |
| 检索演练场 | 四路分数实时拆解 + 探针提示 |
| 记忆星图 | SVG 放射布局 + scene 聚簇 + 来源文档溯源 |
| 司天监控 | 八域指标 + 13 条告警 + 请求趋势 + worker 补跑 |
| 档案浏览 | 全库分页检索 + 衰减概览 |
| 检索质量 | 零召回率 + lane/intent 分布 + token 粗估 |

双主题：**吉金**（青铜深色拓片底 + 云雷纹饰带）、**漏窗**（园林浅色绢黄底 + 月洞门卡片）。

---

## 五、MCP 工具面

Lantai MCP Server 提供 **55 个工具**，覆盖完整记忆生命周期：

| 类别 | 工具 | 说明 |
|------|------|------|
| 检索 | `search`, `verbatim_search`, `graph_expand_search`, `recall_chain` | 混合/原文/图谱/广播链 |
| 写入 | `add`, `raw_add`, `add_dialogue`, `dialogue_add_async` | 标准/原文/对话/异步 |
| 反馈 | `feedback`, `backfill` | 有用性反馈 + 事后标注 |
| 候选 | `candidates_pending`, `candidate_review`, `candidate_refine` | 待审/裁决/精炼 |
| 提案 | `proposals_list`, `proposal_decide` | 查看/裁决 |
| 冲突 | `conflicts_list`, `conflict_resolve` | 查看/消解 |
| 探针 | `probe_detect`, `probe_resolve` | 主动求证/闭环消解 |
| 认知 | `reflect_run`, `autodream_trigger`, `autodream_report` | 反思/蒸馏 |
| 演化 | `memory_consolidate`, `consolidation_report`, `kaogong_eval` | 折叠/考功 |
| 人格 | `persona_get`, `persona_set` | 器识读写 |
| 便签 | `scratchpad_get`, `scratchpad_write` | 札记读写 |
| 快照 | `checkpoint_write`, `checkpoint_latest`, `rollback` | 底本/回滚 |
| 知识 | `tree_view`, `tree_add`, `tree_assign` | 分类树 |
| 结晶 | `crystals_list`, `crystals_detect`, `crystal_decide` | 技能结晶 |
| 图谱 | `graph_view`, `scene_get`, `scenes_list` | 星图/场景 |
| 运维 | `mem_help`, `mem_sync`, `mem_create_skill`, `mem_health` | 命令/健康 |
| 核心 | `core_memory_get` | 核心记忆块 |
| 可观测 | `recall_report`, `mem_stats`, `mem_recent`, `mem_usage` | 监控/统计 |
| Wiki | `wiki_read` | 知识库下钻 |
| Obsidian | `obsidian_sync` | 双链同步 |
| 卸载 | `offload_read` | 超长记忆取回 |
| 任务 | `dialogue_task_status` | 异步任务状态 |

---

## 六、安全与治理

### 6.1 鉴权体系

```
X-API-Key（命中即 admin）→ 库内 Bearer Token → DEV MODE（仅回环+无 API_KEY+空库）
```

- 默认回环绑定（127.0.0.1），非回环强制 Token 鉴权
- SSRF 防护：协议白名单 + DNS 解析 IP 拦截
- 数据 URI 校验：MIME 白名单 + base64 严格解码 + 10MB 上限

### 6.2 数据安全

- 原子备份恢复（在线备份 + manifest SHA256 校验）
- Schema 版本化迁移（v1→v20），异常只记日志不阻断启动
- Checkpoint 快照：每次记忆变更前对比快照，可一键回滚
- ConflictEvent 审计账本：冲突双方 + 裁决历史完整记录

### 6.3 宁 miss 不脏写

这是贯穿整个系统的铁律：

- 校验失败不静默丢弃、不自动修正 → 进待审队列
- LLM 异常/超时 → 优雅降级保持原始数据不变
- 块 < 3 字符不落库，> 600 截断
- 越界 lane 拒绝落库
- 父节点缺失/重名/非法名 → 422 拒绝

---

## 七、测试与质量

### 7.1 测试体系

- **849 个测试用例**，全绿
- 测试纪律：**每个核心函数至少一个不 mock 的冒烟测试**
- mock 仅允许用于外部网络（LLM/embedding/reranker）、文件系统副作用
- **不允许** mock 让被测函数跳过内部计算逻辑

### 7.2 中文记忆评测集

80 case（v3），五类覆盖：

| 类型 | 数量 | 测试目标 |
|------|------|----------|
| typo（错别字容错） | 23 | FTS5 trigram 子串模糊召回 |
| fresh（新鲜对照） | 18 | 活跃记忆必须被检索到 |
| stale（陈旧残留） | 14 | 衰减归档记忆不应干扰检索 |
| temporal（时效排序） | 13 | 新记忆排在旧记忆之前 |
| superseded（取代排序） | 12 | 新值排在被取代旧值之前 |

六维门禁（stale=0 / typo=1 / fresh=1 / temporal=1 / superseded=1 / residual 只报告），CI 自动执行。

### 7.3 发布门禁

`scripts/release_check.py` 只读核查：
- 五处版本号一致性（pyproject / README / FastAPI / MCP serverInfo / CHANGELOG）
- Git 分支干净 / 工作区无脏文件 / tag 不重复 / origin 存在
- `--online` 查远程 tag

---

## 八、中文典籍命名体系

兰台采用独特的中文典籍意象命名规范（ADR-0013），每个新功能/机制的正式名称必须满足：

- **2–4 字**，出自传统意象（官职/典籍/器物）
- **名实相副**：名称直观反映功能本质
- **先登记再使用**：必须先在 CONTEXT.md 词汇表登记

当前已登记 40+ 术语，覆盖系统各个层面。三大意象来源：

| 来源 | 示例 |
|------|------|
| 古代官职 | 兰台、考功、拾遗、司天、持节 |
| 典籍经义 | 探颐（《易经》）、沉潜（《荀子》）、潜移（《文心雕龙》）、辨域（《周礼》） |
| 器物意象 | 悬镜（宝镜）、吉金（青铜）、漏窗（园林）、烽燧（边关烽火） |

---

## 九、路线展望

基于当前架构与 CONTEXT.md 已登记的预留术语，可以预见的方向包括：

- **持节（智能体自主审批 ADR-0039）**：受信任 Agent 自主治理记忆库
- **润物（自适应认知中间件）**：透明为请求自动装载相关规则与历史教训
- **知命（知识生命状态机）**：知识全生命周期演进追踪
- **认知闭环基准测试**：行为学习基准（失败→经验→规则→复用）
- **性能优化**：Embedding 缓存、批量向量操作、查询计划优化

---

## 十、技术规格快览

| 项目 | 值 |
|------|-----|
| 语言 | Python 3.11+ |
| Web 框架 | FastAPI + Uvicorn |
| 结构化存储 | SQLite（WAL mode，busy_timeout=30s） |
| 全文索引 | FTS5 trigram |
| 向量存储 | ChromaDB（嵌入式，cosine HNSW） |
| 词级检索 | jieba + rank-bm25 |
| 嵌入模型 | BAAI/bge-m3（默认，可配） |
| LLM 网关 | OpenAI 兼容 API（chat.completions） |
| 调度器 | APScheduler（SQLAlchemy jobstore） |
| MCP 工具 | 55 个 |
| REST 路由 | 27+ 核心路由组 |
| 测试 | 849 例全绿 |
| DB Schema | v20（增量迁移） |
| 许可证 | MIT |
| 端口 | REST :8767 / MCP :8766（默认回环） |
| 配置 | Pydantic BaseSettings（60+ 环境变量） |
| 部署 | 源码 / pip / Docker（amd64 + arm64） |

---

*本白皮书由兰台 v0.21.0 代码库与完整 CHANGELOG 自动生成，反映截至 2026-09-17 的全部公开演化历程。*
