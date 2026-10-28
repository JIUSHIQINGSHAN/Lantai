# ADR-0057: 完璧——多实体事实抽取粒度原子性（SQLite 表达，无图数据库）

**日期**: 2026-10-26（2026-10-28 落地存储层实现）
**状态**: Accepted（已采纳，存储层与迁移链已合入 v31）
**决策者**: 维护者裁决与自动流水线落地
**来源**: 票据 `.scratch/v0.24-next-window-2026-10/issues/06-p1-6-multi-entity-fact-adr.md`（§六 P1 第 6 条，§四.3 Zep 超边 verified 级外部印证）；Zep 论文 arXiv:2501.13956 与 Graphiti 开源仓库（见 `docs/research/direction-research-report.md` 引 [5][6]）

---

## 背景

Zep/Graphiti 在 2026 年头部厂商实践中公开认定一个痛点（verified 级）：**一条涉及三个及以上实体的完整事实，若在抽取时按实体拆散成若干二元关系，检索时将丢失关系结构**。Zep 的应对是超边（Hyperedges）：把「多实体 + 关系谓词」整体保持为一个原子知识单元入图，而不是炸开成 N 条二元边。

兰台现状（`lantai/llm/prompts.py:1-11` `EXTRACT_SYS`、`lantai/parsing/extractor.py`）：

- 抽取产物是 `claims / methods / constraints / actions` 平铺列表，粒度完全交给 LLM 自由裁量——没有「多实体事实不拆散」的约束；
- `MemoryEdge`（`lantai/models/tables.py:276`）是 **memory↔memory** 边（supports/contradicts/refines/supersedes/links），没有实体节点层；ADR-0019 已注明「实体级链接依赖未来实体提取赛道」；
- 一旦未来上实体抽取，最自然的实现（每个实体生成一条残片记忆，如「A 创办 D」「B 创办 D」「C 创办 D」）恰好会复刻 Zep 认定的痛点。

本 ADR 在实体抽取赛道动工之前，先钉死**抽取粒度纪律与存储层原子性表达**。

## 决策

引入**「完璧」（Wanbi，多实体事实保整）**机制：涉及多实体的事实（三人共同创办一家公司、A 把项目 X 移交给 B、四方签署某协议）在抽取、存储、去重、演化全链路保持为**一条原子事实记录**；实体只作为该记录上的可索引投影，不是独立的知识单元。

### 1. SQLite 表达方案（推荐方案）

事实主表一条记录承载完整事实，实体以关联表做可查询投影；投影可随时从主表重建，**主表是唯一权威**。

```sql
-- 事实原子表：一条多实体事实 = 一行
CREATE TABLE fact_atomic (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT, user_id TEXT, agent_id TEXT,   -- 归属，同 MemoryEdge 惯例
    statement     TEXT NOT NULL,       -- 完整事实句（如「A、B、C 三人共同创办 D」）
    predicate     TEXT NOT NULL,       -- 谓词（cofound / handover / sign …）
    object        TEXT,                -- 若关系有中心宾语（如移交的「项目 X」）
    confidence    REAL DEFAULT 0.5,
    valid_from    TEXT, valid_to TEXT, -- 承更漏（ADR-0048）事件轴，迟到更正可锚定
    source_memory_id TEXT,             -- 血统（provenance，ADR-0015）：从哪条记忆抽出
    created_at    TEXT
);

-- 实体投影表：只做检索索引，不是知识单元；可从 fact_atomic 重建
CREATE TABLE fact_entity (
    fact_id       TEXT NOT NULL REFERENCES fact_atomic(id) ON DELETE CASCADE,
    entity_name   TEXT NOT NULL,       -- 归一化实体名
    role          TEXT NOT NULL,       -- subject / object / witness 等角色
    PRIMARY KEY (fact_id, entity_name, role)
);
CREATE INDEX idx_fact_entity_name ON fact_entity(entity_name);

-- 全文检索：FTS5 虚表挂在 statement 上，多实体事实整句入索引
CREATE VIRTUAL TABLE fact_fts USING fts5(statement, fact_id UNINDEXED);
```

