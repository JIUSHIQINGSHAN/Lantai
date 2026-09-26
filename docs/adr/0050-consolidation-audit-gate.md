# ADR-0050: 沉潜过审——巩固产物提案制与影子对照（consolidation audit gate）

**日期**: 2026-09-25
**状态**: Accepted
**决策者**: 大哥
**来源**: roadmap-v2 P1-3「沉潜/反思产物一律过审；巩固前后快照，巩固产物 100% 带提案记录」（`docs/research/memory-landscape-2026-09/roadmap-v2.md:25`）+ 实施票据 `.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`；学理依据 iter-08 TRUSTMEM（`iterations/iter-08.md:14-15`：写/改/删操作本身可遗漏、损坏、幻觉，错误入库即持久系统状态失败）

---

## 背景

沉潜（ADR-0036）是兰台唯一仍在**静默直写正式记忆**的后台合成面：

- `consolidate_cluster`（`lantai/services/consolidation_service.py:106-222`）LLM 提纯（:115-126）→ TrustMem 迁移校验（:140-142，:76-103）→ **主记忆直落 `status="active"`**（:163-179）→ 碎片折叠 `status="consolidated"`（:181-185）→ 索引同步（:190-209），全程**无 MemoryProposal、不进待审队列**。REST / MCP / 闲时调度三入口（`routes_evolution.py:63-68`、`lantai/cli/mcp.py:249-253`、`lantai/core/scheduler.py:346-356`，cron UTC 19:30，job id `"consolidation"`，无 settings 门控），用户对产物没有任何裁决点——与「人工闸门是全场独占优势」的产品定位自相矛盾（票据引 synthesis §三）。
- 快照与回滚链路对巩固产物失效：现状 checkpoint 用伪 id `"cluster_consolidation"`（`consolidation_service.py:152-161`）且主记忆自身无 checkpoint，而 rollback 按 `memory_id` 取检查点（`lantai/evolution/promoter.py:289-299`）——**现状巩固产物不可回滚**。
- 复用件齐备：`MemoryProposal` 字段齐备（`lantai/models/tables.py:332-353`；票据所引 :320 已漂移，现 :332）；`MemoryCheckpoint.proposal_id` 已备（`tables.py:377`，票据所引 :365 已漂移）；裁决链 `apply_proposal`/`decide_proposal`/`list_proposals`（`promoter.py:37`、`lantai/services/evolution_service.py:26-48`、`routes_evolution.py:16-18`）；merge 分支是「应用时折叠来源」的现成范式（`promoter.py:93-140`：逐来源 supersedes 边 + checkpoint + 归档 + FTS/向量清理同事务）；autodream 是「后台合成 → 待审提案、绝不自动应用」的直先例（`lantai/evolution/autodream.py:8`「只建提案，绝不自动应用」，:169-170）。
- 提案域现状两处缺口：`ProposalType` 无 consolidation（`lantai/models/enums.py:26-31`）；`apply_proposal` 的 add 捕获分支 `elif prop.proposal_type == "add" or not existing:`（`promoter.py:141`）会吞掉任何未知 proposal_type——新类型分支必须显式插在其之前，否则 apply 静默走 add/update 路径（落主记忆但**不折叠碎片**、checkpoint trigger 变 gate/evolve、多出 supports 边与 Evidence）。

## 决策

机制依附已登记的**沉潜**（`CONTEXT.md` 词汇表，ADR-0036），确立「巩固产物过审制」：**本票不新增正式名**（理由见决策 8），统一用描述性短语「沉潜产物过审 / 巩固产物过审」。

### 1. 模式开关（settings，仿更漏三开关范式 `lantai/core/settings.py:189-192` 三要素：段注命名 ADR/票号 + 默认值理由 + 行尾注释）

```python
# 沉潜过审（ADR-0050/票 07）：巩固产物过审开关（默认 off = 现行行为零漂移）
CONSOLIDATION_AUDIT_MODE: str = "off"  # off/shadow/enforce；非法值 fail-loud：拒绝执行巩固并 ERROR 留痕
```

