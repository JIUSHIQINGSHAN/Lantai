# 反思模块设计方案（Reflection Module Spec）

> 依据：docs/plans/reflection-module-prompt.md（v1）执行产出。
> 日期：2026-08-11。事实来源：项目代码（lantai/ 各模块）+ 调研报告（docs/research/memory-reflection-borrow.md）+ 7 篇论文精读笔记（docs/research/papers/notes/）。
> 状态：设计草案，待用户评审。未实现任何代码。

---

## 1. 背景与目标

兰台记忆链路「摄取→闸门→演化→遗忘→检索」中，**演化**目前只处理「候选→提案」，没有「记忆自我审视」环节。三个事实缺口：
1. 路线图欠 autodream 蒸馏，从未落地；
2. 早期实测（2026-08-11 遗忘质量报告，已随生成报告归档策略移出 git）`superseded_residual_rate = 0.5`——被取代的旧记忆仍参与检索；修复见 `9cda3dd`（supersedes 排序降权），当前评测集（chinese-memory-v2，80 case）该指标为诚实测量（降权不删旧值）；
3. `lantai/evolution/reflector.py` 只有使用反馈打分（record_feedback），无反思蒸馏。

目标：新增**反思/蒸馏模块**——周期性审视既有记忆，发现被取代、过期、冲突、低帮助、可升华的模式，产出 add/update/merge/deprecate 提案，走既有提案链路（自动应用或进锦囊待审），并重测健康指标自证效果。哲学不变：「宁 miss 不脏写」。

## 2. 方案对比与推荐

| 维度 | A. 周期蒸馏 worker | B. 蒸馏 + 健康审计闭环（推荐） | C. B + 失败反思闭环 |
|---|---|---|---|
| 触发策略 | 固定周期全量扫描 | 健康扫描发现问题才蒸馏 + 周期兜底 + 重要性水位 | B + /feedback 负反馈事件 |
| 输入选择 | 近窗口全量记忆 | 问题驱动候选（superseded/过期/冲突/低帮助） | B + 误导过 Agent 的记忆 |
| 加工过程 | 单段 LLM 蒸馏 | 两段：curator 提案 + rejecter 复核（Cognee 借鉴） | B + 纠错蒸馏（Reflexion 借鉴） |
| 输出形态 | MemoryProposal | MemoryProposal + 健康分前后自证 | B + 纠错提案 |
| 审查与安全 | 既有提案审批 | 既有 + rejecter 防幻觉（防 memory hacking） | 既有 + 失败上限 |
| 评估自证 | 无 | 健康快照 before/after + 复用 forgetting_quality | B + 负反馈率下降 |
| 实现成本 | 低 | 中（核心改动在 apply_proposal 的 deprecate/merge） | 中高 |
| 对缺口的直接效果 | 弱 | 强（superseded_residual_rate 直接受益） | 中（用户体验） |

**推荐 B 为 v1**：最贴合现有缺口（superseded 残留）、复用最多既有组件（proposal/conflict_event/checkpoint/pending_review）、成本可控。A 的「固定周期」降级为 B 的兜底触发器；C 留 v2（依赖 /feedback 数据量，先让 feedback 跑起来积累）。

## 3. 推荐方案详细设计（v1 = 方案 B）

### 3.1 触发策略

三层触发，全部落在新增 worker `lantai/workers/reflect_worker.py`（scheduler 注册 cron job，默认每日一次，`REFLECT_CRON_HOUR=22` UTC = 本地早 6 点，与 digest 错开 1 分钟）：

1. **健康候选触发（主）**：`health_scan()` 用纯 SQL 扫描 5 条规则（见 3.2），候选列表非空 → 进入蒸馏。
2. **重要性水位触发（保底）**：自上次 reflect 后新增 MemoryItem 的 `importance` 累加值 ≥ `REFLECT_IMPORTANCE_POOL`（默认 10.0）→ 即使健康候选为空也做一次「新记忆主题蒸馏」（提炼重复模式 → add/merge 提案）。借鉴 Generative Agents 的 importance_trigger（其 150 是 1-10 poignancy 尺度；兰台 importance ∈ [0,1]，10.0 相当于 10 条满重要性记忆，初始值待 dry-run 校准）。
3. **周期兜底**：每日 cron 必跑一次扫描（扫描是廉价 SQL）；候选为空且水位不足 → 只 `record_run("reflect")` 退出，**不调 LLM**（成本防护）。

