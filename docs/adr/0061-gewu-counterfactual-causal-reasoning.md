# ADR-0061: 格物（Gewu）反事实推理与因果假设推演引擎

## 状态
已接受 (Accepted)

## 背景
1. **现有格物（v0.35）之局限**：
   在 v0.35.0「格物」中，系统构建了基础因果时序拓扑与事件溯源链（`CausalEngine`、`trace_decision_provenance`、`get_event_timeline`），突破了单点静态事实记忆的局限。
   然而，既有的因果推演主要局限于**事后回溯（Retrospective Provenance）**——即已知事件已经发生，逆向推导最顶层根因与事件时间线。
2. **高阶认知进阶痛点**：
   Agent 在制定决策、推演方案时，极度需要**前瞻推演与反事实推理（Counterfactual Reasoning / "What-If" Analysis）**能力：
   - *“如果当初节点 A 未发生，链路是否会被切断（根因必要性检验）？”*
   - *“如果假定事件 X 发生，会沿着因果拓扑链衍生哪些次生后果与潜在风险（因果影响面预判）？”*
   - *“在多条因果路径并存时，哪条路径是主导因果链，其置信度乘积与因果强度如何量化？”*
3. **架构与工程规范承接**：
   - 遵循 ADR-0013 传统意象与典籍命名规范（格物 · 《礼记·大学》「致知在格物，物格而后知至」；穷究因果机理与反事实假设）；
   - 遵循 ADR-0001 门面铁律：对既有 `CausalEngine`、`routes_edges.py`、MCP `causal_trace` 等公开接口保持严格向下兼容；
   - 遵循「宁 miss 不脏写」与租户行级隔离纪律；
   - 樊篱（Fanli）数据围栏防护：所有反事实与因果推演输出正文强制经 `wrap_as_data` 隔离。

## 决策
1. **扩展 `CausalEngine` 核心反事实与因果推演能力 (`lantai/cognition/causal.py`)**：
   - **反事实根因判定 (`evaluate_counterfactual(target_id: str, intervened_node_id: str) -> dict`)**：
     模拟如果 `intervened_node_id` 不存在/被阻断（Do-calculus 介入），目标 `target_id` 是否仍能由其他替代因果路径触发。计算反事实必要性（Necessity Score）与替代路径拓扑；
   - **因果影响面前瞻推演 (`project_causal_impact(node_id: str, max_depth: int = 5) -> dict`)**：
     沿 `causes` / `leads_to` 前向拓扑模拟下游波及的所有次生事件节点与累积风险置信度衰减（Causal Strength Propagation）；
   - **因果强路径量化 (`find_critical_causal_path(target_id: str) -> dict`)**：
     在 DAG 因果网络中基于置信度权重计算从根因到目标的最关键因果链（Critical Path）。
2. **认知上下文与服务层集成 (`lantai/cognition/context.py` & `lantai/services/provenance_service.py`)**：
   - 在 `CognitiveContext` 中增强对反事实与关键路径推演切面的支持；
   - 确保 `trace_decision_provenance` 补充结构化关键因果路径（`critical_path`）与因果强度评估；
3. **API 与 MCP 工具标准化扩充**：
   - REST 路由：`POST /edges/counterfactual` 与 `POST /edges/project-impact`（带 ACL 租户行级校验）；
   - MCP 工具：暴露 `causal_counterfactual` 与 `causal_project_impact` 工具，保持 MCP 1:1 双向守卫对齐；
4. **测试纪律严格贯彻**：
   - 必须通过真实 in-memory SQLite 构造不 mock 核心逻辑的冒烟测试套件。
5. **遍历设界（v0.68 票 39 增补）**：
   - 反事实逆向回溯与关键路径枚举均为全简单路径遍历（per-path visited 防环），
     n 层 k 叉菱形的路径数 ≈ k^n——`max_depth` 入口设界（REST pydantic ge/le、
     MCP -32602、引擎直调 ValueError 三面镜像，单一真源
     `COUNTERFACTUAL_MAX_DEPTH_LIMIT=10`），存活路径硬上限
     `COUNTERFACTUAL_PATH_CAP=64`（BFS 先到先停，`paths_truncated` 外显，
     partial 语义同校雠/拾遗 `scan_truncated`）；
   - 帽内结论语义不变；`find_critical_causal_path` 超帽时帽外路径不参与置信度
     择优（语义收窄如实入 docstring）；该函数产品代码零调用方（死代码留档，
     删除与否留待维护者裁决）；
   - 遍历层共享单一 DB 会话，会话开合与路径数×深度解耦。

## 影响
- 使兰台认知层由“被动时序回溯”升华至“主动反事实分析与决策影响前瞻”，为 Agent 决策安全与因果归因提供深层推演支撑；
- 保持既有系统零破坏与平滑演进；
- 合法形状请求不再能驱动指数级路径枚举与逐路径会话开合（票 39：与 N-02 拾遗全表扫描同族的资源放大面收口）。