落点：settings.py 沉潜相关段（autodream 段 :312-317 后新开小节），同段附三个运营参数：`CONSOLIDATION_SHADOW_MAX_DAYS`（决策 4）、`CONSOLIDATION_REJECTED_COOLDOWN_DAYS`（决策 3）、`CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES`（聚合主记忆判定阈值，`find_consolidation_clusters` 用于跳过已聚合主记忆防无限递归折叠；ADR-0002 零硬编码，settings.py:319-322）。取互斥单枚三值（与更漏三布尔正交面不同构）；**服务内消费**（`consolidate_cluster`/`run_consolidation_cycle` 读 settings），调度注册与 job id 不动（`scheduler.py:346-356`；`tests/test_scheduler.py:243` 的 job id 集合断言不破）。合并时默认 `off`；影子跑一周后由维护者决定切 `enforce`（硬时限见决策 4）。

**非法值 fail-loud（不静默回落 off）**：对更漏三开关，fail-closed＝特性不开＝保守；但本开关的 off 恰是本 ADR 要消灭的静默直写面——非法值静默回落 off，等于一个尾随空格就让系统无感回到脏写（唯一痕迹是夜间调度日志一条 warning），与决策 6「宁 miss 不脏写」方向相反。故非法值＝配置错误：`run_consolidation_cycle` 拒绝执行本周期巩固，ERROR 留痕 + report status 可见——宁巩固停摆，不静默直写。**拒绝事件本身亦落一条 `ConsolidationRun` 留痕行**（mode 记原非法值而非回落字面量、error 记原因，与决策 4 shadow 硬时限拒绝同轨）——仅内存 report + logger 无持久痕，事后排查配置错误无所依凭（决策 9 立意：拒绝须持久可审）。

### 2. 三模式行为矩阵

| 阶段 | off（默认） | shadow（影子对照） | enforce（目标终态） |
|---|---|---|---|
| LLM 提纯 + TrustMem | 照旧 | 照旧 | 照旧 |
| 主记忆落库 | 即时直写 active | 即时直写 active | **不落**（裁决 apply 后才落） |
| 碎片折叠 | 即时 consolidated | 即时 consolidated | **裁决 apply 后** |
| 提案记录 | 无 | 恰一条 shadow 影子提案 | 恰一条 pending 提案 |
| checkpoint | 伪 id、proposal_id=None（现状） | 伪 id + proposal_id=影子提案 id | apply 时逐条真实 id + proposal_id |
| 索引 | 主记忆即写 | 主记忆即写 | apply 时写主记忆 + 清碎片 |
| 调度 | 行为不变 | 行为不变 + 影子留痕 | **只产提案，不代裁决** |

off 模式逐字节不变：不写任何新行、不填任何新字段；off 冒烟须断言「零新行」。

### 3. enforce 语义（提案制）

