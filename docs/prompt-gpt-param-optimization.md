# 提示词：为 Remembrance-System 设计「论文驱动的参数调整建议」功能方案

> 用法：将下方【提示词正文】整段复制给 GPT-5.6 Sol。其输出的完整方案将交由 DeepSeek-V4-Flash 执行实现。

---

## 提示词正文

```text
# 角色

你是一位深耕 AI Agent 长期记忆系统领域、同时精通工程化落地的资深架构师。你熟悉混合检索（向量 + BM25 + 全文索引 + 时效衰减）、记忆衰减模型（Ebbinghaus）、知识演化机制（提案/检查点/回滚）、以及把学术论文结论转化为工程参数决策的方法论。你擅长输出"可直接交付实施"的方案，而不是空谈架构。

# 任务

为一个已开源运行的 AI Agent 长期记忆系统（Remembrance-System，Python 3.11+ / FastAPI / SQLite+FTS5 / ChromaDB / jieba BM25）设计一个「论文驱动的参数调整建议」功能，并输出**完整的、可直接交给执行者实施的技术方案**。

功能定位：系统已具备定时从 arXiv / RSS 摄取最新论文的能力（内容层）。现在要把论文从"知识内容"升级为"系统自优化依据"（元层），采用**人工审阅的辅助模式**：

论文摄入 → LLM 依据论文 + 当前参数快照生成「参数调整建议」→ 进入待审队列（pending）→ 由用户审阅批准/拒绝 → 批准后带 before/after 快照应用 → 可一键回滚。

**注意：这是辅助模式，不是全自动。任何参数变更必须经过人工批准，宁可不建议，不可乱建议。**

# 项目现状（以下为已核实的代码事实，直接采信，不要臆测）

## 关键文件与机制

- `remembrance/ingestion/arxiv.py`：`ArxivAdapter`，从 arxiv API 拉最新论文（默认 `cat:cs.AI`，按 `submittedDate` 降序），摘要转为 `RawDocument`（`source_type="paper"`）
- `remembrance/ingestion/rss.py`：`RSSAdapter`，通用 RSS 源（`source_type="article"`）
- `remembrance/ingestion/registry.py`：`ADAPTERS = {a.kind: a() for ...}`
- `remembrance/workers/ingest_worker.py`：`run_ingest_once()`，APScheduler 定时任务（`INGEST_CRON_MINUTES` 默认 60）。流程：遍历 enabled 的 Source → 适配器 fetch → `content_hash` 去重 → `extract_candidate(title, content)`（LLM 结构化提取，见 `remembrance/parsing/extractor.py`）→ 生成 `MemoryCandidate`。**这是建议生成器的天然挂载点**
- `remembrance/evolution/proposer.py`：提案机制范例。`propose_from_candidate()` 调 `chat_json(PROPOSAL_SYS, user)`，LLM 返回 JSON，失败有 fallback，写入 `MemoryProposal`（status=PENDING），由用户 decide。**新功能的审阅模式应复刻此结构**
- `remembrance/evolution/promoter.py` / `reflector.py`：晋升 / 反馈记录（reflector 实为 `/feedback` 的反馈落库，非自我反思）
- `remembrance/llm/prompts.py`：现有 `EXTRACT_SYS` / `CONTRADICTION_SYS` / `PROPOSAL_SYS`，风格统一为 "Return strict JSON ... Only output JSON"，**新提示词必须延续此风格**
- `remembrance/llm/client.py`：`chat_json(sys, user)` 封装
- `remembrance/core/settings.py`：pydantic BaseSettings，**所有参数静态定义**，`settings = Settings()` 单例，模块级直接 `from ... import settings` 使用
- `remembrance/models/tables.py`：SQLModel 表（`RawDocument` / `MemoryCandidate` / `MemoryItem` / `MemoryProposal` / `MemoryCheckpoint` / `Source` / `IngestJob` 等），`id` 用 `new_id("前缀")` 生成
- `remembrance/api/`：薄路由（如 `routes_evolution.py` / `routes_sources.py` / `routes_memory.py`），业务逻辑下沉 service 层
- `remembrance/workers/`：`ingest_worker.py` / `evolve_worker.py` / `forgetting_worker.py`，APScheduler 注册
- `remembrance/core/scheduler.py`：定时任务调度器
- 测试：`tests/`（pytest），当前 120 个全绿

## 当前可调参数（settings.py 中与检索/演化相关的关键参数）

- 混合检索权重：`RETRIEVAL_W_VECTOR=0.6`、`RETRIEVAL_W_BM25=0.25`、`RETRIEVAL_W_FTS=0.05`、`RETRIEVAL_W_DECAY=0.1`（sum=1.0）
- 去重阈值：`DEDUP_MERGE_THRESHOLD=0.80`（>此值 merge）、`DEDUP_UPDATE_THRESHOLD=0.65`（>此值 update）
- Lane 分轨衰减：`LANE_DECAY_PROFILES = {"fact": {"base_s":30,...}, "rule": {"base_s":60}, "experience": {"base_s":10}, "preference": {"base_s":15}, "chat": {"base_s":3}, "general": {"base_s":10}}`（base_s=半衰期天；importance_boost 另计）
- Lane 检索权重提升：`LANE_RETRIEVAL_BOOST = {"fact":1.3,"rule":1.2,"experience":1.0,"preference":1.1,"chat":0.7,"general":1.0}`
- 归档阈值：`ARCHIVE_DECAY_THRESHOLD=0.01`
- 闸门：`GATE_MIN_EXTRACTOR_CONF=0.55`、`GATE_CACHE_TTL=15.0`
- 意图候选集大小：`INTENT_CANDIDATE_SIZES={"fact_lookup":10,"procedural":15,"exploratory":20}`

## 安全/结构参数（物理不可调，必须排除在白名单外）

`API_KEY`、`HOST`、`PORT`、`DATABASE_URL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`EMBED_MODEL`、`RERANKER_*`、`SSRF_*`、`ALLOWED_API_HOSTS`、`BACKUP_MANIFEST_VERSION`、`CHROMADB_PATH` 等。

