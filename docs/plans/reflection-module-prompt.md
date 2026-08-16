# 兰台反思模块设计提示词

> 用途：让执行者（LLM agent）基于本项目代码与既有调研，产出「反思/蒸馏模块」的设计方案（spec），**不写实现代码**。
> 版本：v1 · 2026-08-11 · 由项目精读 + 外部调研 + 论文精读综合而成

---

## 一、背景与定位

兰台记忆（Lantai）是 AI Agent 长期记忆管理系统，链路：摄取 → 闸门 → 演化 → 遗忘 → 检索。
用户需求：「吾日三省吾身」——让系统定期回顾自己的记忆，发现重复、过时、矛盾、可升华的模式，并沉淀为更好的记忆。

现状缺口（必须回应的三个事实）：
1. 路线图欠「autodream 7 天周期记忆蒸馏」，从未落地。
2. 早期实测（2026-08-11 遗忘质量报告，已随生成报告归档策略移出 git）`superseded_residual_rate = 0.5`、`superseded_order_accuracy = 0.5`——被取代的旧记忆仍参与检索；修复见 `9cda3dd`（supersedes 排序降权）。
3. 现有 `lantai/evolution/reflector.py` 只是「使用反馈打分」（record_feedback），真正的反思蒸馏是空白。

## 二、精读材料索引（执行前必须通读）

### 代码（事实来源，以代码为准）
- 提案状态机与数据模型：`lantai/models/tables.py`（MemoryCandidate/MemoryItem/MemoryProposal/MemoryCheckpoint/MemoryEdge/ConflictEvent）、`lantai/models/enums.py`（ProposalType: add/update/merge/deprecate/rollback；GateDecision 五档）
- 演化链路：`lantai/evolution/proposer.py`（propose_from_candidate：LLM 产出 proposal JSON）、`lantai/evolution/promoter.py`（apply_proposal：add/update、checkpoint、索引同步）、`lantai/workers/evolve_worker.py`（自动应用规则：置信度 ≥0.7 且无冲突 → apply，否则待审）
- 闸门与冲突：`lantai/gate/decision.py`（decide：低置信 reject→锦囊、硬矛盾→archive_conflict、低新颖度→merge 路径）、`lantai/gate/conflict_rules.py`（确定性互斥规则→ConflictEvent 账本）、`lantai/gate/dedup.py`（merge/update/insert 三态）
- 待审与裁决：`lantai/services/candidate_service.py`（enqueue_rejected/list_pending_candidates/review_candidate/TTL 归档）、`lantai/api/routes_candidates.py`（GET /candidates/pending、POST /candidates/{id}/review）
- 遗忘与衰减：`lantai/memory/forgetting.py`（decay_score 指数衰减、working TTL、archived 不删）、`lantai/memory/decay_class.py`（procedural 永不衰减/semantic/episodic）
- 调度与报告：`lantai/core/scheduler.py`（add_job 模式 + record_run）、`lantai/workers/digest_worker.py`（每日盘点 + 五项统计）、`lantai/core/settings.py`（`*_CRON_*` 配置命名惯例）
- 反馈闭环：`lantai/evolution/reflector.py`（record_feedback：importance 增量、helpful_count、hallucination_risk）
- LLM 规范：`lantai/llm/prompts.py`（EXTRACT/CONTRADICTION/PROPOSAL 三个 SYS prompt 的 strict JSON 风格）

### 调研（机制借鉴来源）
- `docs/research/memory-reflection-borrow.md`（12 项目横向对比 + TOP 5 借鉴机制）
- `docs/research/papers/notes/*-reading.md`（7 篇论文精读笔记，每篇第 5 节=反思机制深挖、第 8 节=对兰台启示）

## 三、目标

产出一份「反思/蒸馏模块」设计方案（spec），必须：
1. 先做 3 个方案对比（见第四节），给出推荐与理由，再写推荐方案的详细设计。
2. 所有设计落到**现有组件名**上（MemoryProposal、pending_review、ConflictEvent、checkpoint、scheduler 等），不得发明不存在的表/服务而不说明迁移成本。
3. 明确「明确不做」清单（YAGNI），防范围蔓延。