- **生成**：提纯 + TrustMem 通过后不落主记忆、不折叠、不写索引、不写 checkpoint——落恰一条 `MemoryProposal`：`proposal_type="consolidation"`（`ProposalType` 枚举增补 `CONSOLIDATION`，`enums.py:26-31`）、`evidence_ids=source_ids`、`proposed_patch`=主记忆构造全集（content/importance/confidence/domain/lane/source_ids/decay_class="semantic"，现状 `consolidation_service.py:165-178` 构造平移）、`confidence`=提纯 confidence（`tables.py:344` 默认 0.0，漏填将令 supersedes 边 `promoter.py:130` 的 confidence 与案牍徽标 `work_item_service.py:222`「置信」显示 0.0）、tenant/user/agent/session 四元组取首碎片（聚类仅按 (domain, lane) 分组 `consolidation_service.py:44-45`，同簇未必同租户，以首碎片为准并记入 provenance；主记忆构造平移现状不设四元组——行为零漂移）、`reason`=TrustMem 校验结论、`decided_by="consolidation"`、`status=pending`。LLM 失败 / 输出无效 / TrustMem 不过维持现行「保持原库不变」（:131-142），计入 report skipped 不静默（同 autodream skipped 纪律，`autodream.py:158-160`）。
- **生成侧幂等去重与拒绝冷却**：同 `evidence_ids` 冻结集已存在 pending 的 consolidation 提案，或存在冷却期内 rejected 的同簇提案（新设置 `CONSOLIDATION_REJECTED_COOLDOWN_DAYS: int = 30`）→ 本轮跳过该簇并计入 report（skipped_dupes / skipped_rejected_cooldown）。必须项：enforce 期碎片在 pending 期间保持 active，会被下一轮 `find_consolidation_clusters`（`consolidation_service.py:36`、:43 不拦截 source_ids<3 的碎片）再次聚出——不去重则每夜重复生成同簇提案直至裁决；冷却防的是「拒绝一次＝订阅每日重复打扰」（每次再奏都是真实 LLM 提纯成本），冷却期满允许再奏——拒的是「当时产物」非永久禁令。冷却抑制的是生成侧重复奏，不触碰任何提案终态，与「提案无 TTL」（决策 6）不冲突。
- **裁决 apply**：在 `apply_proposal` 显式新增 `elif prop.proposal_type == "consolidation":` 分支，**必须插在 add 捕获分支（`promoter.py:141`）之前**（本 ADR 最高风险点，落锚测试固化：consolidation 提案 apply 后碎片必须折叠、不得出现 trigger="gate"/"evolve" 的 checkpoint）。一个事务内：①主记忆落 `active`（构造取 proposed_patch，不重推断）；②碎片折叠 `consolidated`（幂等，仅仍 active 者生效）；③supersedes 边（主记忆→碎片，方向同 merge 分支 `promoter.py:118-132`）；④逐条 `_make_checkpoint`（`promoter.py:23-34`，真实 `memory_id`、`trigger="consolidation"`、带 proposal_id——主记忆一笔 + 每碎片一笔）；⑤主记忆索引同步 embed+index_memory_item+sync_fts（现行 :190-209 平移至 apply）；⑥碎片索引清理 `sync_fts(s, src.id, None)` + `delete_memory_item(src.id)`（仿 merge `promoter.py:139-140`）→ 终态 APPLIED（:280-284 既有收尾复用）。**不复用** add 分支的 supports 边与 Evidence（:227-253）——碎片血缘由 supersedes 边表达，巩固不是新证据入树。
- **apply 对碎片现状三分（pending 期碎片并非零变更）**：pending 期碎片保持 active，同期遗忘 worker（`lantai/memory/forgetting.py:19`，:33 对全部 active 持续衰减、低于 `ARCHIVE_DECAY_THRESHOLD` 自动归档）、同周期 `prune_decayed_synapses`（`consolidation_service.py:266`，decay<0.05 且 helpful_count==0 即归档，紧随提案生成执行）、笔削 correct/retract/delete 与晚更正（票 10）均可变更或移除碎片。apply 分支对 evidence 逐条 `s.get` 三分处置：仍 active → 折叠＋supersedes 边＋checkpoint；已 archived → 仅补 supersedes 边（血缘补记，不改状态、无 checkpoint）；行已删除 → **不建边**（边指向幽灵碎片即脏写），缺口记入 apply 返回与日志。TrustMem 不重跑（校验属生成时刻语义，票 :65 不改算法），提纯基线可能过时的残余风险由裁决者凭 evidence 现状判断（入边界）。
- **stale 提案守门（模式回切防护）**：apply 前置校验——任一 evidence 已处于 `consolidated` 态即拒绝该提案（明确 reason 返回，宁 miss 不脏写）：`consolidated` 只能由一次折叠产生，出现即意味存在 off 期直写产物或先行 apply 的重叠提案——继续执行将「主记忆照落＋折叠幂等跳过」，产出双主记忆重复召回。enforce→off 回切前置条件：pending consolidation 提案清零（裁决完或显式 REJECTED＋decision_reason 标注回切）方可切回；off 期运行不读写既有提案。
- **reject**：复用既有强制 reason（`evolution_service.py:34-35`）与终态落库（:44-48）——主记忆不落、碎片原样 active、无索引变更、无 checkpoint；提案终态 REJECTED + decision_reason 留痕，`list_proposals(status="rejected")` 可读（`routes_evolution.py:16-18`），反馈回路闭环。
- **未裁决**：碎片保持现状（active），召回面零变化；提案 pending 无限期等待（无 TTL，见决策 6）。
- **返回值与 report 契约**：`consolidate_cluster` 返回「本次产物载体」——off/shadow 返回主记忆 MemoryItem、enforce 返回 MemoryProposal（类型注解宽化为 `MemoryItem | MemoryProposal | None`），杜绝 enforce 不落主记忆时被 `res is not None` 计数路径（`consolidation_service.py:261-264`）误读；`run_consolidation_cycle` 的 report 增 `proposals_created` 键（off=0 / shadow=影子提案数 / enforce=新产提案数），`new_memories` 语义固定为「新落主记忆数」（enforce 期字面 0），`status` 判定扩展为 `(new_memories + proposals_created + pruned_count) > 0`——只产提案的运行不再误报 idle（现计数与判定逻辑 `consolidation_service.py:261-267`）。既有键（last_run/consolidated_groups/pruned_count）不变，REST/MCP 报告消费方（`routes_evolution.py:63-76`、`lantai/cli/mcp.py:249-253`）与 `tests/test_consolidation.py:125-140` 的报告形状断言向后兼容（新键为增量）。