配置（`lantai/core/settings.py`，全部可选带默认）：
- `REFLECT_ENABLED: bool = False`（默认关，与 COALESCE 同风格，显式开启）
- `REFLECT_CRON_HOUR: int = 22`（UTC）
- `REFLECT_MAX_BATCH: int = 20`（单次蒸馏候选上限，控 LLM 成本）
- `REFLECT_IMPORTANCE_POOL: float = 10.0`（待校准）
- `REFLECT_AUTO_APPLY_CONF: float = 0.7`（与 evolve_worker 自动应用阈值一致）
- `REFLECT_MIN_USE_COUNT: int = 3` / `REFLECT_LOW_HELPFUL_RATIO: float = 0.3`（R4 规则）
- `REFLECT_STALE_AGE_DAYS: int = 30` / `REFLECT_STALE_IMPORTANCE: float = 0.4`（R5 规则）
- `REFLECT_STALE_SCAN_ENABLED: bool = False`（R4/R5 默认关，保守起步）
- `REFLECT_MIN_CONFIDENCE: float = 0.5`（低于此置信的提案不落库）

### 3.2 输入选择（健康扫描规则）

`health_scan(session) -> ReflectHealthSnapshot`，纯 SQL + 零 LLM，可单测。规则（R1-R3 默认开，R4-R5 开关控制）：

| 规则 | 判定（SQL 语义） | 候选动作 |
|---|---|---|
| R1 superseded 残留 | `MemoryEdge.relation == "supersedes"` 且 `target_memory.status == "active"` | deprecate 提案（证据 = supersedes 源记忆） |
| R2 过期时间窗 | `MemoryItem.valid_to < now` 且 `status == "active"` 且 `decay_class != "procedural"` | deprecate 提案（LLM 复核是否真过期，Zep 四时间戳精神：看事实时间而非写入时间） |
| R3 open 冲突账本 | `ConflictEvent.status == "open"` | 纳入批次由 curator 裁决 → 对应 update/deprecate 提案 + 账本 resolved/dismissed |
| R4 低帮助率（默认关） | `use_count >= REFLECT_MIN_USE_COUNT` 且 `helpful_count/use_count <= REFLECT_LOW_HELPFUL_RATIO` | update/deprecate 提案 |
| R5 低价值陈旧（默认关） | `age >= REFLECT_STALE_AGE_DAYS` 且 `use_count == 0` 且 `importance < REFLECT_STALE_IMPORTANCE` 且非 procedural | deprecate 提案 |

输出健康快照：`{superseded_active, expired_active, open_conflicts, low_helpful, stale_low_value, batch_total}`（batch_total 为去重后批次大小，受 `REFLECT_MAX_BATCH` 截断）。快照在蒸馏前后各取一次，差值作为自证指标（3.6）。

### 3.3 加工过程（两段 LLM 蒸馏）

全部走 `lantai/llm/client.py::chat_json`（strict JSON + 异常降级不阻断）。两个新 prompt 加入 `lantai/llm/prompts.py`：

**阶段 1 — curator 提案**（`REFLECT_CURATOR_SYS`）：
- 输入：批次候选（每项 `{memory_id, key, content, lane, importance, signal}`，信号如 `superseded_by=<id>` / `expired` / `open_conflict`）+ 相关既有记忆摘要（复用 proposer 的取数方式，取 active 前 20 条）。
- 输出 strict JSON：`{"proposals": [{"proposal_type": "add|update|merge|deprecate", "target_memory_id": "", "new_content": "", "memory_type": "semantic|procedural", "reason": "", "confidence": 0.0-1.0, "evidence_ids": ["memory_id", ...]}]}`
- 铁律（写进 prompt）：无证据不提案；不臆造内容；每条提案必须给 evidence_ids；proposal_type 非法则整条作废。

**阶段 2 — rejecter 复核**（`REFLECT_REJECTER_SYS`，借鉴 Cognee curator/rejecter 双角色）：
- 输入：提案 + 证据原文（evidence_ids 对应 content 逐条贴出）。
- 输出 strict JSON：`{"accept": bool, "risk": "low|medium|high", "reason": ""}`
- 裁决：`accept=false` 或 `risk=high` → 丢弃（宁 miss）；`risk=medium` → 强制 pending；`accept=true 且 risk=low` → 进自动应用判定。

**证据校验（代码层，不靠 LLM）**：evidence_ids 必须是批次内真实存在的 memory_id，任一非法 → 该提案作废。对应 Generative Agents 的 `insight (because of 1, 5, 3)` 证据指针思想，落为 `MemoryProposal.evidence_ids`（表已存在，零迁移）。

### 3.4 输出形态（proposal 映射）

