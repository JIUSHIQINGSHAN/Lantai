# 兰台记忆（Lantai）项目白皮书

**版本**: v0.14.0（代号「缥缃」）
**日期**: 2026-08-13
**定位**: AI Agent 长期记忆引擎 —— 为 AI 建一座不忘过往的记忆档案库

> 记忆不是记事，而是不忘过往的点点滴滴。
> 不只是存储 —— 是检索、演化、遗忘的完整闭环。

---

## 一、执行摘要

兰台记忆（Lantai）是一套面向 **AI Agent 的本地优先长期记忆管理系统**。它不是简单的键值存储，而是一条完整的记忆生命周期链路：

```
摄取 → 闸门 → 演化 → 遗忘 → 检索
让 AI 在对的时间，找到对的回忆。
```

项目改编自 [aiduMEM（优忆思）](https://github.com/monkey2jack/aiduMEM)（MIT License），移植了相关性闸门、潮波并忆、Ebbinghaus 遗忘、Chronos 时间感知等设计思想；在此基础上**重新实现了存储层**（SQLite + FTS5 + ChromaDB）、**补全了 Shell Hook / MCP 双形态集成**与**完整安全加固**，并持续吸收 TencentDB Agent Memory 等业界方案的可借鉴窄版设计。

截至 v0.14.0：

| 维度 | 现状 |
|------|------|
| 版本 | v0.14.0「缥缃」，21 份 ADR 架构决策记录 |
| 代码规模 | 23 个 REST 路由模块、64 个端点、38 个 MCP 工具 |
| 测试 | 60+ 测试文件全绿（README 徽章 701/701） |
| 质量自证 | 中文记忆评测集 v1（13 case / 六维指标，可复现、可 CI 门禁） |
| 集成 | Shell Hook（读）+ MCP server（写）+ Hermes 插件 + Docker/GHCR |
| 部署 | 单机零外部服务（SQLite + 内嵌 ChromaDB），Docker 一键 |

---

## 二、背景与问题

### 2.1 大模型的无状态困境

当前大语言模型（LLM）本质上是**无状态的**：每次对话都是独立推理，上下文窗口有限、成本随长度上升。AI 要成为可靠助手，必须回答三个问题：

- **记住什么** —— 用户的偏好、事实、规则从哪来、存哪里？
- **何时想起** —— 什么时候检索记忆、检索哪些、注入多少？
- **何时忘记** —— 过期信息如何降权，而不是永久污染上下文？

### 2.2 现有方案的普遍缺陷

| 问题 | 表现 |
|------|------|
| **无差别检索** | 每轮对话都全量检索，寒暄/社交结束语也触发，上下文被无关内容污染 |
| **只存不亡** | 记忆只增不删，重复堆积，陈旧信息与新鲜事实互相打架 |
| **写入无节流** | 每条短消息一次 LLM 提取调用，成本与延迟双高 |
| **检索黑盒** | 为什么召回、每步贡献多少分，不可见、不可调、不可测 |
| **记忆不可溯源** | 无法回答"这套记忆是谁、用什么 prompt、什么时候产出的" |
| **中文场景缺位** | 西文生态基准（LoCoMo / LongMemEval / BEAM）不覆盖中文错别字、分词、时效场景，且厂商分数互不透明、不可复现 |
| **集成碎片化** | 无标准读/写通道，Agent 框架对接成本高 |

### 2.3 兰台的回答

兰台以「**会记忆、会演化、会遗忘**」为设计目标，用一套确定性优先、LLM 兜底的机制链解决上述问题，并坚持三条底线：**数据本地优先**（默认零外部服务）、**宁 miss 不脏写**（不静默丢弃、不自动修正错误）、**安全是基线**（默认回环、强制鉴权、SSRF 防护）。

---

## 三、设计理念与原则

兰台的全部机制由以下八条原则推导而来，它们是架构决策（ADR）与代码评审的共同依据：

1. **宁 miss 不脏写**（核心铁律）
   校验失败（低置信度提取、非法输入、越界 lane）**不静默丢弃、不自动修正**：候选进待审队列（锦囊）交用户裁决，超龄自动归档为 rejected；非法请求一律显式报错。写脏数据的代价永远高于漏掉一条记忆。

2. **遗忘是特性，不是 bug**
   记忆永不物理删除，只降权（ADR-0005）。decay 极低的记忆自动转 archived，不参与检索，但随时可回滚恢复 —— 误删风险为零。

3. **记忆有出生证明（provenance）**
   候选 → 提案 → 记忆全程同源携带 `{prompt, model, extracted_at}`（ADR-0015），"记忆质量变差"是可溯源的问题，而不是猜测。

4. **确定性优先，LLM 兜底**
   闸门规则、余弦去重、场景聚类、冲突规则等一律先走确定性路径；LLM 仅用于命名/摘要等无法规则化的环节，失败时确定性降级，绝不把错误写进库。

5. **渐进式披露**
   上下文是稀缺资源：Shell Hook 注入受单条/总字符双预算约束；场景导航、技能块、卸载摘要层层下钻，不一次撑爆上下文。

6. **知识写入有刹车**
   演化走 候选 → 提案 → 应用/拒绝 全流程；每次变更生成 Checkpoint 快照，`/rollback` 一键回滚；技能结晶等沉淀必须人工裁决。

7. **测试不 mock 核心逻辑**
   每个核心函数必须有真实构造最小输入直调该函数的不 mock 冒烟测试 —— 曾有三个真实 bug（FTS schema、Chronos 时区、BM25 `ptp()`）因测试 mock 了外部依赖而未被执行到，此纪律自此确立。

8. **命名有纪律**
   正式名称遵循 ADR-0013 命名体系：2–4 字、出自传统意象（官职/典籍/器物）、名实相副、**先登记 `CONTEXT.md` 词汇表再使用**。

---

## 四、系统架构

### 4.1 分层结构

```
┌──────────────────────────────────────────────────────┐
│   🧠 兰台记忆（Lantai）— 记忆引擎                      │
│   FastAPI REST API :8767（默认 127.0.0.1）             │
├──────────────────────────────────────────────────────┤
│  接入层    REST（api/ 薄路由）· Shell Hook · MCP server │
│  业务层    services/（门面铁律 ADR-0001，逻辑下沉）      │
│  机制层    gate/  ingestion/  evolution/  retrieval/   │
│            memory/  ops/  observability/  parameters/  │
│  存储层    SQLite（结构化 + FTS5 trigram）              │
│            ChromaDB（向量，cosine）· jieba BM25 缓存    │
└──────────────────────────────────────────────────────┘
```

- **接入层**：路由 handler 只做 HTTP 解析/返回，业务逻辑全部下沉 service 层（门面铁律 ADR-0001）。
- **机制层**：闸门（准入）、摄取（潮波/适配器/SSRF）、演化（提案/回滚/反思）、检索（四路融合/意图/精排）、遗忘（衰减/归档）分域自治。
- **存储层**：单库单事务 —— MemoryItem 的增/改/回滚/删 4 个写入点与 FTS 索引**同库同事务提交**（ADR-0008），零异步补写弱一致。

### 4.2 数据生命周期

```
RawDocument ──提取──▶ MemoryCandidate ──闸门──▶ MemoryProposal ──应用──▶ MemoryItem
  （原文）   (LLM/直书)   （候选，锦囊待审）   (add/update/merge/   （落库+FTS+向量）
                                                 deprecate)
                                                    │
                                              Checkpoint 快照（可回滚）
                                                    │
                                            遗忘衰减 decay_score 指数下降
                                                    │
                                              archived（不参与检索，物理不删）
```

### 4.3 存储选型（ADR-0004）

| 组件 | 选型 | 理由 |
|------|------|------|
| 结构化存储 | SQLite + SQLModel | 单文件、零运维、事务内 FTS 原子一致 |
| 全文索引 | SQLite FTS5（trigram） | 内置能力零新依赖；子串匹配对中文错字/插删天然容错 |
| 向量存储 | ChromaDB（cosine） | 内嵌零外部进程，Qdrant 需额外运维（否决） |
| 关键词层 | jieba + rank-bm25 | 轻量成熟；双字中文词（"学习""看书"）可命中（trigram 需 ≥3 字符） |
| Embedding | BAAI/bge-m3（默认） | 已在生产使用，维度问题通过删硬编码解决 |
| 演进 | 不引入 mem0 | 与既有 gate/evolution/forgetting 链路冲突 |

### 4.4 Schema 版本化迁移

`PRAGMA user_version` 增量迁移链：老库自动基线 → 逐版本 `_ensure_column` 补齐，**老库零丢失**，重复启动 no-op。已支撑 10 次增量迁移（scene_id、质心、RetrievalEvent 扩展、provenance 等）。

---

## 五、核心机制

### 5.1 闸门（gate）—— 只检索真正相关的内容

普通 RAG 对每条消息都检索记忆。兰台的**相关性闸门**先用启发式规则判定当前消息是否真的需要记忆检索：

- 纯社交结束语、寒暄 → 直接跳过，**无关查询零检索成本**；
- 纠错、明确回忆、上下文延续 → 立即命中；
- 15 秒热缓存让追问零开销（`GATE_CACHE_TTL`）。

候选准入分三关：**置信度阈值 + 新颖度评分 + 矛盾检测** → 五档决策（reject / working_only / promote_semantic / promote_procedural / archive_conflict）。被拒候选不静默丢弃，落入待审队列「锦囊」（`pending_review`），TTL 7 天自动归档。

### 5.2 潮波并忆（coalesce）—— 批量提取，不逐条调用

短消息按 `user_id + lane` 分键缓冲（ADR-0003），按 lane 档位（空闲超时 / 时间窗 / 条数 / 字符数，`LANE_COALESCE_PROFILES`）触发冲刷，**一次 LLM 调用处理多条消息**。`/add` 单入口自动分流，`COALESCE_ENABLED` 开关，缓冲水位（active_keys + total_messages）由 `/stats` 暴露可监控。

### 5.3 直书（fastpath）—— 高频句型不走 LLM

三类句型（自我声明 / 偏好表达 / 显式指令）经正则白名单（`parsing/fastpath.py`）**直接写入**，精度 ≥95%，命中即返回 `fastpath_candidate`，不入缓冲、不调用 LLM。"记住我叫小明"这类句子本就不需要大模型提取。

### 5.4 校雠（去重）—— 不写垃圾比事后清理便宜

候选创建时、闸门之前执行**两相位三态判定**（ADR-0019）：① 余弦预筛——提取前 sim ≥ 0.95 直合（真重复零 LLM）、< 0.65 插入；② 中带提取后**结构判别**（锚点 + 归一化值规则，中带 LLM 兜底、失败降级 insert）。实测定论：**单一余弦阈值无法分离 merge/update**（36 对 / 3 类，更新类 5/12 曾被误判 merge 吞掉新值），判别信号在结构而非相似度高低。值变更走待审提案（有刹车），杜绝「merge 吞新值」。

### 5.5 演化（evolution）—— 知识生长与自我纠错

- **提案制**：候选过闸后生成变更提案（add / update / merge / deprecate），`/proposals/{id}/decide` 批准或拒绝 —— **知识写入有刹车**；
- **Checkpoint 回滚**：每次记忆变更生成 before/after 快照，`/memory/{id}/rollback` 一键回到上一版本；
- **冲突账本（conflict_event）**：互斥规则集（settings 可配）优先、LLM 回落双通道（ADR-0010），规则命中写审计账本，人工裁决不改记忆状态 —— 确定性、可溯源、可裁决。

### 5.6 遗忘（forgetting）—— Ebbinghaus 指数衰减

记忆有保质期。按 lane 分轨的指数衰减：`fact` 半衰期 30 天、`chat` 仅 3 天、`preference` 15 天。decay 低于阈值自动转 `archived`——**归档不参与检索（`WHERE status='active'`）、物理不删、可回滚**（ADR-0005）。skill 类 procedural 记忆永不衰减（天然浮顶）。

### 5.7 克罗诺斯（Chronos）—— 双时间轴时效

`valid_from` / `valid_to` 时间窗口：**未生效记忆直接过滤，过期记忆降权保留**。设了时间窗的记忆自动受控，"项目 3 月 15 日截止"到期后不再以满分打扰检索。

### 5.8 四路混合检索 —— FTS5 trigram 是亮点

```
score = 0.6·向量语义 + 0.25·jieba BM25 + 0.05·FTS5 子串命中 + 0.1·时效衰减
```

- **向量**：bge-m3 embedding，ChromaDB cosine；
- **BM25**：jieba 分词词级关键词，语料缓存避免全量重建；
- **FTS5 trigram**：子串匹配对中文错别字、插入删除天然容错（"向良"能撞上"向量"）——这是 jieba 给不了的；FTS 命中但向量漏掉的记忆**追加召回**（ADR-0008）；
- **时效衰减**：decay_score 与 Chronos 时间窗参与排序；
- **supersedes 降权**：被取代旧值压到新值之下，新值缺席不动旧值（宁 miss 不脏写）；
- **Reranker**：可配置（兼容 OpenAI Rerank API），失败自动降级；
- **search_trace**：`/search?trace=true` 返回逐步诊断 `{step, elapsed_ms, candidate_count, score_range}`，overhead < 1ms —— 检索不再是黑盒。

### 5.9 场景聚合（scene）—— 渐进式披露的导航

embedding 余弦聚类构建 `MemoryScene`（ADR-0012），heat = 成员 `use_count` 求和（零写放大）。检索命中场景成员时，**导航块优先注入**（`## Scene: 名称（热度 N）` + 摘要 + 成员 key），详情用 `scene_get` 下钻 —— 跨场景上下文不割裂，注入体积可控。

### 5.10 技能资产（skill）—— 可执行步骤沉淀

`structure.steps` 非空的 procedural 记忆以 `## Skill: 名称` + 描述 + 编号步骤注入上下文（ADR-0011），Agent 可照步骤执行；步骤非空强制 `decay_class="procedural"` 永不衰减。`mem_create_skill` 提供零 LLM 结构化落库通道（ADR-0014）。

### 5.11 原文直存（verbatim）—— 零 LLM 快速通道

`POST /add/raw`：内容零 LLM 直入 FTS5 + 向量（ADR-0009），sha256 幂等去重，不走提取/闸门/演化。Obsidian 笔记同步、冷启动 JSONL 导入均复用该通道（ADR-0018），导入保留原始时间戳，非法行记报告不静默修正。

### 5.12 上下文卸载（offload）

超长记忆全文落 `docs/memory-offload/{memory_id}.md`（ADR-0016），Shell Hook 上下文只注入「摘要 + 路径」，需要时经 MCP `offload_read` 取回全文 —— **上下文不随单条记忆长度增长**，白名单文件名防目录穿越。

### 5.13 记忆 Wiki

场景/技能持续维护为 `docs/memory-wiki/` 页面 + `index.md` 索引 + `overview.md` 综述（ADR-0017），`[[wikilink]]` 下钻经 MCP `wiki_read`；`mem_sync` 三件套（scene + digest + wiki）一键刷新。

### 5.14 记忆星图（graph）—— 关系可视化

`GET /graph` 只读聚合（v0.9）：节点 = active 记忆 + 参与边的来源文档（doc_*，出处可溯），链接 = MemoryEdge（supports 绿 / refines 蓝 / contradicts 橙 / supersedes 红）；`/ui/map` 零依赖内联 SVG 放射布局，点击记忆跳档案检索、点击来源开原文 URL。

### 5.15 烽燧（recall_chain）—— 记忆广播链

从 seed 记忆出发，以它为 query 走混合检索，命中结果再作下一层 seed —— BFS 逐层传播（v0.11），呈现"记忆如何触发关联记忆"。零写入，单条搜索失败只缺层不阻断。

### 5.16 目识（Vision）—— 多模态感知

`/add` 与 MCP `add` 支持 `media_url`（仅 http/https/data，白名单校验，兰台不直接 fetch 图片 —— **零 SSRF 面**），复用单一 LLM 网关的 Vision 调用生成 caption（v0.10）；`scripts/screenshot_memory.ps1` 截屏入忆（v0.12）。失败抛 ValueError，不落失败文本。

### 5.17 反思（reflect）—— 自我审视回环

每日健康扫描 + 水位触发蒸馏 + 提案裁决（"吾日三省吾身"）：健康候选与水位触发 → curator 提炼 → rejecter 复核 → 自动应用/待审/丢弃。`reflect_run` 落库可审计（每次运行的空闲/产出/LLM 失败/异常），观察期满后用真实分布回填校准阈值。

### 5.18 蒸馏（autodream）与技能结晶（crystal）

- **autodream**：同 lane + 共享关键词贪心聚类，合成记忆落为待审提案（低置信度进 skipped，宁 miss 不脏写）；
- **crystal**：高频重复记忆自动聚类 → `SkillCrystal` 候选（Mímir 铁律：规则只能建议不能 commit），人工裁决 approve 必须带非空 steps 才落成 Skill 资产，reject 归档记 reason。

### 5.19 分类树（tree）与核心记忆（core-memory）

- **tree**：显式父子层级（`MemoryNode` 表）+ `node_path` 唯一路径 + depth 前缀查询，记忆经 `tree_path` 显式挂载 —— 按主题组织记忆全景；
- **core-memory**：identity / task / policy 三块核心记忆，`/core-memory` 读写，Agent 身份与任务基线。

### 5.20 访问收窄（ACL）

按 `agent_id` 绑定 lane 集（`AGENT_LANE_BINDINGS`）：绑定 agent 只能检索/写入自己 lane 集内的记忆（ADR-0013 登记），缺失/未绑定 403，检索结果宁 miss 不放行；空配置 = 不启用，默认零行为变化。

---

## 六、集成形态

> 读有 Hook，写有 MCP —— 各走各的快路径。

### 6.1 Shell Hook（读，零依赖）

- stdin 收 JSON，stdout 返回 Markdown 上下文，**2s 硬超时**（ADR-0006）；
- ≤3 字符不注入；码点安全截断 + 召回预算（单条 ≤200 字符、总量 ≤1500 字符，超预算丢弃并附提示）；
- 有命中时注入「本次依据」证据段（记忆 id + 摘要）与「记忆使用指南」；
- `--serve` 常驻 NDJSON 模式消除冷启动成本。

### 6.2 MCP server（写，标准协议）

- 标准 JSON-RPC 2.0（protocolVersion 2024-11-05），serverInfo「lantai」；
- **38 个工具**：search / add / feedback 基础三件，覆盖 raw_add、rollback、conflicts、scene、tree、crystal、wiki、offload、digest、graph、recall_chain、vision、provenance 查询、反思触发等全部能力面；
- 输入校验 + 异常隔离，缺参返回 -32602；
- **客户端矩阵**：Claude Code / Cursor / Gemini CLI / Codex / Hermes 五端接入指南与逐端合规验证清单（`docs/mcp-client-matrix.md`）。

### 6.3 Hermes 插件（对话自动写入）

`pre_llm_call` 把 user_message 累积到有界会话缓冲，`on_session_end` 每轮对话结束 flush 给对话摄取链（`ingest_dialogue`）：直书直通、提取建候选、闲聊入待审队列 —— 对话即记忆，无需显式调用。

---

## 七、安全设计（护盾）

> 神盾护住的不是代码，是代码背后的人。

| 防线 | 机制 |
|------|------|
| **默认回环绑定** | 默认监听 `127.0.0.1`；非回环地址必须配置 `API_KEY`，否则拒绝启动（启动守卫 `assert_secure_binding`） |
| **鉴权** | `API_KEY` 走 `hmac` 恒时比较，防时序侧信道 |
| **SSRF 防护** | 外部抓取协议白名单 + DNS 解析后逐 IP 阻断私网/回环/link-local + 重定向逐跳复验 + 响应限长 |
| **备份/恢复原子化** | SQLite online backup 一致性快照 + manifest sha256 校验 + 路径限定 + 原子换入 + fail-closed 停服保护 |
| **端点白名单** | LLM/精排 base_url 域名 allowlist，独立最小权限 `RERANKER_API_KEY`，密钥不落日志 |
| **供应链** | GitHub Actions 锁定 commit SHA（非可变 tag）、Docker 镜像非 root 运行 |
| **数据主权** | 单机本地优先：SQLite + 内嵌 ChromaDB，默认零外部服务、零遥测 |

---

## 八、质量保障与可观测性

### 8.1 测试纪律（Testing Discipline）

**每个核心函数必须至少有一个不 mock 的冒烟测试** —— 真实构造最小输入直调该函数，验证主路径不炸。背景：v0.3.2 修复中暴露的 FTS schema、Chronos 时区、BM25 `ptp()` 三个真实 bug，全部因为既有测试 mock 了外部依赖、产品代码从未被真实执行到。mock 仅允许用于外部网络（LLM/embedding/rerank）与文件系统副作用。

### 8.2 测试基线

- 60+ 测试文件、700+ 用例全绿（README 徽章 701/701），覆盖 FTS 集成、SSRF、备份恢复、MCP 协议、Shell Hook 超时、迁移链、ACL、场景、Wiki、Vision、召回链等；
- 测试进程内永不启动真实调度器（fixture 置空 `start_scheduler`），全量顺序执行无污染。

### 8.3 中文记忆评测集 v2（chinese-memory-v2）

面向「中文 / 错别字容错 / 遗忘质量 / 时效」的检索自证基准 —— 西文生态基准不覆盖此场景且不可复现，兰台以**本地可复现命令**作为主张依据：

- **50 case**（v2，2026-08-14 由 v1 13 case 扩编）= typo×15 / fresh×12 / stale×8 / temporal×8 / superseded×7，命名空间隔离不污染真实库；
- 六维指标：陈旧残留率（0.0）、错别字容错命中（1.0）、对照召回（1.0）、时效排序（1.0）、取代排序（1.0）、取代残留（诚实测量）；
- 两条复现命令，门禁模式 FAIL 退出码 1；**已纳入 CI**（`.github/workflows/tests.yml`，push/PR 全量 pytest + 门禁）。

### 8.4 可观测性

- **search_trace**：检索逐步诊断，overhead < 1ms；
- **RetrievalEvent**：检索事件落库（含 scene_ids、token 估算），零召回率监控排除系统噪音，7 天窗口聚合报告；
- **每日盘点（digest）**：新增/修改/总量/待审/归档/检索六项统计 + 置信桶分布；
- **反思可审计**：每次 `reflect_run` 的空闲/产出/LLM 失败/异常落库；
- **概览与门禁**：`memory_overview`（按 lane/decay_class 分布）、`run_forgetting_quality.py --check` 五维门禁、`scripts/release_check.py` 发布门禁（版本一致性 + Git 干净 + tag 不重复，上传保持人工闸门）。

---

## 九、技术栈

| 域 | 选型 |
|----|------|
| 运行时 | Python 3.11+、FastAPI、Uvicorn、APScheduler |
| 结构化数据 | SQLModel + SQLite（+ FTS5 trigram 全文索引） |
| 向量存储 | ChromaDB（cosine，内嵌零外部依赖） |
| 检索 | jieba + rank-bm25；Reranker 可配置（兼容 OpenAI Rerank API） |
| 大模型 | 兼容任何 OpenAI 格式 API（默认 bge-m3 embedding；LLM 提取、命名、摘要、反思） |
| 部署 | Docker 多阶段构建 → GHCR（`ghcr.io/<owner>/remembrance:vX.Y.Z`） |
| 工程 | Ruff（lint+format）、pytest、pre-commit、GitHub Actions（tag 触发 CI） |

配置全部经环境变量 / `.env` 注入，**全部可选** —— 不设置就走安全默认值（`lantai/core/settings.py`）。

---

## 十、部署方式

**方式一：源码克隆**

```bash
git clone https://github.com/JIUSHIQINGSHAN/Lantai.git && cd Lantai
python -m venv .venv && .venv\Scripts\activate
pip install -e .
cp .env.example .env          # 填入 OPENAI_API_KEY（必填）
python scripts/init_db.py
python api_server.py           # http://127.0.0.1:8767
```

**方式二：Docker**

```bash
docker run -d -p 8767:8767 \
  -e API_KEY=your-admin-key -e OPENAI_API_KEY=sk-xxx \
  -v /your/data:/data \
  ghcr.io/JIUSHIQINGSHAN/remembrance:v0.14.0
```

> 容器默认 `HOST=0.0.0.0` 对外暴露，**必须注入 `API_KEY`**——启动守卫会在非回环地址且无密钥时拒绝运行。

---

## 十一、应用场景

1. **AI 助手个性化记忆**：偏好、事实、规则按 lane 分轨沉淀，闲聊进锦囊待审，隐私数据本地保存；
2. **开发 Agent 项目知识沉淀**：对话自动写入 → 技能结晶 → 可执行 Skill 资产，团队新人秒级继承项目上下文；
3. **客服/对话系统上下文**：相关性闸门拦截寒暄、明确回忆直取，检索预算可控，token 成本可估算可监控；
4. **个人知识管理**：Obsidian 笔记同步、JSONL 冷启动导入、记忆星图浏览、场景导航检索；
5. **多 Agent 隔离协作**：ACL 按 agent 绑定 lane 集，各 Agent 记忆互不可见、宁 miss 不放行；
6. **研究/论文追踪**：RSS / arXiv 适配器摄取，提取为结构化记忆并参与混合检索。

---

## 十二、版本历史与路线图

### 12.1 里程碑

| 版本 | 主题 |
|------|------|
| v0.1.0 | 初始发布（改编自 aiduMEM）：存储层、四路检索、闸门/潮波/遗忘/Chronos 基线 |
| v0.3.1–v0.3.3 | P0/P1 审计修复：仓库卫生、绑定鉴权、SSRF、原子备份恢复、MCP 校验 |
| v0.3.4–v0.3.6 | FTS5 并列接入（ADR-0008）、BM25 缓存、测试全绿、供应链加固 |
| v0.4–v0.5 | 锦囊待审队列、对话写通道、检索透明、provenance、技能资产、场景层 |
| v0.6 | Schema 版本化迁移、中文记忆评测集 v1、遗忘质量门禁 |
| v0.7 | 分类树、技能结晶、记忆 Wiki、上下文卸载 |
| v0.8–v0.9 | MCP 工具面扩至 38、记忆星图、反思可审计 |
| v0.10–v0.12 | 目识 Vision、烽燧召回链、截屏入忆、UI 入口 |
| v0.13–v0.14 | 中国色换肤、双主题（吉金/漏窗）、发布门禁流程、代号「缥缃」 |

### 12.2 路线图

- [x] salience 冲突降权与 contradiction gate 整合（2026-08-14，ADR-0020）——反义词词级碰撞（8 对默认）+ 低 salience 旧记忆确定性冲突降权放行（Checkpoint 可回滚 + 账本 resolved），高 salience/LLM 矛盾维持人工裁决
- [x] autodream 7 天周期记忆蒸馏（2026-08-14）——`autodream_worker` 周期入口（`AUTODREAM_CRON_DAYS`=7 默认），落 pending 提案交人工闸门，`record_run` 可观测
- [x] checkpoint 五段会话快照（2026-08-14，ADR-0021）——底本：在做/下一步/工作区/决策/待办五段会话快照，压缩时写入、下次会话注入（>30 天陈旧标注），保留最近 5 会话；REST + MCP 双入口
- [x] 去重阈值实测校准（bge-m3 中文样本）——实测 36 对 / 3 类：单一余弦阈值无法分离 merge/update，升级结构判别（ADR-0019）；prototype 见 `.scratch/dedup-threshold-calibration/`
- [x] 评测集扩至 50+ case 并纳入 CI（2026-08-14）——chinese-memory-v2（50 case 全维度扩编），门禁 PASS，`.github/workflows/tests.yml` 全量 pytest + 门禁
- [x] arm64 Docker 镜像（2026-08-14）——CI `platforms: linux/amd64,linux/arm64`

---

## 十三、项目治理与工程规范

- **ADR 决策记录**：`docs/adr/0001–0021`，覆盖门面铁律、零硬编码、缓冲键、基础设施栈、遗忘语义、Hook 契约、MCP 形态、FTS5 接入、直存、冲突层、技能资产、场景层、命名体系、mem 命令、provenance、卸载、Wiki、导入、三态去重结构判别、salience 冲突降权、底本会话快照；
- **领域词汇表**：`CONTEXT.md` 为命名事实来源，新名称先登记后使用（ADR-0013）；
- **Issue 追踪**：本地 markdown 票据 + 五档分诊标签（`docs/agents/`）；
- **版本发布**：`docs/release-process.md` —— 发布门禁（版本一致性 + Git 干净 + tag 不重复）通过后，**push tag 为人工闸门**，Agent 只检查/准备。

---

## 十四、许可证与致谢

- **许可证**：MIT。改编自 [aiduMEM](https://github.com/monkey2jack/aiduMEM)（MIT License），由 [JIUSHIQINGSHAN](https://github.com/JIUSHIQINGSHAN) 构建；
- **致谢**：aiduMEM 六组设计思想（写入节流、全链可观测、数据自治、可插拔集成、克隆即跑、架构有纪律）；TencentDB Agent Memory（场景层、provenance、LLM-Wiki、offload、ACL、冷启动导入等窄版借鉴）；Hermes（对话钩子插件生态）。

---

## 附录：术语表（摘要）

| 术语 | 定义 |
|------|------|
| 兰台（Lantai） | 项目名，取自汉代皇家档案馆「兰台」 |
| 锦囊（Jinnang） | `pending_review` 待审候选队列 |
| lane | 记忆类型分轨：fact / rule / experience / preference / chat / general |
| 闸门（gate） | 记忆准入控制：置信度 + 新颖度 + 矛盾 → 五档决策 |
| 潮波（coalesce） | 短消息异步缓冲合并，减少 LLM 提取调用 |
| 直书（fastpath） | 白名单句型直写，宁 miss 不脏写 |
| 校雠（dedup） | 两相位三态去重：余弦预筛 + 结构判别（ADR-0019） |
| 克罗诺斯（Chronos） | `valid_from` / `valid_to` 双时间轴 |
| 烽燧（recall_chain） | 记忆广播链（BFS 逐层传播） |
| 目识（Vision） | 图片感知写入通道 |
| 吉金 / 漏窗 | v0.14 双 UI 主题 |
| 缥缃 | v0.14.0 版本代号（丝帛书衣，代指书卷） |

完整词汇表见 [`CONTEXT.md`](../CONTEXT.md)。

---

**文档索引**：`CONTEXT.md`（词汇表）· `docs/adr/`（架构决策）· `docs/aidumem-port-results.md`（移植结果）· `docs/memory-quality/`（评测与门禁报告）· `docs/mcp-client-matrix.md`（客户端接入）· `docs/release-process.md`（发布流程）