### 4. shadow 语义（影子对照，一周）

直写 + 折叠全链照旧（`consolidation_service.py:151-209` 逐行为不变），额外同步落**恰一条影子提案**：

- `ProposalStatus` 枚举增补 `SHADOW = "shadow"`（`enums.py:34-39`；status 为裸 str 无 DB CHECK 约束，`tables.py:346`，纯枚举增补**无迁移**）；
- `decided_by="shadow"`，`provenance={"mode":"shadow","master_id":<直写主记忆id>,"sources":source_ids}`，evidence_ids/proposed_patch/reason 与 enforce 同构——即「若走提案制会产出什么」的对照样本；
- 该次伪 id checkpoint 的 `proposal_id` 填影子提案 id（生成留痕 ↔ 产物对账键）；
- 影子行**结构性不可入裁决**：`decide_proposal` 仅受理 PENDING（`evolution_service.py:32`）、`apply_proposal` 仅受理 PENDING/APPROVED（`promoter.py:41-42`），双门禁天然拦截；
- 影子行不改变任何碎片/主记忆状态（纯留痕），不进 pending 裁决队列。
- **shadow 硬时限（无无限续期）**：`CONSOLIDATION_SHADOW_MAX_DAYS: int = 7`——时限自 ConsolidationRun 首条 mode=shadow 留痕起算（决策 9 的留痕即状态锚）；超期后 `run_consolidation_cycle` 拒绝执行巩固并 ERROR 留痕（同决策 1 非法值处置）。「影子跑一周后由维护者决定切 enforce」由此获得牙齿：维护者遗忘时，静默直写不会在「过审制」名义下无限合法存续。

### 5. checkpoint 关联（票第 2 条「巩固前后快照」口径）

- off：逐字节不变（伪 id、`proposal_id=None`，现状 `consolidation_service.py:152-161`）。
- shadow：伪 id checkpoint 保留 + `proposal_id`=影子提案 id。
- enforce：生成阶段**不落 checkpoint**——生成时刻零变更，checkpoint 语义是「变更前后对比快照」（`CONTEXT.md` 词汇表 checkpoint 条），提案行（proposed_patch/reason/evidence_ids）即生成留痕；且 rejected 提案若留 checkpoint 将产生指向不存在记忆的孤儿行。快照对齐由 apply 时逐条真实 id checkpoint 承载：主记忆为新建实体，before={} 空 dict（仿 add 分支 `promoter.py:210` 先例）、after=model_dump；碎片的 before=**apply 时刻该碎片的实际现状**——pending 期碎片并非零变更（遗忘/修剪/笔削/晚更正均可变更，见决策 3 三分语义），留痕如实记「apply 前一刻」而非「巩固前现场」，「生成时刻基线」由提案 proposed_patch/provenance 承载；全部带 proposal_id。伪 id checkpoint 自 enforce 起不再新增（存量行不追溯迁移——不可回滚的既成事实留档，无回填价值）。**回滚能力如实定性**：apply 时主记忆与每碎片各仅落 1 笔 checkpoint，而 rollback 要求同 memory_id ≥2 笔（`promoter.py:294-295`，不足即返回「no previous version」）——单次 apply 后任何一方都够不到回滚门槛；且 rollback 无 supersedes 补偿逻辑（`promoter.py:287-310` 只回写实体字段，不撤边、不动他者）——即便某碎片日后积累出第 2 笔 checkpoint 被回滚恢复 active，仍 active 的主记忆与 supersedes 边不会自动撤除，将造成主/碎片双活重复召回。故本 ADR 不宣称「rollback 恢复可用」：实际改善是**从「伪 id 无锚点、不可审计」到「真实 id 有留痕、可审计」**（checkpoint + proposal_id 关联整簇 before/after）；完整回滚语义（撤主记忆＋恢复碎片＋撤 supersedes 边，一事务补偿）列为后续票候选，期内如需下线某笔巩固产物，可 retract 主记忆（ADR-0047）止其召回，但**碎片恢复无工具支撑**（如实声明缺口）：笔削六操作不含 consolidated→active（`lantai/services/record_ops_service.py:31`），unarchive 守卫仅 archived 可恢复（`record_ops_service.py:251-252` 实证）——retract 后碎片停在 consolidated，恢复只剩手改 DB 一条路；「consolidated→active 恢复语义」与完整回滚同列后续票候选。

