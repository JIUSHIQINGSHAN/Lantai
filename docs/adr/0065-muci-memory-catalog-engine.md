# ADR-0065: 目次（Muci）多维记忆知识目录与分级语义导航引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥 & Claude  
**来源**: `docs/adr/0013-naming-system.md` 候选意象转正 & `.scratch/v0.44-next-window-2026-11/`

---

## 背景

在兰台 AI Agent 长期记忆系统的生命周期演进中，Agent 积累了大量跨领域、跨任务的异构记忆（事实、偏好、规则、经验、技能与任务卷宗）。
然而，既有的记忆透出与感知机制存在明显的结构性缺陷：
1. **既有统计概览（`ops/overview.py`）过于粗粒度**：仅提供纯 SQL 行级计数（总数、active/archived 计数、lane 分布、decay_class 分布），不包含任何记忆的主题、实体、领域层级或语义内容，Agent 无法通过它获知「记忆库里到底沉淀了哪些领域的知识」；
2. **检索缺乏全局路标（Navigation Blindness）**：当 Agent 面对一个开放性或综合性问题时，往往盲目依赖关键词或向量相似度在深渊中碰撞，容易漏召回跨领域的关键背景；缺少一份轻量、层级分明、一眼可见的「记忆总目录」来指导精准下钻；
3. **多维知识目录（Memory Catalog）缺失**：缺乏面向多租户和用户身份安全隔离的动态分级聚合机制，无法在毫秒级内按辨域（domain）、泳道（lane）、核心实体/主题聚类与时序重要度输出紧凑结构化的目次树。

---

## 决策

### 1. 传统意象命名转正（ADR-0013）
- 正式中文名：**目次**（Muci）。
- 出处：南朝梁·刘勰《文心雕龙·序志》「成岁既渐，目次来构」；古籍编目首重目次，部目次序、条贯井然，使开卷者一览无遗。
- 英文代号：`MuciEngine` / `Catalog`。
- 登记入口：`CONTEXT.md` 词汇表与 `docs/adr/0013-naming-system.md`。

### 2. 核心目次分级聚合引擎（MuciEngine）
在 `lantai/cognition/muci.py` 建立 `MuciEngine`，提供纯只读、零写放大、行级租户隔离的分级目录构建能力：
1. **多维分级编目（Hierarchical Cataloging）**：
   - 顶层按 **辨域（domain: user / session / agent）** 与 **分轨（lane: fact / rule / preference / experience / general）** 构建知识板块；
   - 次级按 **主题聚类（Topic / Entity Clusters）** 汇总核心掌握的事实簇与规则集；
   - 提取各板块代表性记忆条目（Title + Summary 摘编，严格经过樊篱防护截断）；
2. **紧凑量化元数据（Catalog Metadata）**：
   - 统计各目录分支的活跃度、最新事件时刻（`latest_event_at`）与平均重要度权重；
   - 提供目录摘要（Outline）与深度展开（Drilldown）双模式，支持极低 Token 开销注入；
3. **租户与属主安全隔离（Tenant/ACL Guard）**：
   - 严格继承 `tenant_id` 与 `user_id` 行级过滤口径，不可见数据在目次生成全链路被彻底剪枝（宁 miss 不脏写）。

### 3. API、MCP 工具与认知上下文切面
1. **REST 路由**：
   - `GET /catalog/overview`：获取记忆全域目次树与分级聚合；
   - `GET /catalog/domains/{domain}`：获取指定领域的专项精细目次；
2. **MCP 工具扩展**：
   - 注册 `muci_catalog` 工具，参数支持 `domain`、`lane`、`detail_level`（compact / full），工具池扩充至 78 个并保持 1:1 双向对齐；
3. **认知上下文装配切面（CognitiveContext）**：
   - 在 `CognitiveContext` 引入 `catalog_digest` 切面，允许在任务开始时自适应注入全局记忆目次，引导 Agent 建立顶层全局知识视野。

---

## 影响

- Agent 具备全局记忆目录导航能力，彻底告别单条记忆检索盲人摸象的痛点；
- 统计概览（`overview.py`）演化为深度的认知层目录聚合服务，赋能 Studio 可视化导览与外部 Host 协议。
