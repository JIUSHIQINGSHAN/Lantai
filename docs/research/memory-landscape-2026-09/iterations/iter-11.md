# Iteration 11 — 主题综合 II：时间感知与冲突消解（2026-09-19）

## 类型
synthesis（主题 2/4）

## 证据汇总
- **Zep/Graphiti（5.0）**：bi-temporal 为显式设计（`valid_at`/`invalid_at`），facts 自动失效而非删除；查询 `invalid_at IS NULL` 取当前视图。是全部 20 系统中唯一把「事实有效期」做成一等公民的。
- **Mem0（3.0）**：Temporal Reasoning 主打 time-aware retrieval 排序时间版本；但图按共现连边、无类型边，时间语义浅。
- **Dreaming V3（2.5）**：把「随时间保持更新」列为核心评测目标（将去→已去），平台级印证时效需求。
- **兰台（3.0，R7 核验）**：仅入库时间 `created_at`；supersedes 边在 MemoryEdge；冲突账本+直断理由+回声抑制时间窗（ADR-0046）存在；**事件时间/有效时间缺失**。
- 论文侧：Governed Shared Memory 把 temporal supersession 列为治理四原语之一；Zep 论文（2501.13956）双时间轴+LLM 识别冲突。

## 缺口分解（兰台 3.0 → 目标）
1. 数据模型：MemoryItem 增 `event_time`（事件发生时间，可空+精度标记）与 `effective_until`/invalid 语义（优先复用 supersedes+知命「被取代」态，不引入图库）。
2. 写入：gate 提取时抓事件时间（「昨天说我用 Postgres」≠今天的事实），失败进待审而非猜。
3. 检索：时间窗过滤+当前/历史双视图；迟到更正由冲突层裁决（保留直断理由），不按最近写入机械取胜。
4. 评测：E2 时间用例（当前/历史证据选择正确率 ≥90%，2026-09-18 调研§五阈值沿用）。

## 决策
- D20：时间主线维持 P1 但**升级为 P1 首位**（治理四原语中唯一数据级缺失）；实施沿用 2026-09-18 主线 C 的「先窗口过滤与双视图、不全图化」边界。
- D21：明确不做：图数据库集群、全量 Graphiti 式抽取（运维成本 ops_cost 2.0 是其负资产）。