### 6. 提案无 TTL（票 :66 明示）

**决定：CANDIDATE_TTL_DAYS 不扩展到 MemoryProposal，提案无超龄自动归档。** 理由：①语义不同质——候选 TTL 处理低置信度垃圾（`lantai/services/candidate_service.py:180-200` 超龄 pending_review→rejected），巩固提案是 TrustMem 已过的高置信度合成产物，价值不随时间衰减；②超龄自动 rejected 即未经人的终态裁决，直接违反票 :64「不做自动裁决」与 autodream「绝不自动应用」（`autodream.py:8`）同纪律；③机制上 `run_candidate_ttl_once` 只查 MemoryCandidate 表（`candidate_service.py:185`），提案天然不受波及，零改动成立；④堆积的正解是生成侧幂等去重与拒绝冷却（决策 3）+ 案牍/报告可见性，不是定时清理。代价（如实）：长期不裁决则 pending 堆积、巩固收益延迟（碎片维持现状）——宁 miss（巩固延迟）不脏写（未审先生效）。

### 7. 反思产物处置（票第 4 条盘点，两条独立落库路径）

- **路径 A `lantai/cognition/reflection.py:72-163`**：晋升产物直接 `db.add` MemoryItem（:104/:142/:148，commit :162），不经 MemoryProposal——形式上绕过裁决，但产物 `status="candidate"`（`lantai/cognition/evolution.py:181`「Must be candidate」，:240/:285 同；CognitivePattern 同 candidate 直写，evolution.py:114-120），召回仅取 active（`lantai/retrieval/hybrid.py:530`），**不生效**。candidate→active 的唯一转移通道是 `KnowledgeLifecycleManager.promote`（`lantai/cognition/lifecycle.py:41-52`），而该通道在生产代码零调用（全库仅 `tests/cognitive/test_knowledge_lifecycle.py:106` 一处测试调用）——即反思 candidate 产物现状事实上**永不转正**，「不生效」结论比初审更强。**对照结论：未过审但也未生效，处于等效前置闸门（candidate 态）之后——同语义已满足，不改造，记录在案；初审「转正另经 EvolutionEngine 晋升链（在运行）」的表述失实，在此更正。**
- **路径 B `lantai/evolution/reflector.py`**：`propose_from_reflection` 落 PENDING/decided_by="reflect"，但 :395-400 设有自动应用通道（`confidence >= REFLECT_AUTO_APPLY_CONF`（`settings.py:386`=0.7）且 rejecter risk=low → 直接 `apply_proposal` 生效）——**实质绕过人工裁决，与本 ADR「后台合成产物不自动生效」原则冲突**。iter-08:15 曾记「兰台反思提案已如此设计（已过审）」，与代码实情不符，在此如实更正。按票面条件属「绕过裁决」项，同语义应纳入过审——**本 ADR 据此判定：该通道与 roadmap P1-3「反思产物一律过审」直接冲突，收口是要求而非可选项**。reflector 属反思子系统，本票实施切片只动沉潜通道，故收口执行须维护者在两案中**显式拍板其一**：(a) 随本票一并收口——增设总开关 `REFLECT_AUTO_APPLY: bool = False`（默认关；功能消费仅 `reflector.py:395` 一处、门控无外溢，已核验；`lantai/workers/digest_worker.py:458` 月度盘点模板**文本**引用该配置，落地时须同步改写该文案否则盘点失真），auto-apply 分支默认不触发、产物一律进 pending；(b) 另立收口票据排期（挂 roadmap P1-3 尾巴）。**收口两案未经维护者显式拍板并落地前，P1-3 的「反思产物一律过审」不得结项**——此为本 ADR 的显式偏离登记与结项前置条件，不静默放行、不以「待拍板」悬置。

  **收口落地（2026-09-26，维护者显式拍板选 (a)）**：已在票 07 实施切片内落地——`settings.REFLECT_AUTO_APPLY: bool = False`（默认关，消费点唯一 `reflector.py:395`，门控 `and` 于既有条件之前）；默认关时反思产物一律进 pending，`auto_applied=0`/`pending=n`；`tests/test_reflect.py` 增 `test_auto_apply_disabled_by_default`（现场断言默认值 + 产物未生效 + 零 checkpoint），既有 `test_auto_apply_deprecate`/`test_full_run_records_outcome` 改为显式 `monkeypatch` 开启开关以保留「开启路径逐字不变」的原验证意图；`digest_worker` 月度盘点「待回填结论 B」文案同步改写。**结项前置条件就此解除：roadmap P1-3「沉潜/反思产物一律过审」全链闭环。**