# 设计哲学（硬性约束，方案不得违背）

1. **门面铁律（ADR-0001）**：重构只搬家不改语义，旧 import 必须全绿。
2. **宁 miss 不脏写（fastpath 原则）**：建议宁可不生成，不可乱生成。LLM 只能从参数注册表（白名单）中选择参数，输出必须通过严格 JSON Schema 校验，非法直接丢弃；LLM 调用失败静默跳过，不降级为"拍脑袋建议"。
3. **知识写入有刹车**：所有变更走审阅（proposal 模式），`pending → accepted/rejected`，可回滚（checkpoint 快照思想）。
4. **测试纪律（AGENTS.md）**：每个核心函数必须至少有一个不 mock 的冒烟测试（真实构造最小输入直调该函数，验证主路径不炸）。mock 仅允许用于外部网络（LLM/embedding）。
5. **风格一致**：新 LLM 提示词沿用 `PROPOSAL_SYS` 的 "Return strict JSON ... Only output JSON" 风格；表结构沿用 SQLModel + `new_id("前缀")`；路由保持薄，逻辑下沉 service。
6. **零硬编码（ADR-0002）**：新阈值/超时等一律进 settings 或注册表声明，不散落魔数。
7. 中文注释，代码/标识符用英文。

# 待你裁定的决策点（必须给出明确结论 + 理由）

1. **触发粒度**：单篇论文触发 vs 批量窗口（如每轮摄完后新论文 ≥N 篇 或 距上次建议 >M 天，类比项目已有的潮波并忆 Tidal Coalescing）。给出你的裁定与 N/M 建议值。
2. **白名单首期范围**：建议起步为 4 个检索权重 + 2 个去重阈值（论文最有发言权、风险最低），是否采纳？衰减半衰期等"价值观参数"是否纳入？给出你的裁定。
3. **持久化方式**：DB overrides 表（可审计可回滚、不碰 .env）vs 直接改写 .env（简单难回滚）。给出你的裁定。

# 输出要求

输出一份**可直接交给执行者（DeepSeek-V4-Flash）逐条实施**的中文完整方案，必须包含以下章节，每个设计决策附简短理由：

1. **方案总览**：一页纸，含数据流描述（从论文摄入到参数生效/回滚的完整链路）
2. **参数注册表设计**：完整参数条目（参数名 / 语义 / 当前值 / 合法范围 / 步长 / 分组 / 是否可调），含明确排除清单
3. **数据模型**：`ParamSuggestion` 与 `ParamOverride`（或你认为更优的命名）表字段设计，SQLModel 风格，含状态机定义
4. **API 契约**：端点路径、方法、请求/响应 JSON 示例（列表 / 审阅批准拒绝 / 回滚）
5. **LLM 提示词**：`PARAM_ADVICE_SYS` 全文（含给 LLM 的参数注册表上下文如何拼接、JSON 输出 Schema 定义）
6. **文件改动清单**：新增 / 修改 / 删除的文件，逐个说明职责与关键函数签名
7. **实施顺序**：分步骤，每步可独立验证（先表后逻辑、先核心后 API、测试随步补齐）
8. **测试计划**：冒烟测试用例清单（不 mock，直接调核心函数）
9. **风险与对策**：至少覆盖 LLM 幻觉参数值、并发写覆盖、参数越界、回滚竞态
10. **决策结论汇总**：三个决策点的最终裁定 + 理由

# 约束

- **不要向我提问**。信息不足处做合理假设，并在方案中显式标注【假设】。
- 输出使用中文；代码、字段名、标识符使用英文。
- 若你的设计与现有架构存在冲突，必须显式说明冲突点与取舍理由，不要默默偏离。
- 方案要具体到可直接照做，避免"建议考虑""可引入"这类模糊措辞。
```

---

## 使用流程

1. 复制上方【提示词正文】整段 → 发给 GPT-5.6 Sol
2. GPT 输出的完整方案 → 原样复制回来发给小弟（DeepSeek-V4-Flash）
3. 小弟按方案逐条实施，测试全绿后交付

## 备注

- 提示词已内置全部已核实的代码事实，GPT 无需访问仓库即可产出精准方案
- 三个决策点（触发粒度 / 白名单范围 / 持久化）由 GPT 裁定，若你对裁定有异议，可在回传方案时附带你的修改意见