新增函数 `propose_from_reflection(session, batch, curated) -> list[MemoryProposal]`，直接建 `MemoryProposal`（不经过 candidate，因为反思对象是既有记忆而非候选）：
- `proposal_type ∈ {add, update, merge, deprecate}`（`lantai/models/enums.py::ProposalType` 已有）
- `evidence_ids = 证据指针数组`；`conflict_ids = []`
- `confidence = curator 值 × rejecter 接受度`；`decided_by = "auto"`（与 evolve 一致）
- `proposed_patch = {memory_type, key, content, lane}`（沿用 `proposer.py` 的 patch 结构）

**自动应用 vs 待审**（与 `evolve_worker.py` 同规则）：
- `confidence >= REFLECT_AUTO_APPLY_CONF(0.7) 且 risk == low 且无冲突` → `apply_proposal` 立即应用（含 checkpoint）
- 否则 `status = pending` → 用户经既有 `GET /proposals?status=pending` + `POST /proposals/{id}/decide` 裁决（`lantai/api/routes_evolution.py` 已存在，零新增）

**依赖改动（本方案最大代码点）**：`lantai/evolution/promoter.py::apply_proposal` 目前只实现 add/update（`if proposal_type == "add" or not existing: add 分支；else: update 分支`），**merge/deprecate 会错误落入 update 分支**。v1 必须新增两个分支（门面铁律：不动既有 add/update 语义，只加分支）：
- `deprecate`：目标记忆 `valid_to = now`（时间对齐截断，借鉴 Zep）+ 写 `MemoryEdge(relation="supersedes", source=新记忆, target=旧记忆)` + `status = "archived"`（立即退出检索，`WHERE status='active'` 生效）+ checkpoint。旧记忆物理不删，历史可回滚。
- `merge`：目标记忆合并入主记忆（内容合并、`evidence_ids` 并集、`version += 1`、tags 并集）+ 双方 checkpoint + 被合并记忆写 supersedes 边并 archived。

### 3.5 审查与安全

- 所有写入经 `apply_proposal` → 自动 checkpoint → 可 `POST /memory/{id}/rollback`（既有）。
- rejecter 挡住幻觉蒸馏与「记忆投毒」（Generative Agents 承认的 memory hacking；Reflexion 的 MBPP 假阳性污染反例）。
- R3 冲突账本闭环：提案应用后，对应 `ConflictEvent` 标记 `resolved`（确认冲突成立）或 `dismissed`（误报），走既有 `conflict_service.resolve_conflict_event`。
- LLM 失败（chat_json 异常）→ 本轮跳过该批，不落任何提案；连续失败由下一轮 cron 重试。
- 预算防护：`REFLECT_MAX_BATCH=20` + 每日最多一轮 + 候选空/水位不足不调 LLM。

### 3.6 评估自证

- `run_reflect_once()` 返回 `{health_before, health_after, proposals_created, auto_applied, pending, discarded}`；`scheduler.record_run("reflect")` 存最近一次摘要，经 `/stats` 暴露。
- Daily Digest（`digest_worker.py`）报告追加一行「反思：N 提案 / M 自动应用 / K 待审」（改动 3 行）。
- 手动自测复用 `lantai/eval/forgetting_quality.py`：对 chinese-memory-v1 用例集重跑，对比 `superseded_residual_rate / superseded_order_accuracy` 是否改善——与现有 memory-quality 报告机制闭环。

### 3.7 实现清单

| 类型 | 文件 | 内容 |
|---|---|---|
| 新增 | `lantai/evolution/reflector.py` | 保留 `record_feedback`；新增 `health_scan` / `run_reflect_once` / `propose_from_reflection` / `_curate` / `_reject` |
| 新增 | `lantai/workers/reflect_worker.py` | `run_reflect_once` 包装 + `record_run("reflect")` |
| 修改 | `lantai/evolution/promoter.py` | `apply_proposal` 新增 deprecate / merge 分支 |
| 修改 | `lantai/llm/prompts.py` | `REFLECT_CURATOR_SYS` / `REFLECT_REJECTER_SYS` |
| 修改 | `lantai/core/settings.py` | `REFLECT_*` 配置块 |
| 修改 | `lantai/core/scheduler.py` | 注册 reflect job（cron hour） |
| 修改 | `lantai/workers/digest_worker.py` | 报告追加反思统计行 |
| 新增 | `tests/test_reflect.py` | 见第 4 节 |

## 4. 测试计划（测试纪律：核心函数必须有不 mock 内部逻辑的冒烟测试）