### 8. 命名治理（不起新名）

- 机制本质是既有「沉潜」的产物生效通道**改道既有提案裁决链**（提案：`CONTEXT.md` 词汇表 proposal 条；待办面：案牍；裁决入口/状态机全部既有）——无新实体，R3 名实相副的最准确称谓即描述性短语「沉潜产物过审」，不含未登记新名。
- R5 撞名风险：裁决/审批/验证语义邻域已被密集占用（持节 :60 审批、悬镜 :59 审批面、锦囊 :14 待审队列、案牍 :15、披沙 :51 提纯、校雠 :47、考功 :52、直断 :67、探颐 :58），新造「审/验」域词极易一物多意象。
- 新增 `ProposalStatus.SHADOW` 为技术取值（与 pending/applied 同层的英文枚举值，非正式中文名），不入词汇表。
- 若日后将同语义推广为跨子系统统一过审制（autodream/反思/沉潜）并需要伞名，届时按 ADR-0013 R4 先登记 `CONTEXT.md` 再使用。

### 9. 验收统计出口（票第 5 条）

交付 `consolidation_audit_report()` 查询件 + scripts/ CLI（范式：ADR-0049 §5 `receipt_traceability_report` / `scripts/receipt_report.py`；无样本时比例返回 `None` 不编造）。

**运行留痕（比例的分母地基，初审缺失项）**：`_LAST_CONSOLIDATION_REPORT` 是模块级内存变量且每次运行整体覆盖（`consolidation_service.py:23-29`、:276），不能作统计分母；enforce 生成阶段除提案行外无任何**运行级**留痕（决策 3/5）——若无独立留痕，「提案数 ÷ 成功提纯次数」的分母只能取自提案自身，比例恒 100% 而不可证伪。故 shadow/enforce 期 `run_consolidation_cycle` 每次运行落一条持久化运行留痕：新增 `ConsolidationRun` 审计表，仿 ReflectRun 范式（`lantai/models/tables.py:579-601` 与 `lantai/evolution/reflector.py:302-311` 的 `_record_reflect_run`），字段 {mode, clusters, purified_ok, proposals_created, skipped_dupes, skipped_lowq, pruned, ran_at}，随实施票据走 `lantai/storage/db.py` 版本链顺延迁移（现行链尾 v22，`db.py:483`）。off 期不留痕（零漂移）。口径（窗口 N 天）：

- **enforce 自证（口径唯一化，①②合取才构成票面「100% 可证伪」的完整断言）**：①比例＝窗口内**创建**（created_at∈窗口，非存续口径；排除 shadow 行）的 consolidation 提案数（status∈{pending, applied, rejected}）÷ 同窗口 mode="enforce" 留痕 `purified_ok` 总数，目标恰 100%（偏高偏低均判异常）；`purified_ok` 唯一定义＝本周期通过 LLM 提纯与 TrustMem 且进入「应落提案」判定（即去重/冷却/低质过滤**之后**）的簇数，skipped_dupes / skipped_rejected_cooldown / skipped_lowq 单列、不计入分母——分母≡应落提案数，①校验「留痕 ↔ 提案」两路写入一致性；②直写指纹＝窗口内伪 id checkpoint（memory_id="cluster_consolidation"）新增行数必须为 **0**——伪 id 行只由 off 式直写路径产生（`consolidation_service.py:152-161`），是独立于提案与留痕两路的持久化证据。仅①则「同路径写两表」的 bug 不可见，仅②则绕过两路的直写不可见，合取方唯一可实现——**合取结论以报告键 `enforce.self_attestation_ok` 显式暴露**（`(提案数==purified_ok) and (伪 id 行数==0)`；分母为 0 时 None 不编造），消费方无需自行合取以免只看 `ratio_ok` 误判「通过」；
- **shadow 对照（三方互证）**：直写主记忆数（伪 id checkpoint 行数，`consolidation_service.py:152-161` 每次直写恰一行）vs 影子提案数 vs mode="shadow" 留痕 `purified_ok`，三者应相等——直写路径与提案路径各写各的持久化痕迹，任何一边缺失即暴露；
- 分桶输出 pending/applied/rejected/shadow 与留痕分桶（skipped_dupes/skipped_lowq），供「近 N 次巩固产物带提案记录比例 100%」票面口径自证。

## 边界（如实声明，不夸口）