## 四、方案对比要求（三选一推荐）

- 方案 A：周期蒸馏 worker——固定周期触发，全量/近窗口扫描 → LLM 蒸馏 → 提案化。（对应 autodream 原始设想）
- 方案 B：蒸馏 + 健康审计闭环——由「记忆健康扫描」（superseded 残留、open 冲突、低 use_count、过时 valid_to）驱动输入选择，周期只做兜底；蒸馏后重跑健康分自证。（借鉴 agentmem health/conflict/stale、Letta /doctor、Generative Agents 重要性水位）
- 方案 C：方案 B + 失败反思闭环——feedback 负反馈（helped=false / hallucination_risk 高）触发对误导记忆的纠错反思，产出 update/deprecate 提案。（借鉴 Reflexion 失败反思、MCMA 双通道）

对比维度：触发策略 / 输入选择 / 加工过程 / 输出形态 / 审查与安全 / 评估自证 / 实现成本。
推荐倾向：B 为 v1（最贴合 superseded 缺口、复用最多、成本可控），C 留 v2，A 的周期触发作为 B 的兜底触发器。允许推翻，但必须给理由。

## 五、铁律与约束（违反即失败）

1. **宁 miss 不脏写**：反思产出一律走 proposal 链路；低置信/有冲突进 pending_review 交用户裁决，禁止静默丢弃或自动改写。
2. **测试纪律**：设计里每个核心函数必须列出至少一个「不 mock 内部计算逻辑」的冒烟测试用例（真实构造最小输入直调函数；允许 mock 的只有：LLM/embedding 外部网络调用、文件系统副作用、DB session）。参考 `tests/test_digest.py` 的内存 SQLite 建表 + patch get_session 写法。
3. **门面铁律**：不破坏既有 API 与 import（参考 ADR-0001），新增模块不得改名既有函数。
4. **零新存储**：能复用 MemoryProposal/ConflictEvent/MemoryCheckpoint 就不建新表；如必须新增字段，说明理由与迁移。
5. **配置惯例**：新开关命名 `REFLECT_*`，周期 `REFLECT_CRON_*`，全部可选、有安全默认值。
6. **诚实标注**：任何「未确认」的假设（如阈值初始值）必须标注，不得编造。

## 六、spec 交付结构（必须完整）

```markdown
# 反思模块设计方案（Reflection Module Spec）
1. 背景与目标（3-5 句）
2. 方案对比表 + 推荐与理由
3. 推荐方案详细设计
   3.1 触发策略（水位/周期/事件，含阈值初值与来源标注）
   3.2 输入选择（健康扫描规则：每条规则的可执行判定、SQL/代码位置）
   3.3 加工过程（LLM prompt 结构：curator 产出 JSON schema、rejecter 复核条件、证据指针）
   3.4 输出形态（proposal 映射：proposal_type × 场景；evidence_ids；自动应用 vs 待审判定）
   3.5 审查与安全（复用 pending_review/conflict_event/checkpoint；防 memory hacking）
   3.6 评估自证（复用 forgetting_quality 自测；健康分前后对比）
   3.7 实现清单（新增/修改文件、配置项、scheduler job 注册、REST 入口）
4. 测试计划（逐函数：测试名 + 最小输入 + 断言；标注哪些 mock 什么）
5. 明确不做（YAGNI）
6. 风险与未确认
7. 相关文件索引
```

## 七、验收标准

- 每个机制点都能指回一个现有代码文件/函数或调研来源。
- 设计文档里出现的每个函数名都有对应冒烟测试用例名。
- 不出现「TBD/TODO」占位符；未确认项必须标注并给出验证方法。
- 明确写出 v1 与 v2 的边界（B 与 C 的拆分）。

## 八、执行方式

- 执行者可先读代码验证假设（用 rg/读文件），再写 spec；引用代码时写清路径。
- spec 保存路径：`docs/plans/reflection-module-spec.md`（若与既有 v0.5 计划冲突，在文末说明取舍）。
- 最终答复：spec 路径 + 推荐方案一句话 + 3 个最关键设计决策 + 未确认项清单。