**为什么不炸成实体-实体边表**：SQLite 上表达 N 元关系若用二元边表（`entity_edge(source, target, relation)`），一条三元事实要么重复存谓词（A→D, B→D, C→D 三行共享一个 `fact_group_id`），要么退化成「组标识 + 拆散行」——两者都让「整条事实」在存储层不再是一行，原子性靠约定维持而非 schema 保证。上表以**主表一行 = 一事实**从 schema 层杜绝拆散；`fact_entity` 只是倒排索引，删了可重建，不承载语义。

**检索路径**：BM25 走 `fact_fts`（整句命中，天然保原子）；实体过滤/扩展走 `fact_entity` 反查 `fact_id` 集合（等价于把「按实体找边」降为一次索引查询）；与贯珠（ADR-0035）的衔接 = 以 fact 为节点、共享实体为隐式边的图扩展。

**备选方案（记录不采用）**：仅在 `fact_atomic` 上加 `entities JSON` 列——实现最简，但实体过滤需 `json_each` 全扫或另建表达式索引，且「投影可重建、非权威」的边界感弱，容易把 JSON 列当知识本体用。

### 2. 抽取管线衔接点（拆散发生在哪一步）

| 步骤 | 现状 | 完璧约束 |
|---|---|---|
| `EXTRACT_SYS` 提示词（`prompts.py:1`） | `claims` 列表粒度自由 | 增补指令：**涉及 ≥3 实体且共享一个谓词的事实，输出为单条 claim（保留全部实体名），禁止拆成逐实体子句** |
| `extract_candidate`（`extractor.py:6`） | 返回平铺 dict | 多实体 claim 附 `entities`+`predicate` 结构（LLM 输出或规则解析）；解析失败按「宁 miss 不脏写」整条进锦囊（pending_review），不自动猜实体 |
| 候选过闸（gate/校雠） | 以记忆文本为单元 | 以 `statement` 整句为单元（见 §4） |
| 落库 | 进 MemoryItem | 多实体事实另落 `fact_atomic`；`source_memory_id` 挂血统 |

### 3. 反例：拆散后检索/演化丢信息的具体场景

**场景（三元共同创办）**：输入「张三、李四、王五于 2025 年共同创办了兰图科技」。

- **按单实体拆散存储**为三条残片：「张三创办兰图科技」「李四创办兰图科技」「王五创办兰图科技」。
- **丢失的查询**：用户问「李四和张三有什么共同经历？」——两条残片各自只含一个实体名与「兰图科技」。余弦/BM25 对「共同」「一起」无锚点可命中；即便靠「兰图科技」桥接，**「共同创办」这一关系语义（三人同日同股比？还是先后加入？）在残片里已不存在**，Agent 只能猜。这正是 Zep 超边要保住的「关系结构本身」。
- **演化侧丢信息**：若王五后来退出，更正「王五于 2026 年退出兰图科技」——校雠 update 只能命中「王五创办兰图科技」这条残片并改写它；张三、李四两条残片不受影响，库内出现「部分新部分旧」的割裂视图，而完整事实「现股东为张三、李四」从未被任何一条记录表达过。

**不值得保整的场景（对照，拆散无损失——这些不是完璧的适用对象）**：

- 单实体事实（「用户的手机号是 139…」）——本就无多元结构；
- 同一句里语义独立的并列事实（「张三创办了兰图科技；李四喜欢咖啡」）——两个分句各说各事，拆成两条是正确粒度，强行合并反而污染校雠的锚点判别；
- 闲聊寒暄（沙汰/ADR-0026 已在入队前滤除）。

判据一句话：**共享同一谓词、缺一实体则事实不完整或关系语义改变 → 保整；否则按语义独立拆**。

### 4. 与校雠三态去重的交互（ADR-0019/0023）