- **不改 TrustMem 校验**（`consolidation_service.py:76-103`）**与聚类算法**（:32-73）（票 :65）——只改产物生效通道。
- **索引清理是收紧口径**：现行折叠不清碎片 FTS/向量（:181-185 只翻 status，靠 `hybrid.py:530` active 过滤兜底）；enforce apply 后碎片索引被清（仿 merge）——off/shadow 期碎片索引照旧不清，两代语义并存至全量 enforce。
- **rejected 同簇冷却期（已决，不再待拍板）**：拒绝即人对该簇当时提纯产物的否定；无冷却则每夜 03:30 同簇重新提纯（真实 LLM 成本）并重新生成 pending 提案——拒绝等于订阅每日重复打扰。去重范围＝pending ∪ 冷却期内 rejected（`CONSOLIDATION_REJECTED_COOLDOWN_DAYS: int = 30`，见决策 3）；冷却期满允许再奏。
- **冷却期起算点用 `created_at` 近似（已知限制，如实声明）**：`MemoryProposal` **无裁决时刻列**（列集止于 `created_at`/`applied_at`，`applied_at` 仅 apply 时写、reject 不写——`decide_proposal` 拒绝分支只置 status/decided_by/decision_reason，`evolution_service.py:43-48`），故 `_consolidation_proposal_blocked` 只能以 `created_at`（提案**生成**时刻）近似裁决时刻（函数 docstring 已如实标注）。**可达失败场景**：提案 pending 逾冷却期（>30 天）后方被拒绝 → 冷却窗口自生成时刻起算、早已过期 → 次夜即重新提纯并再生成同簇提案，正是本冷却要消灭的「拒绝＝订阅每日重复打扰」在该窗口内复原。**严重度定性**：不脏写（无主记忆误落、无碎片误折叠，属「宁 miss」方向降级），代价为一次多余 LLM 提纯与一次打扰；冷却是**抑制生成侧重复奏**的运营优化，非安全门。**修法**：新增 `MemoryProposal.decided_at` 列（reject/approve 均落值）＋迁移 v23→v24＋冷却起算改读 `decided_at`（存量 rejected 行无 decided_at，回退 `created_at` 保持向后兼容）——属独立票据候选，不在本票实施切片内。
- **精确集去重不防重叠集**：碎片内容演化导致跨夜聚类集漂移时，可能出现证据集重叠的并行 pending 提案——apply 硬门（任一 evidence 已 consolidated 即拒绝，决策 3）使后至者被拒并显式留痕，不再仅靠幂等折叠兜底。
- **pending 期碎片可被变更/移除**：遗忘 worker（`lantai/memory/forgetting.py:19`）、同周期 prune（`consolidation_service.py:266`）、笔削、晚更正均不豁免 pending 案证据——apply 三分语义兜底（决策 3），TrustMem 不重跑的提纯基线过时风险由裁决者判断。
- **案牍可见性（已核验，今日可结案）**：`work_item_service.py:497` 按 status=="pending" 全量捞提案、:204-222 对 proposal_type 无白名单（title=`f"{proposal_type} 提案"`、未知类型 risk=medium）——enforce 的 consolidation 提案**自动进案牍待审**（缺口仅展示层：原始枚举文案与风险标注，实施时增补中文文案）；shadow 行 status!="pending" 天然不可见，`list_proposals`（`evolution_service.py:14-23`）需显式 `status="shadow"` 才可见。
- **无自动应用旁路（已核验）**：evolve_worker 的自动应用仅作用于本循环 propose_from_candidate 新产提案（`lantai/workers/evolve_worker.py:35-36`），`run_pending_proposals` 仅 apply APPROVED 态（:48-54）；持节巡检仅扫 pending_review 候选（`lantai/services/auto_triage_service.py:201-216`）——consolidation pending 提案无任何 worker 旁路，只能人工裁决。
- **运行留痕是新表＋迁移**：`ConsolidationRun` 数据行仅 shadow/enforce 期写入（v22→v23 顺延，`db.py:483` 链尾）；**表结构在迁移时无条件建立**（`CREATE TABLE IF NOT EXISTS`，含 off 期——迁移无法按运行时模式条件化），故精确表述为「off 期零新**行**、表结构照建」，非「零新表」（与决策 2 的「off 逐字节不变」指**数据行为**零漂移，不含 schema 版本推进；off 冒烟断言零新行，见决策 2）。
- **report 键语义随模式**：`proposals_created` 为新增键；`new_memories` 固定为「新落主记忆数」（enforce 期字面 0）、`status` 判定扩展（决策 3）——既有消费方向后兼容，按需取新键。
- **巩固回滚不闭环＋恢复无工具**：单次 apply 各实体仅 1 笔 checkpoint（<2 笔门槛 `promoter.py:294`）、rollback 无 supersedes 补偿（决策 5）；碎片恢复无工具（unarchive 仅 archived，`record_ops_service.py:251-252`）——完整回滚与 consolidated→active 恢复语义均另票。**（2026-09-26 欠账①已闭环：见 [ADR-0052](0052-consolidation-revive.md)「起复」——`record_ops_service.revive_consolidated` 一事务撤主记忆/恢复碎片/撤 supersedes 边；欠账②冷却期起算点见上条，另票 candidate。）**
- **调度不升级**：enforce 下 `run_consolidation_cycle` 只产提案不代裁决（票 :52）；REST/MCP 入口行为随模式走，无新端点、无新常驻进程。
- **反思 auto-apply 通道收口已在票 07 实施切片内落地**（2026-09-26 维护者显式拍板选方案 (a)，见决策 7 路径 B 落地段）：`REFLECT_AUTO_APPLY` 默认关，产物一律进 pending——**收口完成，roadmap P1-3 结项前置条件解除**。