全部沿用 `tests/test_digest.py` 模式：内存 SQLite 真实建表 + `monkeypatch.setattr(db_module, "get_session", session_factory)`；**仅允许 mock** `chat_json`（外部 LLM 网络）与 embed。逐函数：

| 被测函数 | 测试名 | 最小输入 | 断言 | mock |
|---|---|---|---|---|
| `health_scan` | `test_health_scan_superseded_residual` | 真实 DB：supersedes 边 + active 目标 | R1 命中，`superseded_active==1` | 无 |
| `health_scan` | `test_health_scan_expired_window` | valid_to 过去 + active | R2 命中 | 无 |
| `health_scan` | `test_health_scan_open_conflicts` | ConflictEvent open | R3 命中 | 无 |
| `health_scan` | `test_health_scan_batch_cap` | 30 条候选 | batch_total==REFLECT_MAX_BATCH | 无 |
| `propose_from_reflection` | `test_propose_valid_json` | mock chat_json 返回合法 proposals | MemoryProposal 落库、类型/证据正确 | chat_json |
| `propose_from_reflection` | `test_propose_rejects_bad_evidence` | evidence_ids 含不存在 id | 该提案作废不落库 | chat_json |
| `run_reflect_once` | `test_auto_apply_high_confidence` | 高置信 low risk 提案 | MemoryItem 变化 + checkpoint 生成 + record_run | chat_json |
| `run_reflect_once` | `test_pending_low_confidence` | 低置信/medium risk | status=pending，`GET /proposals` 可见 | chat_json |
| `run_reflect_once` | `test_no_llm_when_idle` | 空健康候选 + 水位不足 | chat_json 调用 0 次 + record_run | chat_json（计数） |
| `apply_proposal` | `test_deprecate_branch` | deprecate 提案 | valid_to=now + supersedes 边 + archived + checkpoint | 无（embed 可 mock） |
| `apply_proposal` | `test_merge_branch` | merge 提案 | 内容合并 + evidence 并集 + 旧记忆 archived + 双方 checkpoint | 无（embed 可 mock） |
| `apply_proposal` | `test_add_update_unchanged` | 既有 add/update 提案 | 语义与现状一致（防门面破坏回归） | 无（embed 可 mock） |

## 5. 明确不做（YAGNI）

- 不做 MCMA 式 copilot 训练/微调——v1 纯 prompt 蒸馏，训练留作远期。
- 不做 Generative Agents 式递归反思树——v1 单层蒸馏，每层可追溯由 evidence_ids + checkpoint 保证。
- 不做 Voyager 式 procedural 技能自动验证执行——需要环境执行器，超出记忆系统边界；rule lane 蒸馏仍走提案 + 人工/低置信待审。
- 不做对话级复盘（省对话 channel）——候选 v2，与 daily digest 场景重叠。
- 不建独立健康分存储表——健康快照即算即用，落 digest 报告即可。
- 不引入新向量/图存储——复用 SQLite + 现有 MemoryEdge。

## 6. 风险与未确认

- `REFLECT_IMPORTANCE_POOL=10.0`、`REFLECT_LOW_HELPFUL_RATIO=0.3`、`REFLECT_AUTO_APPLY_CONF=0.7` 为经验初值，需用现有 dry-run 管线校准（验证方法：对 chinese-memory-v1 用例集扫阈值网格）。
- `apply_proposal` 的 merge/deprecate 分支是本方案最大代码改动点；需防回归（测试计划第 12 行专门做门面回归）。
- 当前 supersedes 边数据可能稀疏，R1 上线初期可能空转——由 R2/R3 + 水位触发保底；同时可在 `edge_service` 层补「update 时自动写 supersedes 边」的小改进（另立 issue，不进本 spec）。
- 健康规则 R4/R5 误报风险未实测，默认关闭，仅 R1-R3 起步。
- LLM 蒸馏成本：批次 20 条 + 每日一轮 + 空闲零调用，实际成本需观察期统计（digest 反思行可积累数据）。

## 7. 相关文件索引

- 本 spec：`docs/plans/reflection-module-spec.md`；提示词：`docs/plans/reflection-module-prompt.md`
- 机制借鉴：`docs/research/memory-reflection-borrow.md`；论文笔记：`docs/research/papers/notes/01..07`
- 现有链路代码：`lantai/evolution/{proposer,promoter,reflector}.py`、`lantai/workers/evolve_worker.py`、`lantai/gate/decision.py`、`lantai/services/candidate_service.py`、`lantai/memory/forgetting.py`
- 与路线图取舍：autodream 条目由本 spec 承接（「7 天周期」改为「健康驱动 + 每日兜底 + 水位触发」）。