- **去重单元 = 整句 statement**：预筛余弦与结构判别（锚点/值归一）都作用于完整事实句。`fact_entity` 投影**不参与**去重——两条事实共享实体不构成重复（「张三创办 X」与「张三投资 X」实体集合相同、事实不同），否则会误 merge 掉真实差异。
- **update 整条生效**：值变更（股比变化、退出）命中时对 `fact_atomic` 整行出 update 提案，`fact_entity` 投影随新行重建；**禁止投影层局部更新**（同事实部分新部分旧，即 §3 场景二）。
- **merge 保守性放大**：ADR-0019 已知限制「old ⊆ new 判 merge」在多实体事实上风险更大（「三人创办」vs「两人创办+一人退出」锚点高度重合、实体集合不同）。规则层建议：`fact_entity` 实体集合不一致时**禁判 merge**，降级走 LLM judge，judge 缺席按 insert（宁 miss 不脏写）——实体集合作为结构判别的新增信号，是本 ADR 对校雠的唯一算法层增量。
- **残片历史**：若历史上已有拆散残片，不强制回溯合并（承 ADR-0013「既有不追溯」）；仅在残片被再次命中 update 时提示维护者可选合并。

### 5. 迁移影响面

- **纯增量，零改名**：新增 `fact_atomic` / `fact_entity` / `fact_fts` 三表，不动 `MemoryItem` / `MemoryEdge` / 既有 FTS（满足 ADR-0001 门面铁律与 R8；不违反「不做清单」首条——不引入图数据库，SQLite 单文件内闭环）。
- FTS 同步走既有「索引与正文对齐」纪律（司门版复原自查同款：删表级联 + 写后重建索引）；幂等写入承勘合（ADR idempotency 键）。
- 抽取提示词经 prompt_service 版本化（ADR-0043 动态提示词），变更可灰度、可回滚到旧 prompt。
- 检索面（hybrid/贯珠/graph_expand）本期不动——ADR 只钉存储与抽取粒度，检索接线是后续票据。
- 命名登记：`完璧` 已按 ADR-0013 R4 登记 `CONTEXT.md` 词汇表（见下「相关」）。

### 6. 命名

**完璧（Wanbi，多实体事实保整）**：出自《史记·廉颇蔺相如列传》「完璧归赵」——璧不受丝毫损伤、完整交付，喻多实体事实从抽取到存储不被拆散、以完整单元入档。L2 机制层，典籍意象，2 字，无既有冲突（R1–R6 自检通过）；实体投影表不另起中文名（纯技术索引，非知识概念）。

## 理由

- Zep 超边是 verified 级外部印证，但**方案有效性未在兰台验证**——故本 ADR 止于草案与 schema 设计，不带代码、不接检索，符合票据非范围；
- SQLite 单表 + 投影索引的表达，把「原子性」从编码约定提升为 schema 保证，且不违反「不引入图数据库」不做清单；
- 校雠侧唯一增量（实体集合不一致禁 merge）是对既有「merge 吞新值」风险的收窄，方向与 ADR-0019 的宁 miss 不脏写一致。

## 影响

- 通过后：实体抽取赛道必须以 `fact_atomic` 为落点，禁止逐实体残片记忆；`EXTRACT_SYS` 增补粒度指令；校雠 relation 判别加实体集合信号（各一张不 mock 冒烟测试，承测试纪律）。
- 未通过/搁置：无代码影响（纯文档票），实体赛道动工时须先回来裁决本 ADR，避免拆散性 schema 先落地。

## 相关

- [ADR-0013](0013-naming-system.md) — 命名纪律；完璧登记 `CONTEXT.md`
- [ADR-0019](0019-dedup-structural-relation.md) / [ADR-0023](0023-dedup-extension-signal.md) — 校雠三态去重
- [ADR-0035](0035-graph-association-retriever.md) — 贯珠（未来以 fact 为节点的扩展检索）
- [ADR-0048](0048-genglou-bitemporal-event-time.md) — 更漏双时间轴（valid_from/valid_to 承接）
- [CONTEXT.md](../../CONTEXT.md) — 完璧词汇登记