## 后果

- 巩固产物从「静默直写」变为**可裁决、可留痕、可审计**：enforce 后每笔巩固有 pending→applied/rejected 全程审计（提案行 + 真实 id checkpoint 带 proposal_id + ConsolidationRun 运行留痕），roadmap P1-3「巩固产物 100% 带提案记录」从口号变成**可证伪**的实测口径。回滚如实定性：从「伪 id 无锚点、不可审计」改善为「真实 id 有留痕、可审计」，完整回滚语义不在本票（决策 5）。
- 人工闸门兑现到最后一块直写面：写入面（gate/候选）、演化面（提案/反思）、巩固面至此同轨——「宁 miss 不脏写」在全库写入路径上不再有例外面。
- 成本：shadow 期每运行多一行 MemoryProposal + 一次 checkpoint 字段填充（量级参照 autodream 每日上限 10，`settings.py:315`）；shadow/enforce 期另增每运行一行 ConsolidationRun 留痕（新表 v22→v23 迁移，off 期零成本）；enforce 期每日 03:30（UTC 19:30，`scheduler.py:346-356`）产 pending 提案，**裁决吞吐成为巩固节奏的实际上限**。
- 测试面：`tests/test_consolidation.py` 既有断言按新语义同步——主记忆 active 与碎片 consolidated 断言后移到 apply 之后（未裁决前不存在新 active 主记忆）、聚类与 chat_json patch 边界保留、报告形状断言（:125-140）既有键不变而 `proposals_created` 为增量，逐条说明改动理由（票 :53）；不 mock 冒烟走真实 SQLite 全链（`chat_json` 同接口形状替身只替外部网络，TrustMem 校验真实执行——AGENTS.md 测试纪律）。
- 已知限制：pending 期巩固收益延迟；pending 期提纯基线可能过时（碎片被遗忘/修剪/笔削变更后 TrustMem 不重跑，裁决者凭 evidence 现状判断，apply 三分语义兜底）；重叠集并行提案由 apply 硬门拒后至者（见边界）；反思 auto-apply 通道已按决策 7 方案 (a) 收口（`REFLECT_AUTO_APPLY` 默认关）——**roadmap P1-3 结项前置条件已解除**；完整巩固回滚与 consolidated→active 恢复语义均另票（决策 5）。

## 相关

- 实施票据：`.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`
- [ADR-0036](0036-sleep-memory-consolidation.md) — 沉潜（本机制依附的子系统）
- [ADR-0048](0048-genglou-bitemporal-event-time.md) — 更漏（settings 开关范式与 fail-closed 纪律来源）
- [ADR-0049](0049-receipt-chain.md) — 回执链（验收统计出口范式）
- [ADR-0044](0044-cognition-evolution-boundary.md) — 认知闭环边界（反思产物 candidate 态）
- [ADR-0013](0013-naming-system.md) — 命名体系（本票不起新名的纪律依据）
- 调研：`docs/research/memory-landscape-2026-09/iterations/iter-08.md`（TRUSTMEM）、`roadmap-v2.md:25`
- 代码：`lantai/services/consolidation_service.py`、`lantai/evolution/promoter.py`、`lantai/evolution/autodream.py`、`lantai/evolution/reflector.py`
