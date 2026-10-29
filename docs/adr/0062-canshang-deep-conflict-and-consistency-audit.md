# ADR-0062: 参商（Canshang）深度矛盾冲突检测与多跳信念一致性仲裁

## 状态
已接受 (Accepted)

## 背景
1. **现有冲突层之局限**：
   在 v0.10 / ADR-0010 中，兰台确立了基础冲突账本与 6 维评分裁决体系（`ConflictEngine`，包含 `evidence_strength`、`confidence`、`provenance`、`recency`、`independence`、`contextual_fit`）。
   然而，既有的冲突消解主要依赖于**两两成对输入时的局部判定**（Pairwise Comparison）。
2. **多跳逻辑与信念拓扑痛点**：
   在复杂长期认知演进中，Agent 常常遇到以下高级矛盾：
   - **多跳因果隐性冲突（Multi-hop Causal Contradiction）**：
     事实 A 本身并不直接与事实 B 文本相左，但事实 A 沿因果边（MemoryEdge: `causes` / `leads_to`）衍生出的次生必然结论，与事实 B 的前置主张直接相悖；
   - **全域一致性审计盲区（Consistency Audit Blindspot）**：
     缺乏面向特定实体（Entity）、场景（Scene）或全域活跃事实的主动一致性审计矩阵，无法批量识别潜伏的未决矛盾；
   - **多方深度仲裁直断缺失（Deep Multi-way Arbitration）**：
     对于多方互斥主张，缺乏结合更漏双时间轴（事件轴发生时间与主张有效区间）、反事实依赖判定与证据链的深层仲裁器。
3. **架构与命名规范承接**：
   - 遵循 ADR-0013 传统意象与典籍命名规范（参商 · 《左传·昭公元年》「虽相背而不同出，如参商之不可见也」）；
   - 遵循 ADR-0001 门面铁律：对既有 `ConflictEngine`、`routes_conflicts.py`、MCP `conflict_resolve` 等保持向下兼容；
   - 遵循「宁 miss 不脏写」与租户行级隔离纪律；
   - 樊篱（Fanli）数据围栏防护：所有参商冲突审计输出强制经 `wrap_as_data` 隔离。

## 决策
1. **构建 `CanshangEngine` 深度冲突认知引擎 (`lantai/cognition/canshang.py`)**：
   - **两两及时间轴互斥判定 (`detect_pairwise_contradiction`)**：
     检测文本主张矛盾、实体属性互斥及事件轴发生时态冲突；
   - **全域/实体信念一致性扫描 (`audit_consistency(memory_ids, entity_name, limit) -> ConsistencyAuditReport`)**：
     结合因果拓扑与语义结构，遍历候选事实集，发现两两及多跳矛盾（`multihop_causal`）；
   - **深层仲裁直断 (`arbitrate_deep_conflict(conflicting_ids) -> DeepArbitrationResult`)**：
     结合更漏事件轴、反事实必要性与证据强度，计算主导记忆（`dominant_memory_id`）与从属记忆，输出可解释决策证据链。
2. **集成与穿透**：
   - 在 `lantai/services/conflict_service.py` 暴露服务函数；
   - 在 `lantai/api/routes_conflicts.py` 暴露 REST 端点：`POST /conflicts/scan-consistency` 与 `POST /conflicts/arbitrate-deep`；
   - 在 `lantai/cli/mcp.py` 注册 MCP 工具：`conflict_scan_consistency` 与 `conflict_deep_arbitrate`（工具池由 72 增至 74）。
3. **测试纪律严格贯彻**：
   - 先行编写真实 in-memory SQLite 不 mock 冒烟测试套件（`test_canshang_engine_smoke.py`、`test_canshang_api_smoke.py`）。

## 影响
- 使兰台认知系统具备跨拓扑多跳推演与主动一致性审计能力，杜绝自相矛盾的认知污染；
- 平滑向下兼容既有两两冲突机制。
