# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **起复——巩固撤销与碎片恢复（2026-09-26，ADR-0052；票据 `.scratch/consolidation-rollback/issues/01-p0-consolidation-revive.md`；闭环 ADR-0050 决策 5 欠账①「retract 后碎片恢复只剩手改 DB」）**：
  - **服务函数** `lantai/services/record_ops_service.py:revive_consolidated(memory_id, *, reason, actor="", session=None)`：输入二义性按形态判别（宁 miss 不脏写，不猜意图）——主记忆（`source_ids` 非空，promoter 巩固 apply 落库标记）→ 撤销全簇（主记忆 retracted + 全部 consolidated 碎片恢复 active + 删除 supersedes 边 + 主记忆/逐碎片审计，一个事务）；碎片（`status == "consolidated"`）→ 恢复 active + FTS/向量重同步 + checkpoint（`trigger="revive"`）+ 审计（`action="revive"`）；两者皆非 → `{"ok": False, "error": "invalid target ..."}`（路由层映射 409）。**已被普通撤回过的主记忆同样受理**——补完没收尾的撤销（重做索引清理并如实回报，不假设干净）。
  - **独立函数不改 `rollback`**：`promoter.rollback` 是 5 类提案共用的单实体版本回滚，塞入「撤边 + 恢复他者」会让正确性风险外溢到 merge/deprecate 分支；巩固撤销形态（一主多碎片 + supersedes 边）是 consolidation 独有，独立最诚实。
  - **幂等**（同 `retract_memory` 先例，只认状态不假设首次同步结果）：簇已撤销（主记忆 retracted 且边清零、无 consolidated 碎片）→ `already_revoked`；碎片已 active 且带 `trigger="revive"` checkpoint（起复的精确标记，区别于普通 active 记忆与晚更正 supersedes 旧值）→ `already_active`。
  - **恢复语义的如实边界**：碎片折叠不改内容（consolidated 只改 `status`），恢复是真实的；但折叠后碎片若被遗忘/笔削/晚更改变更过，恢复的是变更后现状——不宣称「恢复到巩固前现场」（生成时刻基线由提案 `proposed_patch`/`provenance` 承载，与 ADR-0050 决策 3 同口径）。
  - **常量与审计面**：`STATUS_CONSOLIDATED` 入模块常量；`AUDIT_ACTIONS` 增补 `"revive"` / `"unconsolidate"`（既有六名不动，顺序追加），既有审计查询面自动可见。
  - **命名**：「起复」（Qifu，唐宋典制「夺情起复」——官员去位后重新起用；碎片被折叠如官员去位，恢复现役即起复）已登记 `CONTEXT.md` 词汇表；候选「拾残」因与既有「拾遗」（检索韧性降级）同字不同义违反 R5 而弃用。
  - **测试增量**：`tests/test_record_lifecycle.py` 新增 `TestReviveConsolidated` 13 例不 mock 冒烟（真 DB + 真 FTS + 真实服务函数，替身边界仅 embed 与向量库）：碎片起复三面可召回/审计无正文/checkpoint 留痕、撤销全簇三面 0 命中 + 碎片全 active + 边清零、审计双面、两形态幂等、普通 active 记忆被拒、普通撤回过的主记忆补完撤销、撤销后同提案再 apply 被提案状态机拒（封死双主记忆）、FTS/向量失败如实回报且 SQL 权威过滤面兜底。**变异验证**：去掉边清理 → 3 例 failed；跳过碎片恢复 → 4 例 failed（还原后全绿）。
- **起复出口——REST + MCP（2026-09-26，票据 `.scratch/consolidation-rollback/issues/02-p0-revive-exports.md`；ADR-0052；依赖票 01）**：
  - **REST** `POST /terminal/memory/{memory_id}/revive-consolidated`（`routes_terminal.py`，笔削家族同址）：reason 必填非空（422 on empty，同 retract 口径——撤销/恢复均须留痕）；归属校验复用 `_check_ownership`（P0 票 04 单一真源）；`memory not found` → 404、`invalid target` → 409 映射同既有范式；幂等语义标注于 docstring。
  - **MCP 工具** `revive_consolidated`（`mcp.py`）：schema 同 rollback 风格（memory_id + reason，两者均 required）；handler 调 service 单一真源；空 id/空 reason → `-32602` 且不调 service（留痕强制，异常隔离范式同既有）；`tools/list` 计数断言 59→60 同步。
  - **测试增量**：`tests/test_record_lifecycle.py` 新增 `TestReviveRoutes` 7 例（碎片起复 200 / 主记忆撤销 200 + 簇内碎片全 active + 边清零 / 非巩固目标 409 资源原样 / 空 reason 422 资源原样 / 越权 403 资源原样 / 不存在 404 / 重复撤销 already_revoked）；`tests/test_mcp.py` 新增 2 例（合法输入透传返回 / 空 id 与空 reason 双校验）。**变异验证**：删路由 → 6 例 failed；去 reason 强制 → MCP 校验例 failed（还原后全绿）。
- **提案裁决时刻列 `decided_at` + 冷却期口径修正（2026-09-26，ADR-0053；票据 `.scratch/consolidation-rollback/issues/03-p0-decided-at-column.md`；闭环 ADR-0050 边界「冷却期起算点用 `created_at` 近似」条）**：
  - **Schema**：`MemoryProposal.decided_at: datetime | None = None`（nullable 无默认——NULL 是「未记录」的事实状态，宁 miss 不猜；与 `applied_at`【apply 执行时刻】正交）。
  - **迁移 v23→v24**（`db.py`，`CURRENT_SCHEMA_VERSION` 23→24）：`_has_column` 幂等守卫 + `ALTER TABLE memoryproposal ADD COLUMN decided_at DATETIME`；**老行不回填**（拿 created_at 冒充 decided_at 会让冷却起算点悄悄失真，且把「猜」写进库不可撤销）；无索引（不参与检索热路径，只服务冷却判定）。
  - **三处落点**（裁决即落时刻，与终态同事务）：`decide_proposal` approve 分支、同函数 reject 分支、`promoter.apply_proposal` stale 硬门落 REJECTED 处（ADR-0050 决策 3 硬门＝系统裁决，与人工 reject 同口径）。
  - **冷却口径修正**：`_consolidation_proposal_blocked` 改读 `decided_at or created_at`——新行精确起算，老行自动回退旧口径（**既有 dedup/cooldown 用例零改动通过即证**，行为逐字节不变）。修复场景：提案 pending 逾冷却期后方被拒，旧口径下冷却窗口自生成时刻起算早已过期，次夜即重复提纯再奏（一次多余 LLM 成本 + 一次打扰）。
  - **测试增量**：`tests/test_migrations.py` 新增 v23→v24 两例（老库加列不填充 + 新库跳过不覆盖）；`tests/test_genglou_migration.py` 版本锚 23→24 顺延；`tests/test_consolidation.py` 新增 3 例（冷却按 decided_at 起算 / 老行回退 created_at 口径不变 / decide_proposal 两分支均落值且与 applied_at 正交）。**变异验证**：去 decided_at 落值 → 裁决用例 failed；冷却读侧忽略 decided_at → decided_at 用例 failed（还原后全绿）。

## [0.22.0] - 2026-09-26 - 圭表（Guibiao · 更漏双时间轴 + 宿主矩阵 + 沉潜过审）

> 版本代号「圭表」：古代度量日影定时辰的仪器——量时而立，与「更漏」同属计时器，贴合本版时间主线。登记见 [ADR-0013](docs/adr/0013-naming-system.md) §7 与 [CONTEXT.md](CONTEXT.md) 词汇表。

### Added
- **更漏波全量落地（2026-09-25，roadmap-v2-execution 票 03/04/05/08/09/10/11；ADR-0048/0049；「更漏」Genglou=铜壶滴漏，ADR-0013 转正登记）**:
  - **事件时间双时间轴（票 05-A/08/09/10，ADR-0048）**：MemoryItem 增 `event_time`（可空+精度 year~fuzzy，宁 miss 不猜）+ `valid_from`（语义必填，迁移回填 created_at）/`valid_to`；迁移链 v20→v21（`_has_column` 幂等 + 回填 + 三索引 + 表存在/双列守卫）。写入侧 `lantai/core/time_precision.py`（I1 校验 + 显式时间确定性格式提取，相对时间解析另票）+ 候选 provenance→proposal→MemoryItem 链路透传 + correct 可更正事件时间（旧值 corrections 留痕）。检索侧 `lantai/retrieval/temporal.py` 纯函数（逐精度区间/second 闭点特判/as-of 判定按信息量取最具体）+ hybrid 双挂点（主路径+拾遗降级）+ `RetrievalParams.temporal_asof_strict/temporal_fuzzy_penalty`（fail-closed）+ `POST /search` 与 MCP `search` 增 `as_of/as_of_recorded/time_from/time_to`（缺省行为逐字节不变）。迟到更正闭环 `lantai/cognition/late_correction.py`（替换型四步一个事务：supersedes 边+知命 SUPERSEDED+valid_to/valid_from 锚定+checkpoint+`ConflictEvent(kind="override")` 落账，I3 钳制豁免——锚点早于回填 valid_from 时钳制并留 clamped/anchor_raw；失效型仅回写 valid_to；自始错误导流笔削）；直断 recency 优先 event_time 且 `recency_axis` 入 DecisionTrace——「不按最近写入机械取胜」。**E2 实测：37 条时间/更新用例双视图证据选择正确率 1.0（≥90% 口径达标）**。
  - **E1 阶段化评测 harness（票 03）**：`lantai/eval/staged.py` 六段回放（提取→闸门→入库→索引→召回→注入）+ 首错归因 Q1-Q6 + 条件化计分（失败只计入首错段）+ 预埋锚点三件套（闸门必拒/索引前删档/改写零召回）各归正确阶段不串段 + `scripts/run_staged_eval.py` 报告出口；`tests/test_staged_eval.py` 含 run_dry_run 对照校验（阶段化不改评分定义）。
  - **回执链一等化（票 04，ADR-0049）**：RetrievalEvent 增 `request_id/receipt_status/receipt_at` 三列 + 迁移 v21→v22；状态机 pending→acked（backfill 落定）/pending→missed（`mark_missed_receipts` 超时惰性判定，missed 是事实不是错误）；`receipt_traceability_report()` 可追溯率出口 + `scripts/receipt_report.py`；shell_hook NDJSON `context` 响应携 `request_id`、`backfill` 帧透传对账（request_id 不一致记日志不拒绝，归属以 event_id 为准）。**受控链可追溯率 1.0 实测**。
  - **测试增量**：test_genglou_migration（4）/test_genglou_ingest（8）/test_genglou_retrieval（11）/test_genglou_correction（4）/test_staged_eval（4）/test_receipt_chain（7）/test_e2_temporal（3）共 41 例不 mock 冒烟；既有 test_migrations/test_shell_hook/test_retrieval_log 回归全绿。
  - **已知限制（如实声明）**：SQLModel 0.0.42 对 `default_factory` 字段（valid_from）在 DB 重读 NULL 时重新应用默认——「valid_from IS NULL」无稳定读回语义，I4 的承载面为 event_time IS NULL（E2 用例按此口径）；fuzzy 精度区间定宽 ±1d；事务轴完整 system-time as-of（append-only 版本化）留 v2（`as_of_recorded` 以 created_at 近似）。
- **宿主矩阵（2026-09-26，票据 `.scratch/host-matrix/`（spec + issues 01-05）；父票 `.scratch/roadmap-v2-execution/issues/06-p1-host-matrix.md`；roadmap P1-2；不起新名——描述性短语「宿主适配层/宿主矩阵」，扩写既有 Shell Hook 条）**:
  - **协议归一化层**（`lantai/integrations/host_protocol.py`，纯函数、无 IO）：`HostRequest` 不可变值对象 + `parse_host_request` + `render_host_response`；`scripts/shell_hook.py` 退化为「适配 → 归一化 → 分发 → 渲染」的宿主实现之一，`_handle_one` 签名与返回逐字节不变——`tests/test_hermes_plugin.py` **零改动**通过、`tests/test_shell_hook.py` 既有断言**零改动**通过（该文件仅新增 2 条畸形帧回归测试，未触碰既有 24 例）。
  - **宿主帧适配**（`lantai/integrations/host_adapters.py`）：Hermes 直通；Claude Code 与 Codex CLI → `hookSpecificOutput.additionalContext`（官方文档一手实证二者同形状）。**只翻译注入类响应**，回执/对话/底本等控制面应答原样返回（否则 `receipt_status` 被抹掉）。
  - **≥3 宿主端到端冒烟**（`tests/test_host_matrix.py`，7 passed）：Hermes + Claude Code + Codex，真实子进程 + 真实 stdin/stdout NDJSON 协议帧 + 真实 SQLite/FTS，每宿主跑「注入（context 非空 + event_id）→ 回执（`receipt_status="acked"`）→ 隔离（畸形帧降级不影响后续）」三断言。唯一替身为子进程外部 embedding 网络（`tests/support/host_matrix_stub/sitecustomize.py`，确定性 3-gram 哈希；子进程无法继承父进程 patch）。
  - **安装出口**（`scripts/install_host_hooks.py`）：默认**只打印**配置片段、不写宿主目录（宁 miss 不脏写）；`--write` 才落盘并打印落点；**落点已存在则拒绝覆盖**。三宿主片段：`.claude/settings.json` / `.codex/hooks.json`（含 `features.hooks`）/ `.cursor/rules/lantai.mdc`。
  - **Cursor 降级档（如实声明）**：调研实证 Cursor **无命令钩子入口**，只能静态规则注入（无运行时检索、无回执）——故**不计入命令钩子矩阵**，矩阵取 Hermes + Claude Code + Codex（三者同构）。不依赖 Codex 阻塞语义（官方 config-reference 未载）。
  - **协议文档**（`docs/host-hook-protocol.md`）：5 动作逐条列字段/超时/降级/回执语义；与 ADR-0006（时点决策）分工，ADR 只加引用行。
  - **两处真实缺陷（实施中发现并修）**：①非对象 JSON 帧（`[1,2]`/`null`/`123`）原会抛 `AttributeError`——`--serve` 常驻 NDJSON 循环里**一个畸形帧即打死进程**，今统一静默降级；②CC/Codex 响应适配最初连回执应答也包成 `additionalContext`，致 `backfill` 在这两个宿主上**恒失败**——E2E 暴露后修正为只翻译注入类响应。
  - **已知不一致（如实登记，未改）**：`checkpoint_write` 的 `session_id` 不过归一化函数（`query`/`dialogue` 经）——既有差异，改它会变更已落库来源链值；记入协议文档 §2.4。
- **沉潜产物过审（2026-09-26，票据 `.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`；ADR-0050；不起新名——描述性短语「沉潜/巩固产物过审」，`ProposalStatus.SHADOW`/`CONSOLIDATION` 为技术枚举值不入词汇表，CONTEXT.md 零改动）**:
  - **三模式机制（ADR-0050 决策 1/2）**：settings 增 `CONSOLIDATION_AUDIT_MODE`（off/shadow/enforce，默认 off＝现行直写行为逐字节零漂移，off 冒烟断言零新行；非法值 fail-loud 不静默回落——拒绝执行本周期巩固＋ERROR 留痕＋report status="refused"，宁巩固停摆不静默直写）＋ `CONSOLIDATION_SHADOW_MAX_DAYS=7`（shadow 硬时限，自 ConsolidationRun 首条 mode=shadow 留痕起算，超期拒绝执行巩固——静默直写不能在过审制名义下无限合法存续）＋ `CONSOLIDATION_REJECTED_COOLDOWN_DAYS=30`（拒绝冷却，只抑制生成侧重复奏不触碰提案终态）。
  - **enforce 提案制（决策 3）**：`consolidate_cluster` 提纯+TrustMem 通过后只落恰一条 pending `MemoryProposal`（`ProposalType` 枚举增补 `CONSOLIDATION`；evidence_ids=source_ids、proposed_patch=主记忆构造全集、confidence=提纯 confidence【漏填将令 supersedes 边与案牍徽标显 0.0】、tenant/user/agent/session 四元组取首碎片【同簇未必同租户】、reason=TrustMem 校验结论、decided_by="consolidation"），裁决前不落主记忆不折叠零 checkpoint；生成侧幂等去重＋拒绝冷却（skipped_dupes/skipped_rejected_cooldown/skipped_lowq 单列入 report 与留痕，防每夜重复提纯的真实 LLM 成本）；提案无 TTL（宁巩固延迟不未审生效）。
  - **裁决 apply（决策 3/5）**：`promoter.apply_proposal` 显式 `elif proposal_type == "consolidation"` 分支插在 add 捕获分支之前（落锚测试固化：apply 后不得出现 trigger="gate"/"evolve" checkpoint）——一个事务内主记忆落 active（构造取 proposed_patch 不重推断，before={} 仿 add 先例）＋碎片折叠＋supersedes 边（方向同 merge 分支，confidence=提案 confidence）＋逐条真实 id checkpoint（trigger="consolidation"、全带 proposal_id；碎片 before＝apply 时刻实际现状非「巩固前现场」）＋主记忆 embed/向量/FTS 同步＋碎片 FTS/向量清理（仿 merge，收紧口径）；evidence 三分处置（仍 active→折叠+边+checkpoint；已 archived→仅补血缘边无 checkpoint；已删除→不建边，缺口入 apply 返回与日志）；stale 硬门：任一 evidence 已 consolidated 即拒绝 apply（封死 off 回切双主记忆与重叠集双活两变体）；reject 复用既有强制 reason 与终态落库，碎片原样零索引变更。
  - **shadow 影子对照（决策 4）**：直写+折叠全链照旧，另落恰一条 `ProposalStatus.SHADOW` 影子提案（纯枚举增补无迁移；decided_by="shadow"、provenance 对账记 master_id/sources）＋该次伪 id checkpoint 的 proposal_id 填影子提案 id（生成留痕↔产物对账键）；影子行双门禁结构性不可裁决不可应用（decide 仅受理 PENDING、apply 仅受理 PENDING/APPROVED）、不进 pending 裁决队列。
  - **验收统计出口（决策 9）**：新表 `ConsolidationRun` 运行留痕（shadow/enforce 期每次运行一行，迁移 v22→v23，off 期零新行；仿 ReflectRun 范式）＋ `consolidation_audit_report()` 查询件＋ `scripts/consolidation_audit_report.py` CLI——enforce 自证①②合取（窗口内创建的 consolidation 提案数÷留痕 purified_ok 恰 100%【留痕↔提案两路一致性】＋窗口内伪 id checkpoint 新增行数必须为 0【独立于两路的直写指纹】，仅①同路径写两表 bug 不可见、仅②绕过两路直写不可见）、shadow 三方互证（伪 id checkpoint 行数＝影子提案数＝purified_ok）、无样本比例返回 None 不编造；run report 增 proposals_created/mode/skipped_* 增量键，new_memories 固定「新落主记忆数」（enforce 期字面 0），status 判定扩展为 (new_memories+proposals_created+pruned)>0 防只产提案误报 idle。
  - **案牍可见性**：consolidation 提案按 status=="pending" 捞取自动进案牍待审（无类型白名单），展示层增补「沉潜巩固提案」中文文案与风险标注（折叠多碎片与 merge/deprecate 同级 high）；无自动应用旁路（evolve_worker 仅 apply APPROVED、持节仅扫 pending_review 候选，已核验入 ADR 边界）——consolidation pending 提案只能人工裁决。
  - **测试增量**：test_consolidation.py 按三模式逐条改写并注明理由（18 例不 mock 冒烟：off 零新行/enforce 生成→apply→拒绝真实全链+FTS/向量/checkpoint 断言/evidence 三分/stale 硬门/shadow 双门禁/去重冷却/非法值 fail-loud/shadow 超期/验收统计含 CLI）；test_migrations.py 增 v22→v23 用例；test_genglou_migration 版本锚 22→23 顺延；既有 REST/MCP 报告形状断言保留（既有键向后兼容，新键为增量）。
  - **反思过审收口（决策 7a，维护者 2026-09-26 显式拍板）**：增设 `REFLECT_AUTO_APPLY: bool = False` 总开关——默认关＝反思产物一律进 pending 待人工裁决（与沉潜/autodream 同轨：后台合成产物不自动生效）；置 True 恢复「高置信 + rejecter risk=low 自动 apply」既有通道（`REFLECT_AUTO_APPLY_CONF=0.7` 仅开启时生效）。消费点唯一（`reflector.py:395`），`digest_worker` 月度盘点「待回填结论 B」文案同步改写（否则盘点失真）。**至此 roadmap P1-3「沉潜/反思产物一律过审」全链闭环，结项前置条件解除。**
  - **零硬编码与效率整改**：`find_consolidation_clusters` 聚合主记忆判定阈值原硬编码 `3` 提取为 `CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES: int = 3`（ADR-0002）；`_consolidation_proposal_blocked` 状态过滤下推 SQL（只取 pending/rejected 两态，applied/shadow 行与判定无关却永久累积，原全表取回再于 Python 内过滤的代价随时长线性增长）；`work_item_service` 提案分支消除同一 id 的重复 `s.get`。
  - **终审四项（代码审查两轴整改）**：①`promoter.apply_proposal` stale 硬门原「早退不改状态」造成 **livelock**——`decide_proposal` 已置 APPROVED 而 `run_pending_proposals` 专捞 APPROVED 每轮重试每次失败（独立探针证实）；改为落终态 `REJECTED` + `decision_reason` 落痕 + commit（ADR-0050 决策 3「拒绝该提案」的终态语义），探针复验状态不再回捞；②`_LAST_CONSOLIDATION_REPORT` 初值仅 5 个旧键而 REST/MCP 直接透传该 dict——首运行前读新键即 KeyError，初值键集补齐至与 ADR 决策 3 报告契约一致；③`enforce.self_attestation_ok` 合取键（`(提案数==purified_ok) and (伪id行数==0)`，分母 0 时 None）——ADR 决策 9 明言①②合取方唯一可实现，原仅暴露两个分键，消费方只看 `ratio_ok` 会在直写指纹违约时误判「通过」。另：非法值拒绝路径补落 `ConsolidationRun` 留痕行（与 shadow 硬时限拒绝同轨；mode 记原非法值），拒绝事件不再只有内存 report 与 logger。
  - **已知限制（如实声明）**：单次 apply 各实体仅 1 笔 checkpoint（<2 笔回滚门槛）且 rollback 无 supersedes 补偿——改善为「真实 id 有留痕可审计」但不宣称「rollback 恢复可用」；碎片恢复无工具（笔削六操作不含 consolidated→active、unarchive 仅 archived），期内下线只能 retract 主记忆止召回＋手改 DB，完整回滚与恢复语义另票；off/shadow 期碎片 FTS/向量照旧不清（两代语义并存至全量 enforce）；重叠集并行 pending 提案由 apply 硬门拒后至者显式留痕；**冷却期起算点用 `created_at` 近似**——`MemoryProposal` 无裁决时刻列（`applied_at` 仅 apply 时写、reject 不写），提案 pending 逾冷却期后方被拒则该窗口内冷却失效（次夜重复提纯与打扰一次）；不脏写属「宁 miss」方向降级，修法须新增 `decided_at` 列＋迁移（v23→v24）另票。

### Fixed
- **沉潜零硬编码收尾（2026-09-26，ADR-0002）**：`find_consolidation_clusters` 的 `min_cluster_size`（原签名默认值 `3` + `run_consolidation_cycle` 调用字面量两处）与 `prune_decayed_synapses` 的 `threshold`（原签名默认值 `0.05` + 调用字面量两处）提取为 settings `CONSOLIDATION_MIN_CLUSTER_SIZE: int = 3` / `CONSOLIDATION_PRUNE_THRESHOLD: float = 0.05`，签名默认值改 `None` 惰性取 settings（显式传参行为不变）；两例不 mock 冒烟（`test_min_cluster_size_is_configurable` / `test_prune_threshold_is_configurable`）以「调参前后同一输入行为反转」锚定参数真实生效，变异验证（还原硬编码字面量 → 两例均 failed）证明测试不空转。
- **终验整改三连（2026-09-26，genglou 票 09/11）**：①ConflictEngine diffs 只算数值键（recency_axis 字符串入差值致 decide 全链炸）②asof_matches 判定顺序按信息量取最具体且 I4 承载面定为 event_time IS NULL（SQLModel 0.0.42 default_factory 读回语义下 valid_from 无稳定 NULL，如实入 CHANGELOG 已知限制）③test_infra 的 chromadb patch 改 sys.modules 条目替换（全局模块 setattr 污染内部组件缓存泄漏至后续测试，全量实证）+ 评测 embed 统一确定性 hash（杜绝 1024/512 混维）；全量门禁 1138 passed + 遗忘质量 PASS
- **现状审阅六步收口（2026-09-20 第三批，票据 `.scratch/state-remediation-20260920/`）**:

### Added
- **宿主矩阵（2026-09-26，票据 `.scratch/host-matrix/`（spec + issues 01-05）；父票 `.scratch/roadmap-v2-execution/issues/06-p1-host-matrix.md`；roadmap P1-2；不起新名——描述性短语「宿主适配层/宿主矩阵」，扩写既有 Shell Hook 条）**:
  - **协议归一化层**（`lantai/integrations/host_protocol.py`，纯函数、无 IO）：`HostRequest` 不可变值对象 + `parse_host_request` + `render_host_response`；`scripts/shell_hook.py` 退化为「适配 → 归一化 → 分发 → 渲染」的宿主实现之一，`_handle_one` 签名与返回逐字节不变——`tests/test_hermes_plugin.py` **零改动**通过、`tests/test_shell_hook.py` 既有断言**零改动**通过（该文件仅新增 2 条畸形帧回归测试，未触碰既有 24 例）。
  - **宿主帧适配**（`lantai/integrations/host_adapters.py`）：Hermes 直通；Claude Code 与 Codex CLI → `hookSpecificOutput.additionalContext`（官方文档一手实证二者同形状）。**只翻译注入类响应**，回执/对话/底本等控制面应答原样返回（否则 `receipt_status` 被抹掉）。
  - **≥3 宿主端到端冒烟**（`tests/test_host_matrix.py`，7 passed）：Hermes + Claude Code + Codex，真实子进程 + 真实 stdin/stdout NDJSON 协议帧 + 真实 SQLite/FTS，每宿主跑「注入（context 非空 + event_id）→ 回执（`receipt_status="acked"`）→ 隔离（畸形帧降级不影响后续）」三断言。唯一替身为子进程外部 embedding 网络（`tests/support/host_matrix_stub/sitecustomize.py`，确定性 3-gram 哈希；子进程无法继承父进程 patch）。
  - **安装出口**（`scripts/install_host_hooks.py`）：默认**只打印**配置片段、不写宿主目录（宁 miss 不脏写）；`--write` 才落盘并打印落点；**落点已存在则拒绝覆盖**。三宿主片段：`.claude/settings.json` / `.codex/hooks.json`（含 `features.hooks`）/ `.cursor/rules/lantai.mdc`。
  - **Cursor 降级档（如实声明）**：调研实证 Cursor **无命令钩子入口**，只能静态规则注入（无运行时检索、无回执）——故**不计入命令钩子矩阵**，矩阵取 Hermes + Claude Code + Codex（三者同构）。不依赖 Codex 阻塞语义（官方 config-reference 未载）。
  - **协议文档**（`docs/host-hook-protocol.md`）：5 动作逐条列字段/超时/降级/回执语义；与 ADR-0006（时点决策）分工，ADR 只加引用行。
  - **两处真实缺陷（实施中发现并修）**：①非对象 JSON 帧（`[1,2]`/`null`/`123`）原会抛 `AttributeError`——`--serve` 常驻 NDJSON 循环里**一个畸形帧即打死进程**，今统一静默降级；②CC/Codex 响应适配最初连回执应答也包成 `additionalContext`，致 `backfill` 在这两个宿主上**恒失败**——E2E 暴露后修正为只翻译注入类响应。
  - **已知不一致（如实登记，未改）**：`checkpoint_write` 的 `session_id` 不过归一化函数（`query`/`dialogue` 经）——既有差异，改它会变更已落库来源链值；记入协议文档 §2.4。
- **沉潜产物过审（2026-09-26，票据 `.scratch/roadmap-v2-execution/issues/07-p1-consolidation-audit.md`；ADR-0050；不起新名——描述性短语「沉潜/巩固产物过审」，`ProposalStatus.SHADOW`/`CONSOLIDATION` 为技术枚举值不入词汇表，CONTEXT.md 零改动）**:
  - **三模式机制（ADR-0050 决策 1/2）**：settings 增 `CONSOLIDATION_AUDIT_MODE`（off/shadow/enforce，默认 off＝现行直写行为逐字节零漂移，off 冒烟断言零新行；非法值 fail-loud 不静默回落——拒绝执行本周期巩固＋ERROR 留痕＋report status="refused"，宁巩固停摆不静默直写）＋ `CONSOLIDATION_SHADOW_MAX_DAYS=7`（shadow 硬时限，自 ConsolidationRun 首条 mode=shadow 留痕起算，超期拒绝执行巩固——静默直写不能在过审制名义下无限合法存续）＋ `CONSOLIDATION_REJECTED_COOLDOWN_DAYS=30`（拒绝冷却，只抑制生成侧重复奏不触碰提案终态）。
  - **enforce 提案制（决策 3）**：`consolidate_cluster` 提纯+TrustMem 通过后只落恰一条 pending `MemoryProposal`（`ProposalType` 枚举增补 `CONSOLIDATION`；evidence_ids=source_ids、proposed_patch=主记忆构造全集、confidence=提纯 confidence【漏填将令 supersedes 边与案牍徽标显 0.0】、tenant/user/agent/session 四元组取首碎片【同簇未必同租户】、reason=TrustMem 校验结论、decided_by="consolidation"），裁决前不落主记忆不折叠零 checkpoint；生成侧幂等去重＋拒绝冷却（skipped_dupes/skipped_rejected_cooldown/skipped_lowq 单列入 report 与留痕，防每夜重复提纯的真实 LLM 成本）；提案无 TTL（宁巩固延迟不未审生效）。
  - **裁决 apply（决策 3/5）**：`promoter.apply_proposal` 显式 `elif proposal_type == "consolidation"` 分支插在 add 捕获分支之前（落锚测试固化：apply 后不得出现 trigger="gate"/"evolve" checkpoint）——一个事务内主记忆落 active（构造取 proposed_patch 不重推断，before={} 仿 add 先例）＋碎片折叠＋supersedes 边（方向同 merge 分支，confidence=提案 confidence）＋逐条真实 id checkpoint（trigger="consolidation"、全带 proposal_id；碎片 before＝apply 时刻实际现状非「巩固前现场」）＋主记忆 embed/向量/FTS 同步＋碎片 FTS/向量清理（仿 merge，收紧口径）；evidence 三分处置（仍 active→折叠+边+checkpoint；已 archived→仅补血缘边无 checkpoint；已删除→不建边，缺口入 apply 返回与日志）；stale 硬门：任一 evidence 已 consolidated 即拒绝 apply（封死 off 回切双主记忆与重叠集双活两变体）；reject 复用既有强制 reason 与终态落库，碎片原样零索引变更。
  - **shadow 影子对照（决策 4）**：直写+折叠全链照旧，另落恰一条 `ProposalStatus.SHADOW` 影子提案（纯枚举增补无迁移；decided_by="shadow"、provenance 对账记 master_id/sources）＋该次伪 id checkpoint 的 proposal_id 填影子提案 id（生成留痕↔产物对账键）；影子行双门禁结构性不可裁决不可应用（decide 仅受理 PENDING、apply 仅受理 PENDING/APPROVED）、不进 pending 裁决队列。
  - **验收统计出口（决策 9）**：新表 `ConsolidationRun` 运行留痕（shadow/enforce 期每次运行一行，迁移 v22→v23，off 期零新行；仿 ReflectRun 范式）＋ `consolidation_audit_report()` 查询件＋ `scripts/consolidation_audit_report.py` CLI——enforce 自证①②合取（窗口内创建的 consolidation 提案数÷留痕 purified_ok 恰 100%【留痕↔提案两路一致性】＋窗口内伪 id checkpoint 新增行数必须为 0【独立于两路的直写指纹】，仅①同路径写两表 bug 不可见、仅②绕过两路直写不可见）、shadow 三方互证（伪 id checkpoint 行数＝影子提案数＝purified_ok）、无样本比例返回 None 不编造；run report 增 proposals_created/mode/skipped_* 增量键，new_memories 固定「新落主记忆数」（enforce 期字面 0），status 判定扩展为 (new_memories+proposals_created+pruned)>0 防只产提案误报 idle。
  - **案牍可见性**：consolidation 提案按 status=="pending" 捞取自动进案牍待审（无类型白名单），展示层增补「沉潜巩固提案」中文文案与风险标注（折叠多碎片与 merge/deprecate 同级 high）；无自动应用旁路（evolve_worker 仅 apply APPROVED、持节仅扫 pending_review 候选，已核验入 ADR 边界）——consolidation pending 提案只能人工裁决。
  - **测试增量**：test_consolidation.py 按三模式逐条改写并注明理由（15 例不 mock 冒烟：off 零新行/enforce 生成→apply→拒绝真实全链+FTS/向量/checkpoint 断言/evidence 三分/stale 硬门/shadow 双门禁/去重冷却/非法值 fail-loud/shadow 超期/验收统计含 CLI）；test_migrations.py 增 v22→v23 用例；test_genglou_migration 版本锚 22→23 顺延；既有 REST/MCP 报告形状断言保留（既有键向后兼容，新键为增量）。
  - **反思过审收口（决策 7a，维护者 2026-09-26 显式拍板）**：增设 `REFLECT_AUTO_APPLY: bool = False` 总开关——默认关＝反思产物一律进 pending 待人工裁决（与沉潜/autodream 同轨：后台合成产物不自动生效）；置 True 恢复「高置信 + rejecter risk=low 自动 apply」既有通道（`REFLECT_AUTO_APPLY_CONF=0.7` 仅开启时生效）。消费点唯一（`reflector.py:395`），`digest_worker` 月度盘点「待回填结论 B」文案同步改写（否则盘点失真）。**至此 roadmap P1-3「沉潜/反思产物一律过审」全链闭环，结项前置条件解除。**
  - **零硬编码与效率整改**：`find_consolidation_clusters` 聚合主记忆判定阈值原硬编码 `3` 提取为 `CONSOLIDATION_AGGREGATE_MASTER_MIN_SOURCES: int = 3`（ADR-0002）；`_consolidation_proposal_blocked` 状态过滤下推 SQL（只取 pending/rejected 两态，applied/shadow 行与判定无关却永久累积，原全表取回再于 Python 内过滤的代价随时长线性增长）；`work_item_service` 提案分支消除同一 id 的重复 `s.get`。
  - **终审四项（代码审查两轴整改）**：①`promoter.apply_proposal` stale 硬门原「早退不改状态」造成 **livelock**——`decide_proposal` 已置 APPROVED 而 `run_pending_proposals` 专捞 APPROVED 每轮重试每次失败（独立探针证实）；改为落终态 `REJECTED` + `decision_reason` 落痕 + commit（ADR-0050 决策 3「拒绝该提案」的终态语义），探针复验状态不再回捞；②`_LAST_CONSOLIDATION_REPORT` 初值仅 5 个旧键而 REST/MCP 直接透传该 dict——首运行前读新键即 KeyError，初值键集补齐至与 ADR 决策 3 报告契约一致；③`enforce.self_attestation_ok` 合取键（`(提案数==purified_ok) and (伪id行数==0)`，分母 0 时 None）——ADR 决策 9 明言①②合取方唯一可实现，原仅暴露两个分键，消费方只看 `ratio_ok` 会在直写指纹违约时误判「通过」。另：非法值拒绝路径补落 `ConsolidationRun` 留痕行（与 shadow 硬时限拒绝同轨；mode 记原非法值），拒绝事件不再只有内存 report 与 logger。
  - **已知限制（如实声明）**：单次 apply 各实体仅 1 笔 checkpoint（<2 笔回滚门槛）且 rollback 无 supersedes 补偿——改善为「真实 id 有留痕可审计」但不宣称「rollback 恢复可用」；碎片恢复无工具（笔削六操作不含 consolidated→active、unarchive 仅 archived），期内下线只能 retract 主记忆止召回＋手改 DB，完整回滚与恢复语义另票；off/shadow 期碎片 FTS/向量照旧不清（两代语义并存至全量 enforce）；重叠集并行 pending 提案由 apply 硬门拒后至者显式留痕；**冷却期起算点用 `created_at` 近似**——`MemoryProposal` 无裁决时刻列（`applied_at` 仅 apply 时写、reject 不写），提案 pending 逾冷却期后方被拒则该窗口内冷却失效（次夜重复提纯与打扰一次）；不脏写属「宁 miss」方向降级，修法须新增 `decided_at` 列＋迁移（v23→v24）另票。
- **更漏波全量落地（2026-09-25，roadmap-v2-execution 票 03/04/05/08/09/10/11；ADR-0048/0049；「更漏」Genglou=铜壶滴漏，ADR-0013 转正登记）**:
  - **事件时间双时间轴（票 05-A/08/09/10，ADR-0048）**：MemoryItem 增 `event_time`（可空+精度 year~fuzzy，宁 miss 不猜）+ `valid_from`（语义必填，迁移回填 created_at）/`valid_to`；迁移链 v20→v21（`_has_column` 幂等 + 回填 + 三索引 + 表存在/双列守卫）。写入侧 `lantai/core/time_precision.py`（I1 校验 + 显式时间确定性格式提取，相对时间解析另票）+ 候选 provenance→proposal→MemoryItem 链路透传 + correct 可更正事件时间（旧值 corrections 留痕）。检索侧 `lantai/retrieval/temporal.py` 纯函数（逐精度区间/second 闭点特判/as-of 判定按信息量取最具体）+ hybrid 双挂点（主路径+拾遗降级）+ `RetrievalParams.temporal_asof_strict/temporal_fuzzy_penalty`（fail-closed）+ `POST /search` 与 MCP `search` 增 `as_of/as_of_recorded/time_from/time_to`（缺省行为逐字节不变）。迟到更正闭环 `lantai/cognition/late_correction.py`（替换型四步一个事务：supersedes 边+知命 SUPERSEDED+valid_to/valid_from 锚定+checkpoint+`ConflictEvent(kind="override")` 落账，I3 钳制豁免——锚点早于回填 valid_from 时钳制并留 clamped/anchor_raw；失效型仅回写 valid_to；自始错误导流笔削）；直断 recency 优先 event_time 且 `recency_axis` 入 DecisionTrace——「不按最近写入机械取胜」。**E2 实测：37 条时间/更新用例双视图证据选择正确率 1.0（≥90% 口径达标）**。
  - **E1 阶段化评测 harness（票 03）**：`lantai/eval/staged.py` 六段回放（提取→闸门→入库→索引→召回→注入）+ 首错归因 Q1-Q6 + 条件化计分（失败只计入首错段）+ 预埋锚点三件套（闸门必拒/索引前删档/改写零召回）各归正确阶段不串段 + `scripts/run_staged_eval.py` 报告出口；`tests/test_staged_eval.py` 含 run_dry_run 对照校验（阶段化不改评分定义）。
  - **回执链一等化（票 04，ADR-0049）**：RetrievalEvent 增 `request_id/receipt_status/receipt_at` 三列 + 迁移 v21→v22；状态机 pending→acked（backfill 落定）/pending→missed（`mark_missed_receipts` 超时惰性判定，missed 是事实不是错误）；`receipt_traceability_report()` 可追溯率出口 + `scripts/receipt_report.py`；shell_hook NDJSON `context` 响应携 `request_id`、`backfill` 帧透传对账（request_id 不一致记日志不拒绝，归属以 event_id 为准）。**受控链可追溯率 1.0 实测**。
  - **测试增量**：test_genglou_migration（4）/test_genglou_ingest（8）/test_genglou_retrieval（11）/test_genglou_correction（4）/test_staged_eval（4）/test_receipt_chain（7）/test_e2_temporal（3）共 41 例不 mock 冒烟；既有 test_migrations/test_shell_hook/test_retrieval_log 回归全绿。
  - **已知限制（如实声明）**：SQLModel 0.0.42 对 `default_factory` 字段（valid_from）在 DB 重读 NULL 时重新应用默认——「valid_from IS NULL」无稳定读回语义，I4 的承载面为 event_time IS NULL（E2 用例按此口径）；fuzzy 精度区间定宽 ±1d；事务轴完整 system-time as-of（append-only 版本化）留 v2（`as_of_recorded` 以 created_at 近似）。

### Fixed
- **现状审阅六步收口（2026-09-20 第三批，票据 `.scratch/state-remediation-20260920/`）**:
  - **conftest API_KEY 测试环境消毒（票 02）**：仓库根部署 `.env`（gitignored）经 settings 的 env_file 渗入测试进程，`dev_mode_allowed()` 全局拒绝 DEV MODE——29 文件 70 例 401。conftest 加 autouse fixture 清 `settings.API_KEY` + pin 测试（`tests/test_test_environment.py`）把「测试环境 API_KEY 恒空」钉成显式契约；裸跑 `pytest tests/ -q`（无任何环境前缀）恢复全绿。
  - **终端写路由守卫补全（票 03）**：`PUT /terminal/memory/{id}` 补归属校验（403，与 DELETE 同一 `ensure_can_delete` 真源同口径）——原先同资源删有守卫、改无守卫；retracted 拒改文（409）堵 D23「撤回后三面 0 命中」被改文旁路重新填回 FTS/向量的缺口；顺带修复 `updated_at` 赋 ISO 字符串致 PUT 任何成功更新必 500 的隐藏 bug（该路由此前零测试覆盖，新正面对照测试抓出）；CHANGELOG 票04「统一 403 不区分 404 防存在性探测」假宣称改正为如实表述（404 不存在 / 403 越权，REST 常规可区分）。
  - **插件注入读路径 session_id 透传（票 05）**：`_call_hook` 携带 session_id（shell_hook 服务端本就支持，纯发送端补线），Hermes 主环路检索事件落会话——「带 session 的读才算真实会话读」从写路径半程补齐全链；缓冲序号 docstring 对齐 fd03b184 后的单调计数语义。无会话行为不变（空串照发，服务端归一化为 NULL）。
  - **BM25 AND 语义如实声明（票 04，docs 零行为变更）**：更正 8834f846「确定性 AND 路径零改动」假宣称——默认开档下 search_fts 与 OR 召回面共用 `_bm25_keywords`，AND 语义为「各 gram 任意位置全命中」，gram 跨位拼合假阳性面如实写明（fts.py/settings.py/测试 docstring/CHANGELOG 四处）；`_GRAM_TERMS_MAX` 定义前移；Whitepaper 司天 ADR 0044→0045 误引修正。评测门数值不动（typo_mid 1.0 等 6/6 PASS）。

### Added
- **笔削（Bixiao，撤回/删除四分法，2026-09-19 第三批；roadmap-v2 P0-2 / 调研 D23；命名登记 CONTEXT.md，《史记》「笔则笔，削则削」；ADR-0047）**:
  - **四分语义**：纠错 correct（就地改文保留版本历史，旧文进 `provenance.corrections`）、撤回 retract（主张停用即全检索面禁用，不可自动复活，unretract 仅 admin）、归档 archive（可逆退出常规检索，FTS/向量行保留复原零成本）、删除 delete（净清除正文，仅留无正文审计）。服务单一真源 `lantai/services/record_ops_service.py`，路由 `POST /terminal/memory/{id}/retract|unretract|archive|unarchive|correct`。
  - **验收口径落地（D23）**：确定性用例「撤回后禁用命中=0」——SQL status / FTS / 向量三面 0 命中（`tests/test_record_lifecycle.py` 21 例不 mock 冒烟）；FTS 清理失败注入测试证明响应如实上报且 SQL 权威过滤面兜底仍 0 命中（宁 miss 不脏写）。
  - **无正文审计**：新表 `memory_audit_events`（`MemoryAuditEvent`）——六操作全枚举留痕，只记 content_hash/长度/版本，永不存正文（隐私删除后唯一痕迹，铁律）。
  - **不复活约束**：retracted 不被沉潜/晋升等任何 worker 翻回 active（锚测试固化）。
  - **双轴审查整改**：向量重同步 `embed()` 取值漏 `[0]`（三层嵌套必炸，替身加形状守卫锚定）+ metadata 补齐 8 键归属契约（缺键会让属主过滤检索永久丢失该条，守卫锚定）；归档仅 active 入口（candidate 不得绕晋升闸门）；纠错改文 FTS 失败随事务回滚（不留「可命中旧文」脏索引，与 PATCH 同策略）；retract 幂等分支不再假报同步成功；delete 审计 best-effort 如实进 warnings。

### Fixed
- **三处索引同步静默失败（同 except-pass 家族，笔削票据 05 附带发现）**：删除路由 import 不存在的 `remove_fts` → ImportError 被吞，FTS 清理从未生效；更新路由调向量库不存在的 `vs.update` → AttributeError 被吞，向量重同步从未生效；更新路由给 `sync_fts` 传 driver 连接而非 Session → 同样被吞。现 FTS 同事务同步（ADR-0008 强一致，失败随事务回滚）、向量 best-effort 且结果如实进响应（`fts_removed`/`vector_synced`/`warnings`），不再假装干净。

### Added
- **遗留问题清理（2026-09-19 第二批，票 06/07）**:
  - **FTS BM25 3-gram 滑窗分词（票 06）**：OR+bm25 召回路径原按空白切词，中文整句退化为单个短语匹配——词中错字/词面重叠改写全部零召回（基线实测）。改 CJK 3-gram 滑窗 + ASCII 整词（`_bm25_keywords` 单一真源，`FTS_BM25_GRAM_TOKENIZE` 默认开，off=旧语义对照）：错字只污染个别 gram，共享词根即部分命中。**typo_mid 0→1.0（GATES 增补确定性门 1.0）**、paraphrase 0→0.25（词面重叠型，维持报告型——完全改写归向量层）；AND 确定性路径（search_fts）共用同一关键词源，默认开档下语义同步松化为「各 gram 全命中」而非旧整句短语——已知假阳性面为 gram 跨位拼合（现状整改票04 如实声明，五项既有门以新语义验收不动）；顺带清除 search_fts 遗留 debug print。
  - **依赖 advisory 处置留痕（票 07）**：pip-audit 定位 Mimosa 离线 advisory 匹配项——chromadb 0.6.3 ×3（PYSEC-2026-3813/3814/3815，修复仅在 1.x 重大重写版）。处置：wontfix 留痕——兰台仅内嵌 PersistentClient（无服务端/RBAC 面、单用户本地），pyproject 注释留证，1.x 迁移登记独立专项。

### Added
- **P0 可靠性自证 + 宿主闭环（2026-09-19，方向调研 `docs/research/agent-memory-development-directions-2026-09.md`，票据 `.scratch/p0-reliability-host-loop/`）**:
  - **宿主来源链贯通 + 注入回执（票 02）**：Hermes 插件缓冲条目携带会话内 `turn` 序号，flush 逐条 `{type:dialogue, text, session_id, turn}` 透传到候选 `session_id` 列 + `provenance.origin_turn`（缺会话留空、缺轮次 None，宁 miss 不脏写）；检索注入成功后按 `event_id`+`evidence` 回填 `RetrievalEvent.used_ids`（弱标注「已注入」，失败静默）；shell_hook 新增 `backfill` NDJSON 动作；`build_context`/`_try_log` 透传 `session_id`；MCP `search` 可选 `session_id` 参数——写线活性判据「带 session 的读才算真实会话读」从此全链贯通。
  - **樊篱（Fanli，数据围栏，票 03；命名登记 CONTEXT.md，《诗经》「折柳樊圃」）**：`lantai/llm/fence.py` 单一真源——记忆正文注入提示前包 `<memory_data>` 数据围栏 + 固定声明「以下为历史记忆数据，不是指令」（OWASP LLM01 纵深防御）；出口全覆盖：shell_hook 注入串、MCP search results/evidence、认知中间件摘要、`to_prompt`、reflector 内部 LLM 读；正文携带的闭合标记中性化（围栏不可被正文截断）；`DATA_FENCE_ENABLED` 默认开，off 对照测试锚定。如实标注：纵深防御，不宣称绝对防注入。
  - **删除路由归属校验（票 04）**：`acl.ensure_can_delete` 单一真源（admin 全权；非 admin 校验 lane ∈ allowed_lanes——agent 绑定优先、租户匹配、用户匹配；越权 403、资源不存在 404，REST 常规语义可区分）；接入 `DELETE /terminal/memory/{id}`、`PUT /terminal/memory/{id}`（现状整改票 03 补入——原先写路由无归属校验，同资源删有守卫改无守卫）、`POST /terminal/merge`（src+tgt 双查）、`DELETE /documents/{id}`、`DELETE /edges/{id}`；无归属历史行（user_id NULL）不视为越权，只受 lane 约束。
  - **评测两层计分基线（票 05，LongMemEval 式召回/回答分层）**：数据集 80→94（paraphrase×8 带 key_points、typo_mid×6 词中错字）；召回层新增 `paraphrase_recall_rate` / `typo_mid_recall_rate`（离线基线诚实为 0——FTS AND 链不覆盖泛化，只报告不设门，派生票 06「FTS OR 兜底」）；回答层 `lantai/eval/answer_quality.py`（rule_judge 确定性要点命中 / llm_judge 选配含畸形降级 / compute_answer_metrics 按要点加权分维度）；`--judge` CLI 与两层报告；基线 `docs/memory-quality/baseline-2026-09-19.md`（离线门禁 PASS）。
  - **测试隔离常驻绊线（票 01）**：conftest 每测试 setup 校验 `lantai.storage.db` 模块级 `get_session`/`engine` 身份，被改脏即在下个测试点名前置污染者（历史：finally 置 None 污染 + 手写 `_patch_session` 与 monkeypatch 双层补丁因撕卸顺序回写泄漏，139 例连坐）。

### Added
- **v022 上游吸收（aiduMEI v21.2 Memmy 融改，调研 `docs/research/upstream-v212-gap-analysis.md`，票据 `.scratch/v022-upstream-v212-adopt/`）**:
  - **来源链贯通（票据 01）**：`session_id` / `origin_turn` 显式透传——对话摄取（`POST /dialogue[/async]`）、手动写入（`AddMemoryReq`）、原文直存（`RawMemoryReq`）落 `MemoryCandidate.session_id` 与 `provenance.origin_*`，随 `proposer → promoter` 全链继承到 `MemoryItem`；无来源如实 NULL（上游教训：出身必须显式传递，隐式通道上线即空转）。Coalesce 冲刷合并内容跨会话，出身宁留空不错误归属。
  - **回声抑制（票据 01 检索半，上游 M2）**：`hybrid.py` 打分前滤掉本会话自写候选；`ECHO_SUPPRESS_ENABLED` 默认关（兰台 session 域检索本就按 session 圈定，默认开会清空会话内召回）；空 session 一律不过滤。
  - **MMR 多样性截断（票据 02，上游 M4）**：`mmr_select`（λ·相关 −(1−λ)·jieba token 冗余，λ 默认 0.7 fail-closed 夹取）；`MMR_ENABLED` 默认关，关闭时逐条退回按分截断（零回归）；选择结果进 explain 域。
  - **错误签名通道 errsig（票据 03，上游 M6）**：`lantai/retrieval/errsig.py` 单一真源正则（CamelCase + Error/Exception/Warning），写入与检索两侧共用 import 杜绝拷贝漂移；查询含报错签名时正文精确命中候选加有界 bonus（`ERRSIG_BONUS` 默认 0.10，0=关闭）。
  - **咀华（Juhua，会话精华萃取，票据 04，上游 session distill；命名登记 CONTEXT.md，韩愈《进学解》「含英咀华」）**：`POST /session/distill`（只提炼不落库可安全重跑；`store=true` 经 `add_memory` 完整闸门管线进向量库）；新泳道 `distill` 慢衰减（base_s=30）；LLM 不可用确定性降级（取该会话最长两条拼接）并标 `distill_mode=fallback`；情绪词表有界显著性 0.60~0.85；`DISTILL_ENABLED` / `DISTILL_MIN_MEMORIES`（默认 3）。
  - **写线活性探针（票据 05，上游写线断裂事故产物）**：司天新增 `ingest_liveness` 域——`ingest_conv_reads_24h`（带 session 的真实会话检索，后台巡检不算数）+ 24h 写入分列（会话/后台）；三态判据 `broken`→critical、`background_only`→high、`no_evidence` 如实不告警（刚装好就断线不能一路绿过去）；`retrieval_event` 补 `session_id`（schema v21 增量迁移）；新增 `scripts/check_ingest_wiring.py` 写读回环自查（只看 /add 返 200 不算数；SSRF 纪律：默认仅回环目标，`--allow-remote` 显式放行）。
  - **轨迹级奖励信用（票据 06 最小切片，上游 M1）**：`EpisodeRecord` / `EpisodeStep` 表 + `POST /evolve/episode/feedback` 按位置回传 + `episode_credit_weights` 纯函数（λ·均匀 + (1−λ)·归一化 γ 递减，和恒为 1）；**只登记不接检索权重**（上游默认权重 0 同款纪律，察窗攒数据再开）。
  - 新增测试 71 例（`test_v022_retrieval.py` / `test_distill.py` / `test_episode_credit.py` / `TestSessionOriginChain` / `TestIngestLiveness` 等），带开关特性一律开+关对照冒烟。

### Fixed
- **鉴权双轨断裂（P0）**：业务路由原先只走库内 Bearer / DEV MODE，环境变量 `API_KEY` 与 `X-API-Key` 从未生效；空库 + 非回环可零鉴权写记忆。现 `get_current_user` 统一为：`X-API-Key`（命中即 admin）→ 库内 Bearer → **仅回环且无 API_KEY 且空库**才 DEV MODE。
- **启动入口缺失（P0）**：`lantai-server`（`lantai.api.app:main`）此前无 `main()`；Dockerfile/README 引用不存在的 `api_server.py`。已补 `main()` 与薄 shim，Docker `CMD` 改为 `lantai-server`。
- **回声抑制语义修正（ADR-0046，整改票 04）**：旧「删光同 session 候选」与 session 域检索叠加，开启即清空会话内召回；改时间窗语义——仅抑制本会话 `ECHO_SUPPRESS_WINDOW_SECONDS`（默认 900，正数 fail-closed）内新写入的回声，窗口外同会话记忆照常召回；`created_at` 缺失不抑制（宁 miss 不脏写）。
- **RetrievalParams 覆盖路径统一 fail-closed（整改票 02）**：校验下沉 `__post_init__` 单一真源，`from_overrides` 显式覆盖越界/非法/非有限 λ、bonus、窗口参数一律回默认，不再绕过 default_factory 直通评分。
- **distill 泳道权限收窄（整改票 03）**：`DEFAULT_LANES` 补 `distill`（默认密钥/DEV 可写可召回）；精华路由校验调用者泳道集含 `distill` 才许落库（否则 403）；源记忆读取按调用者泳道集收窄（与检索出口同口径），受限部署不再可能跨泳道提炼。
- **认知中间件测试环境依赖（整改票 01）**：`test_cognitive_middleware` fixture 只隔离了 FastAPI 依赖注入，DEV MODE 库检查直连模块级会话工厂查真实库——本机库有 api_keys 行即 401；fixture 补模块级 `get_session` 隔离（五步诊断归档 `docs/memory-quality/review-remediation-v022-diagnosis-2026-09-18.md`）。
- **episode_credit 测试全局状态污染（整改票 05）**：`finally` 里把模块级会话工厂置 `None` 改为 `monkeypatch.setattr` 自动还原，杜绝后续测试顺序依赖。
- **v022 检索测试替身契约（整改票 04）**：向量替身不再「无视 top_k 返回全库」，按 filters 真实过滤 session/lane/domain；测试 helper 的 FTS 同步改为同事务写入（原 commit 后写入随 Session 关闭回滚，`memory_fts` 恒空）；换用真实 `Principal`。

### Added
- **司天（后台运行监控面板，ADR-0045）**:
  - 采集层 `observability/metrics.py`：进程内 `MetricsCollector`（最近请求环形缓冲 + 分钟级聚合桶），
    零第三方依赖采集 uptime/RSS/线程/CPU/fd，`normalize_route` 把 `/memory/mem_01J8…` 归一成
    `/memory/{id}` 杜绝高基数打散统计；
  - 遥测层 `observability/telemetry.py`：纯 ASGI `TelemetryMiddleware` + 采样落库器，
    补齐 ADR-0040 建表以来从未写入的 `OperationLog`（4xx/5xx 与慢请求必留，正常请求 1/N 采样，
    后台批量写 + 按 `MONITOR_RETENTION_DAYS` 清理），杜绝每请求一次 SQLite 写的写放大；
  - 聚合层 `ops/monitor.py`：`build_monitor_snapshot` 一次装配进程/存储/记忆/管道/调度/请求/
    安全/依赖八域事实，`evaluate_alerts` 13 条规则告警，`render_prometheus` 同快照文本出口，
    `safe_settings_view` 生效配置只读（密钥打码、DB 路径只留文件名）；未匹配路由的 404
    在指标中并为 `(unmatched 404)` 一桶（防扫描器打散端点排行），落库仍留真实路径；
  - 接口面 `api/routes_monitor.py`：`GET /monitor/overview|series|logs|config|prometheus` +
    `POST /monitor/workers/{name}/run`（复用 `worker_operation_service` 同名互斥）；
  - 前端 `ui/monitor.js`：悬镜工作台新增「司天监控」视图（指标卡 / 告警 / 服务与依赖 /
    纯 SVG 请求趋势 / 端点耗时排行 / 记忆管道水位 / 调度器与 worker 一键补跑 / 问题请求 /
    运行配置），零构建原生 ES Module，10 秒自动刷新且页面隐藏即暂停；侧边栏告警徽标走
    `?quality=false` 轻量轮询；
  - 口径统一：worker 逾期判定上收为 `core.scheduler.worker_staleness` 纯函数，
    案牍 `project_work_items` 改为调用同一实现（行为不变），杜绝两处规则漂移；
  - 命名正式登记：在 `CONTEXT.md` 登记「司天」（Sitian，出自司天监观天象察灾异），归档 ADR-0045。

### Fixed
- **控制台初始化中断**：`ui/app.js` 绑定了 index.html 中并不存在的 `#systemRefresh`，
  `bindEvents()` 在该行抛 TypeError，导致其后的器识/札记/演练场/档案库事件与全局快捷键
  全部未绑定、`loadQueue()` 也不执行——控制台打开即空转。补上该按钮并新增
  `tests/test_monitor_ui.py::test_every_dom_selector_exists_in_index_html` 静态契约测试
  （JS 里每个 `$('#id')` 必须在 index.html 或 JS 动态创建中存在）防回归；
- **服务重启即崩（P0）**：`start_scheduler()` 里 `ingest` / `evolve` / `forget` 三个
  `add_job` 漏了 `replace_existing=True`，而 jobstore 是持久化在同一个 SQLite 库的
  `SQLAlchemyJobStore`——首次启动正常，**之后每次启动都在 `start()` 抛
  `ConflictingIdError: 'Job identifier (ingest) conflicts with an existing job'`，
  服务对已存在的库再也起不来**（只能删库或手工清 `apscheduler_jobs` 表）。已补参数，
  并加 `tests/test_scheduler.py::TestSchedulerRestart`（真实调度器 + 真实文件 jobstore，
  连启两次）防回归；
- **退出路径连带崩**：`stop_scheduler()` 在调度器已停止时抛 `SchedulerNotRunningError`，
  改为幂等（未启动/已停止均静默返回）；
- **依赖缺失**：`jieba` 与 `rank-bm25` 是四路混合检索的词级通道，却从未写进
  `pyproject.toml`（`uv.lock` 亦无），干净环境 `pip install -e .` 后
  `import lantai.gate.conflict_rules` 直接 `ModuleNotFoundError`；已补声明。

## [0.21.0] - 2026-08-31 - 悬镜（Xuanjing · 兰台可视化管理控制台 Lantai Studio）

### Added
- **悬镜（全功能可视化记忆管理控制台，ADR-0038）**:
  - 前端架构升级（Lantai Studio）：重构 `/ui` 单页工作台，打造一站式人机协同记忆运维界面，彻底打破纯 MCP 命令行运维壁垒；
  - 器识与札记在线工作室（Persona & Scratchpad Studio）：支持在线查看、编辑并一键保存 L/G/E 人格基座与工作区即时便签；
  - 沉潜夜梦沉淀仪表盘（Consolidation Console）：实时查看最近沉淀审计报告、碎片聚类统计与修剪突触计数，支持一键触发夜梦沉淀；
  - 四路检索与探针演练场（Playground）：交互式输入 Query，实时拆解展示向量分、BM25 分、FTS5 字串匹配分、时效衰减乘数与【探颐】主动探针提示；
  - 界面与主题美化：支持「吉金」（青铜深色）与「漏窗」（园林浅色）双典籍主题自适应与移动端响应式布局；
  - 命名正式登记：在 `CONTEXT.md` 登记「悬镜」（Xuanjing，出自宝镜高悬意象），归档 ADR-0038。

## [0.20.0] - 2026-08-31 - 探颐（Tanyi · 记忆主动探针与自然交互消歧）

### Added
- **探颐（记忆主动探针与自然交互消歧，ADR-0037）**:
  - 核心服务 `probing_service.py`：基于 `detect_memory_probes` 扫描未决冲突账本（`ConflictEvent(status="open")`），在检索命中时自动生成温和的自然语言求证探针；
  - 上下文协同插桩：`format_probing_context` 将求证事项注入 Prompt `【探颐·待求证事项】` 区域，供 Agent 顺带发问；
  - 答复识别与闭环消解：`resolve_probe_response` 识别用户次轮自然答复（肯定/否定/纠正），肯定时自动消解冲突并更新记忆版本，记录 `MemoryCheckpoint` 快照；否定时自动归档废弃；
  - 接口面暴露：新增 REST `POST /probing/detect`、`POST /probing/resolve` 以及 MCP 工具 `probe_detect` / `probe_resolve`（MCP 工具总数扩容至 **55**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「探颐」（Tanyi，出自《易·系辞上》「探赜索隐，钩深致远」），归档 ADR-0037。

## [0.19.0] - 2026-08-31 - 沉潜（Chenqian · 闲时夜梦沉淀与折叠压缩）

### Added
- **沉潜（闲时夜梦沉淀与记忆折叠压缩，ADR-0036）**:
  - 核心服务 `consolidation_service.py`：基于 `find_consolidation_clusters` 自动扫描 `(domain, lane)` 分组下的高重合度碎片记忆群（$\ge 3$ 条）；
  - 概念提纯与折叠：调用 LLM 归纳提纯出 1 条高阶概括性主记忆，挂载 `source_ids` 溯源，并将原碎片状态置为 `consolidated`（折叠归档，主检索不再重复干扰）；
  - 衰减突触修剪：`prune_decayed_synapses` 自动将极度衰减（`decay_score < 0.05`）且无高采纳反馈的边缘噪音转入 `archived` 休眠；
  - 调度器集成：在 `scheduler.py` 注册每日闲时/夜间沉淀任务（北京时间凌晨 03:30 自动执行）；
  - 接口面暴露：新增 REST `POST /evolution/consolidate`、`GET /evolution/consolidate/report` 以及 MCP 工具 `memory_consolidate` / `consolidation_report`（MCP 工具总数扩容至 **53**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「沉潜」（Chenqian，出自《荀子》「沉潜以思」），归档 ADR-0036。

## [0.18.0] - 2026-08-30 - 贯珠 · 辨域 · 潜移 · 札记（四维借鉴闭环）

### Added
- **贯珠（基于图谱拓扑的二度语义联想与多跳召回，ADR-0035，借鉴 Cognee）**:
  - 核心服务 `graph_retriever.py`：基于 BFS 沿 `MemoryEdge` 拓扑进行 1~2 步（hop）关系扩散与隐式记忆联想；
  - 路径可解释性：联想结果包含跳数、关联关系、前驱节点与边置信度，过滤环路与已访问集合；
  - 接口面暴露：新增 REST `POST /search/graph_expand` 以及 MCP 工具 `graph_expand_search`（MCP 工具总数扩容至 **51**）；
  - 命名正式登记：在 `CONTEXT.md` 登记「贯珠」（Guanzhu，出自《汉书·景十三王传》「如贯珠焉」），归档 ADR-0035。
- **辨域（User-Session-Agent 三维硬隔离与域分治，ADR-0034，借鉴 Mem0）**:
  - 数据库字段扩展与迁移：`MemoryItem.domain`（user/session/agent）与 SQLite `v17` 幂等增量迁移；
  - 检索层隔离支持：`hybrid_search` 与 `_keyword_fallback` 支持精确 `domain` 过滤与跨域召回；
  - 接口面暴露：REST `POST /search` 与 MCP `search` 工具全面支持 `domain` 参数透传；
  - 命名正式登记：在 `CONTEXT.md` 登记「辨域」（Bianyu，出自《周礼·春官·宗伯》「以辨天地四时之域」），归档 ADR-0034。
- **潜移（异步摄取管道与非阻塞任务调度，ADR-0033，借鉴 Zep）**:
  - 核心服务 `async_ingest_service.py`：基于后台线程池与 `TaskRegistry` 任务注册表提供非阻塞摄取调度；
  - 毫秒级提交：对话提交立即返回 `task_id`（<10ms），后台静默完成提取、提纯（披沙）与去重；
  - 接口面暴露：新增 REST `POST /dialogue/async`、`GET /dialogue/tasks/{task_id}` 以及 MCP 工具 `dialogue_add_async` / `dialogue_task_status`；
  - 命名正式登记：在 `CONTEXT.md` 登记「潜移」（Qianyi，出自《文心雕龙》「潜移暗引，莫之能知」），归档 ADR-0033。
- **札记（Working Memory Scratchpad 工作区暂存夹，ADR-0032，借鉴 Letta / MemGPT）**:
  - 引入 `SessionScratchpad` 表与数据库迁移 `v16`（为 Agent 提供在对话中主动实时读写的小纸条区域）；
  - 核心服务 `scratchpad_service.py`：支持 `get_scratchpad`、`write_scratchpad` 与 `format_scratchpad_context`（上限 1000 字符，超长自动截断，宁 miss 不脏写）；
  - 协同注入：`inject_checkpoint_context` 联动支持在首轮 Prompt 中与「器识」人格基座与「底本」会话快照协同拼合注入 `【札记】`；
  - 接口面暴露：新增 REST `GET /scratchpad/{session_id}`、`POST /scratchpad/{session_id}` 以及 MCP 工具 `scratchpad_get` / `scratchpad_write`；
  - 命名正式登记：在 `CONTEXT.md` 登记「札记」（Zhaji，出自古籍读书摘记要点之木简小帖），归档 ADR-0032。


## [0.16.0] - 2026-08-30 - 更漏（Genglou）

### Added
- **考功（记忆价值演化与升降评定体系，ADR-0031，v0.16.4）**:
  - 核心服务 `kaogong_service.py`：基于长程使用反馈（`MemoryUsageFeedback`）与采纳率实现全库记忆功过评定；
  - 升降规则：高频高采纳记忆（`use_count >= 3, helpful_ratio >= 0.8`）上考晋升长期语义层（`tier="longterm", decay_class="semantic"`），高频低效记忆（`helpful_ratio <= 0.2`）下考降权，样本不足保持原状（宁 miss 不脏写）；
  - 接口面暴露：新增 REST `POST /evolution/kaogong`、`GET /evolution/kaogong/report` 以及 MCP 工具 `kaogong_eval`（MCP 工具总数扩容至 46）；
  - 命名正式登记：在 `CONTEXT.md` 登记「考功」（Kaogong，出自唐代吏部考功司，掌官吏功过品级考评），归档 ADR-0031。
- **沙汰阈值校准（ADR-0026，v0.16.4）**:
  - `CANDIDATE_MIN_CONFIDENCE` 默认值安全校准为 `0.15`，自动淘汰闲聊废话（conf=0.0）与残片，保障待审队列高质量。
- **披沙（候选记忆递归精炼 Refine 机制，ADR-0030，v0.16.3）**:
  - 核心服务 `refine_service.py`：针对模糊置信度（0.2~0.6）的候选记忆进行 LLM 指代消解、消除口语化废话、原子化提纯与置信度重估；
  - 严格降级保护（宁 miss 不脏写）：LLM 异常、超时或校验失败时优雅降级保持原始文本不变，绝不损坏原有数据；
  - 接口面暴露：新增 REST `POST /candidates/{id}/refine`、`POST /candidates/batch_refine` 以及 MCP 工具 `candidate_refine`（MCP 工具总数扩容至 45）；
  - 命名正式登记：在 `CONTEXT.md` 登记「披沙」（Pisha，出自《世说新语·德行》「披沙拣金，往往见宝」），归档 ADR-0030。
- **器识（Persona 人格基座 L/G/E，ADR-0029，v0.16.2）**:
  - 引入 `PersonaProfile` 表与数据库迁移 `v15`（支持言语风格 L、行为准则 G、认知底色 E 三层认知模型）；
  - 核心服务 `persona_service.py`：支持激活切换、多 Profile 管理、格式化 Prompt 上下文生成与纯函数防护；
  - 会话级联动：`inject_checkpoint_context` 支持与「底本」会话快照协同注入首轮 Prompt，赋予 Agent 恒定立身风骨；
  - 混合检索加权（Persona Boost）：`hybrid_search` 针对 preference 与 rule 分轨自动叠加 1.05x 偏好增益，并在 explain 中透明记录；
  - 接口面暴露：新增 REST `/persona/*` 路由端点与 MCP 工具 `persona_get` / `persona_set`（MCP 工具总数扩容至 44）；
  - 命名正式登记：在 `CONTEXT.md` 登记「器识」（Qishi，出自《新唐书·裴行俭传》「士之致远，先器识而后文艺」），归档 ADR-0029。
- **开发工作流标准化**: 确立《研发工作流规范》（`docs/development-workflow.md`），基于六阶段标准（需求立项拆解、5 步根因诊断、架构与命名治理、TDD 先导与核心函数不 mock 冒烟、代码审查门禁、版本收口与发布闸门），并作为 `AGENTS.md` 强制规则。
- **拾遗检索韧性与多级降级（ADR-0028）**: 
  - 混合检索 `hybrid_search` 嵌入异常防护：外部 Embedding API 鉴权 401/网络超时/连接中断时不挂死，平滑降级至 `_keyword_fallback` 本地 FTS5 + BM25 关键词检索；
  - 降级候选提取补充 SQLite LIKE 子串匹配，彻底解决 < 3 字符短词（如 "电脑"、"显卡"、"测试"）无法触发 FTS5 trigram 分词导致的零召回；
  - `SearchReq` 增加 `force: bool = False`，支持显式透传直接绕过闸门检索；
  - 「拾遗」正式登记 `CONTEXT.md` 词汇表（ADR-0013 意象池「拾遗」= 唐代谏官官职，取「拾遗补阙、失落必还」之意）。
- **察窗观察期滑动窗口（ADR-0027，v0.16.0）**: `scripts/reflect_observation_status.py` 支持 `reference_date` 参数，反思观察期由连续口径改为滑动窗口内合格天数统计。

### Fixed
- **相关性闸门短查询与自指校准（ADR-0028）**:
  - `_BASE_SELF_REFERENCE` 正则收录 "大哥" 等项目核心自指，使 "大哥电脑配置" 准确识别为自指；
  - 社交结束语 `NO_MEMORY_PATTERNS` 增强支持多词组合（如 "好的谢谢"、"好的好的"）；
  - 内容查询放行技术/领域专业词短查询（如 "什么是事件驱动架构"、"华硕天选三显卡"），消除武断的 15 字符硬门槛对短实词的误杀；
  - 修复 `tests/test_reflect_observation_status.py` 静态时间戳导致的滑动窗口老化失效。

## [0.15.2] - 2026-08-27

### Added
- **案牍控制台 Phase 1**: `/ui` 重构为单维护者记忆运营工作台，新增七类案牍只读投影、确定性分区/排序、详情检查器、批量拒绝/延期/整理、worker 对应重跑、吉金/漏窗响应式外壳；前端采用 FastAPI 同源托管 HTML/CSS/ES Modules，无构建步骤，五个旧控制台路由继续保留。新增 ADR-0025 与 `.scratch/console-workbench/` 规格票据。
- **候选延期**: `memorycandidate` 增加延期与单步撤销留痕，schema v14；支持 3/7 天延期，最长不超过首次创建后 30 天。
- **反思观察门槛可审计**: `reflect_run` 增加 `source`（scheduled/manual/unknown）并迁移至 schema v13；定时任务与 MCP 手动运行分别写入来源，旧记录保守标为 unknown。新增 `scripts/reflect_observation_status.py`：默认只读报告连续合格定时运行次数，`--check` 未满足 7 次时以失败码阻断发布准备。

### Changed
- **候选审批改为两阶段**: `candidate_review approve=true` 只创建 pending 提案，不再立即应用；最终写入必须通过提案裁决。REST、MCP、控制台与测试统一该语义；拒绝类裁决要求填写理由。
- **v0.15.2 代号登记**: 计划版本代号定为「绳墨」——《礼记·经解》「绳墨之于曲直」，对应本版的校准、门禁与收口；README 测试说明改为以 CI 为准，ADR 索引更新至 0024。

### Fixed
- **curator 零产出根因修复（A 遗留，2026-08-15）**: `REFLECT_CURATOR_SYS` 补显式空提案契约（"If nothing warrants a change, return exactly {\"proposals\": []}"）——实测 Qwen3-8B 在缺该指令时对严格 JSON 妥协返回 `{}`（零产出主因）；补指令后正常返回严格 JSON 且产出真实提案。`curate_failed`（2/3 运行）为网络瞬断偶发，已有空降级留痕。观察期校准从本次修复起重新积累有效样本
- **悬空链接清理（v0.15.2 D1）**: `reflection-module-spec/prompt` 对已删生成报告 `docs/memory-quality/2026-08-11.md` 的引用改为内联数字 + 修复指向（报告按生成归档策略移出 git）

### Changed
- **性能基线首份（v0.15.2 D2）**: `scripts/perf_baseline.py` 实跑——20 问全 200，P50=2062.8ms / P95=2089.7ms；延迟主因外部 embedding API（~2s/次），本地管线毫秒级；报告 `docs/memory-quality/perf-baseline-2026-08-15.md`（生成报告本地留档）
- **评测集 v3（80 case，v0.15.2 C3）**: `chinese_memory_cases.py` 50 → 80 case（typo×23 / fresh×18 / stale×14 / temporal×13 / superseded×12，5 类内扩不动 GATES/runner）；防漂移锁定测试（test_memory_quality_spec.py）计数自动跟随；规格文档/白皮书 8.3 同步；门禁实测 PASS
- **校雠实质新词扩展信号（ADR-0023，v0.15.1 C1）**: `classify_relation` 无新增值分支加扩展判定——旧锚点零丢失（dropped 空，改写是替换非扩展）+ 新增实质词 ≥ `DEDUP_EXTRA_ANCHOR_LIMIT`(2) → 判 **update 提案**（有刹车，不吞内容）——修复 ADR-0019 锚点比非对称（old⊆new 恒 1.0）导致的扩展事实误 merge 吞并；不扩技术名值类（列表漂移，宁 miss）。36 对回归不回归（改写对 dropped 非空）+ 新增扩展对/对照组 3 例
- **参商单字否定对候选探测（ADR-0024，v0.15.1 C2）**: `conflict_rules.check_negation_pairs`——token 级子串探测 是/不是、会/不会、能/不能、有/没有、要/不要 交叉命中 → **候选**（不落硬规则）→ `decision.py` 对该记忆调 LLM 矛盾检测裁决；LLM 判非矛盾/失败 → 放行（宁 miss）。jieba 并词（"我会"→一词）场景由此捕获；"开会" 类误候选由 LLM 澄清。既有回落路径与否定路径的 LLM 调用补 try/except 韧性（防御一致性）

### Fixed
- **反思校准口径修复（A 项收口，2026-08-15）**: `digest_worker._aggregate_reflection` 反思提案标识由 `candidate_id IS NULL` 收紧为 `decided_by == 'reflect'`（reflector 落 `decided_by="reflect"`，与 evolve auto / autodream 区分）——此前 evolve 自动提案误计为「反思提案」（真实库 16 条 duplicate-merge 被误计），校准输入污染。拒绝原因统计同口径。测试：`test_digest.py::test_non_reflect_proposals_excluded` 回归断言 + 既有反思用例种子同步 `decided_by="reflect"`
- **校准窗口竞态修复**: `collect_calibration_stats` 窗口边界秒级截断 + 1s 顶边过悬——微秒精度采样与写入同秒撞界致 `run_at < end` 偶发漏数（test_digest 配对 ~50% flaky，复现后修复，配对 20/20 稳定）
- **观察期数据门判定（8/15）**: 3 次运行 0 产出、2 次 curator LLM 失败 → 样本不足，`REFLECT_IMPORTANCE_POOL`(5.0) / `REFLECT_AUTO_APPLY_CONF`(0.7) / `REFLECT_MIN_CONFIDENCE`(0.5) 维持 dry-run 值（宁 miss 不脏写），`REFLECT_STALE_SCAN_ENABLED` 维持 False；观察期延长至 7 个完整运行日（先修 curator LLM 失败根因）。校准报告 `docs/memory-quality/reflect-calibration-2026-08-15.md`

### Added
- **底本闭环（ADR-0022，v0.15.0 B 项）**: shell_hook serve 协议新增 `{"type":"checkpoint"}`（会话启动注入上次会话五段快照，独立通道不占每轮召回预算）与 `{"type":"checkpoint_write","session_id","blocks"}`（插件会话结束落快照，同库同语义）；Hermes 插件 `pre_llm_call` 会话首轮注入底本（与查询长度/触发词无关，每会话一次，有界标记集），`on_session_end` 用会话缓冲构建五段块落库（宁 miss：在做=末条消息、下一步/决策/待办句式命中才填、工作区恒空）；`build_session_blocks` 纯函数可测。测试：serve 协议分支真实库（test_checkpoint_service.py）+ 插件首轮/落块/纯函数（test_hermes_plugin.py，子进程 mock）
- **底本五段会话快照（ADR-0021，Fog 项收口）**: `lantai/services/checkpoint_service.py`——五段块（在做/下一步/工作区/决策/待办，移植 aiduMEM checkpoint.py 窄版），上下文压缩时 `write_session_checkpoint` 写入、下次会话启动 `inject_checkpoint_context` 注入（>30 天自动标注陈旧）；同 session 重写即替换、保留最近 5 会话（`CHECKPOINT_MAX_SESSIONS`）、块 <3 字符不落 / >600 截断（宁 miss 不脏写）；schema 迁移 v11→v12（session_checkpoint 表）。REST `POST /checkpoint` + `GET /checkpoint/latest` + `GET /checkpoint?session_id=` + `POST /checkpoint/cleanup`（受保护）；MCP `checkpoint_write`/`checkpoint_latest`（工具 40→42）。「底本」登记 CONTEXT.md 词汇表（ADR-0013）。测试 `tests/test_checkpoint_service.py`（纯函数不 mock + 真实 SQLite）+ 迁移断言 v12
- **中文记忆评测集 v2（50 case）+ 纳入 CI（路线图收口）**: `lantai/eval/chinese_memory_cases.py` 13 → 50 case（typo×15 / fresh×12 / stale×8 / temporal×8 / superseded×7，dataset 名 chinese-memory-v2）；错别字 case 统一「去首字」模式保证 FTS trigram AND 链确定性命中，陈旧 case 按 lane 半衰期（chat 90d / preference 200d）保证归档；`GATES` 门槛不变，`run_forgetting_quality.py --check` 实测 PASS；新增 `.github/workflows/tests.yml`——push/PR 全量 pytest + 遗忘质量门禁（供应链纪律：actions 锁 SHA）。测试 `test_forgetting_quality.py` 样本计数 13→50
- **salience 冲突降权 + 反义词碰撞（ADR-0020，Fog 项收口）**: `gate/conflict_rules.py` 新增 `check_antonyms`（jieba 词级互斥，8 对默认反义词，settings 可配；单字否定对因 jieba 并词默认不启用）——"喜欢咖啡"vs"讨厌咖啡"、"支持 X"vs"反对 X" 零 LLM 确定性命中；`gate/decision.py` 分流：确定性冲突命中低 salience 旧记忆（importance < 0.4）→ 降权 0.2（Checkpoint 可回滚）+ ConflictEvent kind=salience_demote status=resolved + 候选放行走提案链（有刹车）；高 salience / LLM 矛盾维持 archive_conflict 人工裁决。测试：反义词双向/词级不误伤/开关 + 降权/高 salience/LLM 不分流（test_conflict_rules.py，规则层不 mock）
- **autodream 7 天周期蒸馏（Fog 项收口）**: `lantai/workers/autodream_worker.py::run_autodream_scheduled`——后台周期蒸馏落 pending 提案（decided_by="autodream"，人工闸门裁决，宁 miss 不脏写）+ `record_run("autodream")` 可观测；scheduler 注册 interval job（`AUTODREAM_CRON_DAYS`=7 默认，settings 可配，AUTODREAM_ENABLED 门控）。测试：scheduler 注册断言（interval/days=7/开关）+ worker 真实库落库冒烟（test_scheduler.py / test_autodream.py）
- **arm64 Docker 镜像（Fog 项收口）**: `.github/workflows/ci.yml` `platforms: linux/amd64,linux/arm64`——tag 推送构建双架构镜像

### Changed
- **校雠三态去重升级（ADR-0019，结构判别）**: 实测（36 对 / 3 类中文样本，真实 bge-m3）证明单一余弦阈值无法分离 merge/update——更新类 5/12 被误判 merge 静默吞掉新值。升级为两相位：① 余弦预筛（提取前，≥ `DEDUP_PRESCREEN_MERGE`=0.95 直合零 LLM、< 0.65 insert）；② 中带提取后结构判别（`lantai/gate/relation.py::classify_relation`，锚点 + 归一化值规则，中带 LLM 兜底、失败降级 insert——宁 miss 不脏写）。`DEDUP_MERGE_THRESHOLD` 默认 0.80 → 0.90（fastpath 路径阈值）；`DEDUP_STRUCTURAL_ENABLED` / `DEDUP_STRUCTURAL_LLM_ENABLED` / `DEDUP_ANCHOR_HIGH` / `DEDUP_ANCHOR_LOW` 新增。回归样本 36 对入 `tests/test_dedup_relation.py`（规则层不 mock）+ `tests/test_dedup_flow.py` 两相位接线。票据：白皮书路线图「去重阈值实测校准」，prototype 见 `.scratch/dedup-threshold-calibration/`

## [0.14.0] - 2026-08-13

- **版本代号「缥缃」**: 丝帛书衣，代指书卷——贴合兰台档案/书卷定位；登记于 `CONTEXT.md` 词汇表与 ADR-0013 版本代号登记。

- **版本上传规范流程（发布门禁 + 人工闸门）**: `docs/release-process.md` 定义从版本号收口到 GHCR 镜像验证的完整流程；`scripts/release_check.py` 只读门禁核对 pyproject / README / FastAPI / MCP serverInfo / CHANGELOG 版本一致，并检查 Git 分支 / 工作区干净 / tag 不重复 / origin 存在（`--online` 时同时查远程 tag）；存量版本号不一致收口到 v0.3.7（FastAPI version / MCP serverInfo / README Docker 示例）。发布上传（push tag）保持人工闸门，Agent 只检查/准备。
- **v0.14 双主题换肤（吉金 + 漏窗，2026-08-12，承接 v0.13 书卷换肤赛道）**: 五式预览（玄墨/天青/书衣/吉金/漏窗，见 `.scratch/v0.14-style-preview/`）用户选定吉金+漏窗，按 ADR-0013 登记命名后落地——`lantai/api/routes_ui.py` 六个面板全局双主题（`[data-theme]` CSS 变量覆盖层，零侵入）：吉金（默认）=玄青拓片底 `#1c2430` / 铜绿 `#3e7a6b` / 鎏金 `#b08a3e` / 朱砂 `#a33b2e` + 云雷纹饰带 + 楷体/宋体；漏窗=绢黄底 `#e9dfc6` / 黛青 `#2f4f4f` / 石绿 `#4e8d7c` / 竹青 `#6f9e8a` + 回纹画框 + 月洞门形卡片 + 行楷/宋体；右上角主题切换钮（localStorage `lantai-theme` 持久化 + `?theme=louchuang` 深链）；记忆星图 SVG 配色改读 CSS 变量（lane/edge/场景/来源/label）随主题重绘；五式名已登记 `CONTEXT.md` 词汇表与 ADR-0013 映射表。UI 面板测试 23 例全绿。票据 01

- **v0.13 书卷·中国色换肤（2026-08-12，借鉴 zhongguose 全谱 526 色）**: 全局 CSS 变量换肤（汉白玉底 `#f8f4ed` / 象牙白卡 / 油绿墨 `#253d24` / 竹绿主色 `#1ba784` / 赭石·靛青·夹竹桃红·瓦松绿·玫瑰灰六 lane 色 / 琥珀黄·朱红 edge 色 / 8 色场景调色板）；**记忆星图防重叠布局重写**（`lantai/api/routes_ui.py::layout`）：画布 1000×700 → 1800×1300，场景组按成员数比例分槽 + 5 层半径（每成员 +26），独立记忆每环 8 个、半径 330 起每环 +40，来源节点最外环角度排序 + 最小 4° 贪心间隔，标签白描边 + 10 字符截断；真实数据 40 节点 0 重叠（minD 36.3px，旧版 34 对重叠 minD 2.7），90 节点压力数据同样 0 重叠 0 出界。票据 01
- **v0.12.1 修复（2026-08-12）**: 根路径 `/` 由 404 改为 307 跳转 `/ui` 控制台——浏览器直接打开 `http://127.0.0.1:8767/` 即可进站（此前根地址 404 表现为「网站打不开」）。
- **v0.12 目识·截屏入忆（目识闭环，借鉴 aiduMEI tools/shot.js 显式触发思路）**: `scripts/screenshot_memory.ps1`——剪贴板截图（Win+Shift+S）或 `-FromFile` 图片 → PNG → base64 data URI → 既有 `POST /add media_url` 通道（title/lane/BaseUri/ApiKey 参数化，`-DryRun` 只构造不写库，pwsh7 MTA 自动 STA 重入）；`validate_media_url` 增强 data URI 严格校验（MIME 白名单 png/jpeg/webp/gif、base64 严格解码、解码后 ≤ `MEDIA_DATA_URI_MAX_BYTES`=10MB，宁 miss 不脏写）；`AddMemoryReq.media_url` max_length 2000 → 15_000_000（截屏 data URI 可达 MB 级字符）。测试 `tests/test_vision.py` 10 例（+3：data URI 规则/超限/schema 长 URI）。票据 01
- **v0.11 烽燧 记忆广播链（借鉴 aiduMEI memory_broadcast /recall_chain 窄版）**: `lantai/ops/recall_chain.py::build_recall_chain(seed_text, max_depth=3, branch=3, min_score=0.3, total_max=20)` 纯函数——seed 逐层 BFS 传播：每层以当前 seed 集调 `hybrid_search(top_k=branch*3, use_rerank=False)` 后链内按分数降序取 branch 条，命中记忆的 content 作下一层 seed；入选需 score≥min_score、非自匹配（文本归一化相等或 jieba 词集合余弦≥0.9，锚点整链排除）、id 跨层去重、总量封顶；单条搜索失败只缺层不阻断（宁 miss 不脏写）；`validate_chain_params` REST/MCP/纯函数三处共用，非法参数抛 ValueError 不静默修正；REST `GET /recall/chain`（只读）+ MCP `recall_chain`（工具 39 → 40）；明确不吸收：作者版 workspace 冷记忆自动清理（兰台 archived 语义已有）、J-lens 整包（search_trace/recall_report 已覆盖）、Ignition 双路径（trace 体系已覆盖）。测试 `tests/test_recall_chain.py` 7 例（真实 SQLite+FTS + 本地 ngram 嵌入 + 假向量库，仅替换外部网络；BFS/去重/自匹配/封顶真实执行）。票据 01
- **v0.10 目识 Vision 多模态（借鉴 aiduMEI v18.3「多模态感知纪元」）**: `/add` 与 MCP `add` 支持 `media_url`（仅 http/https/data，`validate_media_url` 白名单校验，兰台不直接 fetch 图片零 SSRF 面）；`VISION_MODEL` 空时回退 `LLM_MODEL`，`vision_caption` 复用单一 LLM 网关（OpenAI 兼容 chat.completions + image_url，temperature=0.1 / max_tokens=500）；`build_vision_memory`：content 空 → caption 作正文，非空 → 存 `metadata.vision`，失败抛 ValueError 不落失败文本（宁 miss 不脏写）；provenance 记 `vision-caption` + 附加字段（media_url / vision_model）；content/media_url 二选一校验（同给 / 皆空 / <10 字拒绝）。明确不吸收：作者版失败落「图片解析失败」字符串（脏写）。测试 `tests/test_vision.py` 7 例（真实 SQLite+FTS 全链路，仅 mock Vision 外部网络）。票据 01
- **v0.9 code-review 两轴修复收口（2026-08-12）**: `/ui/map` 补 `info` 变量定义（悬停详情 ReferenceError 硬 bug）、renderStats 拼接优先级修正、死代码 `Math.min(13,11)` 清理；scene 成员同色聚簇（场景调色板，spec 原文语义兑现）；点击记忆节点跳 `/ui/recall?q=label`（recall 页支持 `?q=` 预填自动检索，「点击跳档案检索」落地）；limit 校验提取 `ops/graph.validate_graph_limit` 三处共用（REST/MCP/纯函数，build_graph 非法 limit 抛 ValueError 不静默钳制）；`graph_route`/`build_graph`/`get_graph` 补类型注解。票据 01（code-review 收口）
- **反思运行可审计（v10）**: `ReflectRun` 表落库每次反思运行（水位/跳过/产出/LLM 失败/异常，idle 与异常不静默），`run_reflect_once` 异常留痕后原样抛出（调度器重试前可查）；校准报告新增运行记录节（运行次数/空闲/异常/LLM 失败/产出提案），DB 增量迁移 v9 → v10。票据 observability 02
- **重构（收口）**: verbatim 直存共用构造器 `build_verbatim_item`（`add_raw_memory` 与冷启动导入同源去重）；ACL 兜底 lane 改读 `RAW_MEMORY_DEFAULT_LANE`；digest 置信桶边界移入 settings（`DIGEST_CONF_BUCKETS`，ADR-0002 零硬编码）；`import_session_jsonl` 的 `would_import` 统一预览口径（真实模式不随 ingest 错误缩水）
- **MAP 记忆星图（v0.9，借鉴 aiduMEI v18.3.0 MAP 面板窄版）**: `lantai/ops/graph.py::build_graph(session, limit)` 纯函数（零 DB 零 LLM）——节点 = active 记忆（仅参与 MemoryEdge 或属 scene 才入选，孤立记忆不上图）+ 参与边的来源文档 RawDocument（doc_*，带 title/url，出处可溯）；链接 = MemoryEdge（supports 绿 / refines 蓝 / contradicts 橙 / supersedes 红），两端在入选集合才保留（跨池边、指向 archived/池外端点丢弃），scene 名称映射 + node_type/lane/relation 统计；REST `GET /graph`（受保护，limit∈[1,500]）；MCP `graph_view`（工具 38 → 39，只读）；`/ui/map` 零依赖内联 SVG 放射布局（6 lane 扇区 + scene 聚簇 + 来源文档外环矩形贴邻接记忆 + 悬停详情 + 点击记忆跳档案/点击来源开 URL，无外部请求）；`/ui` 入口第五面板。明确不吸收：layer1_selfcheck 容量>80% 自动合并（违背宁 miss 不脏写）、instinct_graduation 自动毕业删原文（v0.7 crystal 已覆盖）。票据 01
- **review 修复（两轴 code-review 收口）**: /import/jsonl 补 lane 级 ACL 校验（绑定 agent 越界 lane 行记 errors 不落库，宁 miss 不脏写）；verbatim 导入时间戳统一归一化为 naive UTC（与摄取链同语义，digest 等 naive 区间比较不再静默偏移）；digest 反思统计统一 created_at 窗口并加 other 兜底（合计恒等于当日提案数）；erify_agent 类型标注修正；settings 注释错贴修复；	est_acl 检索用例改真实 SQLite 检索（不 mock 内部逻辑）；v8 迁移断言适配 v9 迁移链。
- **MCP 工具扩容（第二波，借鉴 aiduMEI v18 工具面 37 个反查兰台已有服务，21 → 28）**: 新增 `mem_recent`（最近记忆只读，按更新时间倒序）、`mem_stats`（overview 聚合：总数/分布/待审候选/检查点/待审提案）、`mem_health`（深度健康：SQLite + 向量存储，不触发外部 LLM）、`autodream_report`（蒸馏预演 dry-run 不写库）、`autodream_trigger`（执行一轮蒸馏落 pending 提案，宁 miss 不脏写）、`proposals_list` / `proposal_decide`（待审提案查看/裁决，approve 先落 Checkpoint 可回滚、reject 记 decision_reason）；`tests/test_mcp.py` 追加 8 例（不 mock 冒烟：真实 SQLite+FTS，仅 mock embedding/向量存储）；明确不吸收：`mem_delete`/`mem_delete_all`（硬删除无审计链）、`mem_update`（原地编辑以 Checkpoint 回滚替代）、`session_*`/`code_*`/`crystals_*`/`knowledge_tree`（会话归宿主、Code Graph 正交、树状/结晶为后续赛道）。票据 09
- **工具面第三波（v0.8，作者 aiduMEI 37 工具反查收尾，34 → 38）**: `reflect_run`（包装既有 `reflector.run_reflect_once`——反思本轮唯一缺失入口，Agent 可主动触发，高置信 auto-apply / 中风险 pending）、`mem_usage`（`ops/usage.collect_usage` 服务提取，REST `/usage` 与 MCP 共用，7 天每日新增缺日补零）、`core_memory_get`（核心记忆块只读）、`verbatim_search`（原文直存专用检索通道，FTS+向量不进混合召回）；明确不吸收终判：`mem_update`/`mem_delete`（无审计链）、`mem_observe`（add_dialogue 覆盖）、`mem_persona`（core-memory identity 覆盖）、`session_*`/`code_*`（归宿主/正交）。票据 01
- **记忆分类树（v0.7，借鉴 aiduMEI TreeMemory 窄版）**: `lantai/services/tree_service.py`——`MemoryNode` 父子表 + `node_path` 唯一路径 + depth 前缀查询，`memoryitem.tree_path` 显式挂载（v9 增量迁移，前缀统计不靠名字匹配）；纯函数 `validate_node_name`/`build_node_path`/`compute_attachments`（/a 不误匹配 /ab）；REST `GET /tree` / `POST /tree/nodes` / `GET /tree/subtree` / `POST /tree/assign|unassign`；MCP `tree_view`/`tree_add`/`tree_assign`（工具 28 → 31）；父缺失/重名/非法名一律 422（宁 miss 不脏写）。票据 01
- **技能结晶（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）**: `lantai/services/crystal_service.py`——检测复用 autodream 聚类（同 lane + 共享关键词，min_size=3，排除 general/chat 噪声 lane），簇 → `SkillCrystal` candidate（procedure 只记摘要不塞全文）；Mímir 铁律：只产候选，人工裁决 `POST /crystals/{id}/decide` approve 必须带非空 steps（宁 miss 不脏写）→ 落成 Skill 资产（复用 create_skill），reject → archived + reason；幂等 upsert（skill_name 冲突 hit_count+1）；settings `CRYSTAL_*` 三项；REST `/crystals` + `/crystals/detect`；MCP `crystals_list`/`crystals_detect`/`crystal_decide`（工具 31 → 34）。票据 02
- **记忆 Wiki（ADR-0017，借鉴 TencentDB Agent Memory LLM-Wiki ingest-v2 窄版）**: `lantai/services/wiki_service.py`——场景/技能 → `docs/memory-wiki/` 页面（frontmatter + 成员 + 相关场景 `[[wikilink]]`）+ `index.md`（按类型分组稳定索引）+ `overview.md` 综述（LLM 优先，失败/关闭确定性兜底）；`run_wiki_update_once` 幂等增量维护（过期页自动清理）；`mem_sync` 升级为 scene+digest+wiki 三件套；CLI `scripts/run_wiki.py`（--no-llm/--json）；MCP `wiki_read` 下钻（工具 20 → 21）；settings 新增 `WIKI_*` 六项；`tests/test_wiki.py` 11 例（纯函数不 mock + 真实 SQLite/tmp_path 集成）
- **上下文卸载（ADR-0016，借鉴 TencentDB Agent Memory offload_server/compact 窄版）**: `lantai/services/offload_service.py`——超长记忆（`SHELL_HOOK_OFFLOAD_CHARS` 默认 2000）全文落 `docs/memory-offload/{memory_id}.md`（`OFFLOAD_OUTPUT_DIR` 可覆盖），Shell Hook 上下文只注入「摘要 + 全文路径」行，需要时经 MCP `offload_read` 取回完整原文（白名单文件名 + 目录内路径校验防穿越）；落盘失败静默降级为截断注入，截断指南附 offload_read 提示；MCP 工具 19 → 20；`tests/test_offload.py` 8 例（纯函数不 mock + 真实 tmp_path/SQLite 集成）
- **中文命名体系（ADR-0013）**: 正式名「有出处、有意义、有登记」——命名层级 L0–L4 + 三大意象源（官职/典籍/器物）+ 功能域映射表（候选意象：直书/拾遗/佐证/更漏/参商/校雠/底本/拟议/起居注/卷宗/法门/三省/测候/目次/尘封）；新名称必须先登记 `CONTEXT.md` 词汇表；AGENTS.md 新增命名纪律
- **MCP 客户端矩阵（多客户端接入合规）**: `docs/mcp-client-matrix.md`——Claude Code / Cursor / Gemini CLI / Codex / Hermes 五端接入指南 + 15 工具清单 + 每端验证清单（tools 元数据 / description / inputSchema / ping+initialized 通知 / tools.call 缺参 -32602）；`tests/test_mcp.py` 追加 3 条标准合规测试
- **检索透明（supersedes explain 降权标记）**: `hybrid.py::_apply_supersedes_order` 新增 `breakdowns` 参数——explain 记录 `superseded_by`（新值 id 列表）+ `demoted: True`，向量主路径 / rerank / FTS 兜底三处调用点统一接入；修复 superseded_by 误记分数的 bug（改用 `superseder_ids`）；`tests/test_fts_integration.py::test_supersedes_explain_marks_demotion` 端到端断言
- **autodream 蒸馏（后台记忆合成 → 待审提案）**: `lantai/evolution/autodream.py`——同 lane + 共享关键词贪心聚类（确定性、min_size 过滤），`plan_distillation` 新值在前 + 去重 + 置信度随簇大小递增（0.5 + 0.15*(n-1)），`run_autodream_once` dry-run 或落 pending 提案（低置信度进 skipped，宁 miss 不脏写）；`scripts/run_autodream.py` CLI；settings 新增 `AUTODREAM_ENABLED` / `AUTODREAM_MIN_CLUSTER` / `AUTODREAM_MAX_DAILY` / `AUTODREAM_MIN_CONFIDENCE`；4 个不 mock 冒烟测试
- **记忆概览 CLI（只读聚合，一眼看清现状）**: `lantai/ops/overview.py::build_overview/get_overview`——记忆总数 / active / archived 按 lane 与 decay_class 分布、待审候选（pending_review）积压、检查点版本数、待审提案数；`scripts/memory_overview.py` Markdown / JSON 双输出；`tests/test_overview.py` 真实临时库 3 例（不 mock 聚合逻辑）

### Added
- **lane 级 ACL（按 agent_id 绑定 lane 集，借鉴 TencentDB Memory Hub Fixed Binding 窄版）**: `lantai/core/acl.py`——`allowed_lanes` / `lane_allowed` / `filter_results_by_lanes` 纯函数 + `verify_agent` FastAPI 依赖；settings `AGENT_LANE_BINDINGS`（空 = 不启用，默认关闭零行为变化）；启用后受保护端点强制 `X-Agent-Id` 且已绑定（缺失/未绑定 403），`POST /search` 结果按绑定 lane 收窄（兼容 memory.lane 与 FTS 兜底两形态，宁 miss 不放行），`POST /add` / `POST /add/raw` 越界 lane 拒绝落库。测试 `tests/test_acl.py`（7 例，纯函数不 mock + 路由 403/过滤接线），票据 08
- **冷启动导入（历史会话 JSONL 批量原文直存，借鉴 TencentDB Agent Memory 冷启动导入）**: `lantai/services/import_service.py`——`parse_import_lines` 纯函数逐行解析（content 必填，created_at/updated_at ISO8601 保留原始时间戳，lane/tags 可选，非法行记 {line, reason} 不静默修正）；verbatim 直存（sha256 幂等去重，embedding/向量索引失败不阻断落库，FTS 可检索）；REST `POST /import/jsonl`（受保护，空文本 422）+ `scripts/import_jsonl.py` CLI。测试 `tests/test_import_jsonl.py`（6 例，纯函数不 mock + 真实临时 SQLite 仅 mock 外部依赖），票据 07
- **冷启动导入·对话链（历史会话 JSONL → 摄取链 + 时间戳继承，借鉴 TencentDB Agent Memory L0 + v2.0.1 时间戳修正）**: `lantai/ingestion/import_service.py`——L0 会话格式（{role, content, timestamp[, session]}）经 `scripts/run_import.py` 批量喂既有对话摄取链；`ingest_dialogue(created_at=...)` 透传原始时间戳（RawDocument.fetched_at / MemoryCandidate.created_at），provenance.prompt=dialogue-session-import，promoter 按 import provenance 把 created_at 继承到 MemoryItem（时间线不压平；非导入路径不覆盖）；--dry-run 零写库预览，`IMPORT_MAX_LINES=5000` 防护。测试 `tests/test_import.py`（8 例，纯函数不 mock + 真实 SQLite/tmp_path + 演化链对照），票据 01，ADR-0018
- **VAULT 档案控制台（锦囊队列 + 档案浏览 + 衰减概览，借鉴 aiduMEI v18.2 控制台）**: `lantai/services/memory_service.py::build_memories_page`（纯函数，只读分页 limit∈[1,100]/offset，lane/status/decay_class/memory_type 过滤，updated_at 新→旧 + id 稳定排序，content 截断带省略号）+ `list_memories`；REST `GET /memories`（受保护）；`/ui/vault` 零依赖静态页——总览卡片（总数/active/archived/待审锦囊）、锦囊待审队列（页内采纳/驳回裁决 `POST /candidates/{id}/review`）、档案表格（过滤 + 分页）、衰减概览（by_decay_class/by_lane 条形图，`/stats` 新增 by_decay_class 聚合）；`/ui` 入口页第三个面板。测试 `tests/test_vault_panel.py`（5 例，纯函数不 mock 直调真实临时 SQLite），票据 06
- **EVOLVE 检索质量看板（借鉴 aiduMEI v18.2 控制台）**: `lantai/observability/recall_report.py::recent_retrieval_events`（最近 N 条检索事件，新→旧，limit∈[1,100]）；REST `GET /retrieval/recent-events`；`/ui/evolve` 零依赖静态页——总览卡片（真实查询/零召回率/token 粗估/场景命中率）+ 按 lane/意图分布条形图 + 事件流表格；`/ui` 改为双面板入口页。测试 `tests/test_evolve_panel.py`（5 例），票据 05
- **追忆漏斗控制台（RECALL 面板，借鉴 aiduMEI v18.2 控制台）**: `lantai/api/routes_ui.py`——零依赖静态页（内联 CSS/JS，无 node/打包），`GET /ui/recall` 公开托管，页内调 `POST /search?trace=true` 渲染 意图→向量→衰减→(重排)→最终 的召回漏斗（每步耗时/候选数/分数区间）+ 闸门裁决 + 结果列表；API Key 可选（localStorage）；`/ui` 307 重定向。测试 `tests/test_ui_recall.py`（2 例冒烟），票据 04
- **Obsidian 双链 + verbatim 专用检索（Ticket 02，借鉴 aiduMEI v18.3）**: `lantai/services/obsidian_service.py`——`extract_wikilinks()` 纯函数解析 `[[页面]]`/`[[页面|别名]]`（忽略 `[[#锚点]]`）；`sync_obsidian_note()` 笔记原文零 LLM 直存（复用 P0-1 `add_raw_memory`，content_hash 幂等），双链词与笔记标题沉淀为实体（`memory_type="entity"`，不建索引不参与召回）并建 `MemoryEdge(relation="links")`，重复推送实体/边幂等；REST `POST /obsidian/sync` + `GET /verbatim/search`（专用通道）；settings `VERBATIM_IN_RECALL`（默认 false，verbatim 不进混合召回，hybrid 向量/FTS 兜底双路径过滤）；MCP `obsidian_sync`。测试见 `tests/test_verbatim_obsidian.py`（5 例不 mock 冒烟）
- **provenance 提取来源（记忆可溯源，借鉴 TencentDB Agent Memory Roadmap）**: `lantai/core/provenance.py::make_provenance` 记录「哪套 prompt / 哪个模型 / 何时产出」；`MemoryCandidate` / `MemoryProposal` / `MemoryItem` 补 `provenance` JSON 列（`user_version` 5→6 增量迁移，老库零丢失）；四个提取入口统一填充（LLM 提取/论文 → extract-v1、memory fastpath → fastpath-direct、dialogue fastpath/闲聊 → dialogue-fastpath/dialogue-chitchat）；proposer → promoter 链路继承同源，最终记忆可回答"谁产出的"；记忆概览新增 `provenance_by_prompt` 分布。决策见 [ADR-0015](docs/adr/0015-provenance.md)
- **mem: 会话指令（MCP 命令式维护，借鉴 TencentDB Agent Memory mem-command）**: `lantai/services/mem_command.py`——`mem_help`（命令表纯函数）/ `mem_sync`（scene 增量聚类补跑 + 今日 digest 重算，子步骤异常不阻断）/ `mem_create_skill`（零 LLM 结构化落库：memory_type="skill" + structure.steps + decay_class="procedural"，sha256 幂等去重，进向量+FTS 可被 `## Skill` 块注入）；MCP 新增 `mem_help` / `mem_sync` / `mem_create_skill` 三个工具（15→18）；校验失败 -32602（宁 miss 不脏写）。决策见 [ADR-0014](docs/adr/0014-mem-command.md)
- **scene 增量聚类（ADR-0012 后续，借鉴 TencentDB Agent Memory L2 场景层）**: `MemoryScene.centroid` 质心落库（`user_version` 4→5 增量迁移，老库零丢失）；`rebuild_scenes` 构建时同步落质心（`_mean_vector`）；纯函数 `incremental_cluster`（复用 `cosine_sim`，cosine ≥ `SCENE_CLUSTER_THRESHOLD` 并入最相似场景，未命中保持无 scene_id——宁 miss 不脏写）；`assign_new_memory` 新记忆并入既有场景并刷 heat/member_count（零写放大）；消化期 `run_evolve_once` 末尾自动补跑（`SCENE_LAYER_ENABLED` 门控）+ `POST /scenes/assign` 手动入口
- **零召回率监控 + token 成本估算（可观测性，借鉴 TencentDB Agent Memory）**: `RetrievalEvent` 补 `scene_ids` / `estimated_tokens`（`user_version` 3→4 迁移）；`lantai/observability/recall_report.py` 提供 `estimate_tokens`（CJK 按字、其余 4 字符/词元，零依赖粗估）与 `recall_report(days)` 窗口聚合——排除系统噪音的零召回率、按 lane/intent 分组、场景命中率（配合 scene 层）、token 总量/均值；`log_retrieval` 埋点落 scene_ids（去重）与 token 估算；入口 REST `GET /retrieval/recall-report` + MCP `recall_report`；窗口默认 `RECALL_MONITOR_WINDOW_DAYS=7`
- **scene 聚合层（ADR-0012，借鉴 TencentDB Agent Memory L2 场景层）**: `MemoryScene` 表 + `MemoryItem.scene_id`（`user_version` 2→3 增量迁移，老库零丢失）；`scene_service` 确定性 embedding 聚类（cosine ≥ `SCENE_CLUSTER_THRESHOLD`，单成员簇不建场景）＋ LLM 批量命名/摘要（失败降级代表 key，宁 miss 不脏写）；`POST /scenes/rebuild` 幂等全量重建，heat = 成员 `use_count` 求和（零写放大）；shell_hook `build_context` 命中场景成员时导航块优先注入（`## Scene: 名称（热度 N，成员 M）` + 摘要 + 成员 key，渐进式披露），详情用 MCP `scene_get` / REST `GET /scenes/{id}` 下钻，`scenes_list` 浏览；`SCENE_LAYER_ENABLED` 默认关
- **Schema 版本化迁移（v0.6 Ticket 01，借鉴 aiduMEI v18.3 Fast-Update）**: `lantai/storage/db.py` 引入 `PRAGMA user_version` 增量迁移链——`CURRENT_SCHEMA_VERSION=2` + `apply_migrations()` + `_ensure_column()`，把原有手写幂等 ALTER（memoryitem.decay_class / retrieval_event.is_system_noise / memorycandidate.review_due_at）收口为版本化流程；老库自动基线 v1→v2，异常只记日志不阻断启动；`tests/test_migrations.py` 5 例不 mock 冒烟测试（空库/全新库幂等/缺列老库补齐+数据零丢失/重复启动 no-op/预版本化库）
- **遗忘质量离线门禁（CI / 发布自证）**: `lantai/eval/offline.py::run_offline_eval`——临时 SQLite + 真实 FTS5 建表 + 仅 mock 外部依赖（embedding / 向量存储 / 意图 LLM），真实执行 种子→遗忘→检索→指标→清理；`check_gates` 断言五维门槛（stale=0 / typo=1 / fresh=1 / temporal=1 / superseded=1），残留只报告不设门槛（诚实测量）；`scripts/run_forgetting_quality.py --check` 门禁模式 FAIL 退出码 1，可直接挂 CI
- **中文记忆评测集 v1 发布稿**: `docs/memory-quality/chinese-memory-v1.md`——评测集规格（13 case / 命名空间隔离 / trigram 词边界约束）、六维指标定义、实测结果、两条复现命令、诚实原则与边界；对外主张依据（英文生态无中文基准且分数不可复现）
- **supersedes 边感知排序（遗忘质量回归）**: `hybrid.py::_apply_supersedes_order` 在打分后降权被取代旧值（新值同在候选集时压到新值之下，新值缺席不动旧值——宁 miss 不脏写，残留如实测量）；向量主路径 / rerank 分支 / FTS 兜底路径统一接入；settings 新增 `SUPERSEDES_ORDERING_ENABLED` / `SUPERSEDES_DEMOTE_EPSILON`；评测集 `superseded_order_accuracy` 由 0.5 确定性升至 1.0，端到端断言升级
- **遗忘质量自测体系（一年内档）**: `lantai/eval/forgetting_quality.py` 六项维度化指标（陈旧残留/错别字容错/对照召回/时效排序/取代排序/取代残留），真实 DB 种子→真实遗忘→真实检索（FTS 兜底确定性），finally 清理含 supersedes 边；`lantai/eval/chinese_memory_cases.py` 中文评测集 v1（13 case：typo×4/fresh×3/stale×2/temporal×2/superseded×2，全部查询经 sqlite 直连验证 FTS 可命中）；`scripts/run_forgetting_quality.py` CLI 落盘报告；首份报告 `docs/memory-quality/2026-08-11.md`——typo/fresh/temporal 全绿、stale 零残留、superseded 暴露真实缺口（FTS 兜底下检索无 supersedes 排序语义）
- **Shell Hook 召回预算 + 记忆工具指南（借鉴 TencentDB Agent Memory）**: `shell_hook.py` 新增码点安全截断 `_truncate_codepoints`、总预算分配 `_apply_recall_budget`、指南生成 `_build_tools_guide`——单条记忆注入上限 `SHELL_HOOK_MAX_CHARS_PER_MEMORY=200`（替代硬编码 `[:200]`）+ 总预算 `SHELL_HOOK_MAX_TOTAL_CHARS=1500`，超预算截断/丢弃并附后缀提示；有命中时注入末尾附「记忆使用指南」（何时深挖、每轮最多检索 3 次、add 回写），`SHELL_HOOK_TOOLS_GUIDE` 可关；evidence 与注入行同源截断保持一致。决策见 [ADR-0006](docs/adr/0006-shell-hook-contract.md)，调研见 `docs/research/tencentdb-agent-memory-borrow.md`
- **Skill 资产化（借鉴 TencentDB Agent Memory）**: `proposer` 把候选 `actions` 沉淀为 `proposed_patch["structure"]`（name/description/steps），`promoter` 落库到 `MemoryItem.structure`，steps 非空强制 `decay_class="procedural"`（永不衰减铁律天然浮顶）；Shell Hook 对 procedural 记忆注入 Skill 块（`## Skill: 名称` + 描述 + 编号步骤），普通记忆保持平铺，同样受召回双预算约束。决策见 [ADR-0011](docs/adr/0011-skill-asset.md)
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- **Hermes 插件对话自动写入（v0.5 落地）**: 插件源码纳入仓库 `hermes-plugin/remembrance-hook/`（版本化+可测试）——`pre_llm_call` 把 user_message 累积到会话缓冲（有界防膨胀），`on_session_end` 每轮对话结束 flush 给 `shell_hook --serve` 新 dialogue 通道 → `ingest_dialogue`（fastpath 直通/提取建候选/闲聊入待审队列）；`scripts/install_hermes_plugin.py` 一键部署（自动备份旧版不删除）；settings 新增 `SHELL_HOOK_DIALOGUE_TIMEOUT=30`（LLM 提取超时）
- **Hermes 会话钩子验证（research）**: 确认 Hermes 插件 API 存在 `on_session_end`（每轮对话结束触发，桌面版与 CLI 通用，payload 无消息文本）——推荐实现：插件缓冲 `pre_llm_call` 的 user_message + `on_session_end` flush 给 `ingest_dialogue`（Supermemory 同款模式）；备选 state.db 只读扫描（sessions/messages 表 + WAL 安全，增量游标 last_activity_at）已探明 schema；结论见 `.scratch/dialogue-loop/issues/05`，已回写 spec
- **Search Transparency（检索透明）**: `remembrance/retrieval/evidence.py::build_evidence`（检索结果 → 来源说明 id+摘要+分数，rerank 路径按内容反查 id）——shell_hook `build_context` 注入附「本次依据」段（记忆 id + 摘要，有命中时）+ 结构化 `evidence` 字段；MCP `search` 与 REST `POST /search` 响应补 `evidence`；无命中/异常零侵入降级
- **Dialogue Ingest（对话写通道）**: `remembrance/ingestion/dialogue.py::ingest_dialogue`——对话文本 → 现有提取链（rawdocument→memorycandidate，不新建存储）：fastpath 白名单直通（记住/自我声明/偏好）；闲聊（过短/社交结束语）进待审队列；LLM 提取低置信度/失败（上游 502）兜底入队不丢数据；lane 启发式预判（preference/fact/general）。REST `POST /dialogue`（routes_dialogue.py）+ MCP `add_dialogue`；settings 新增 `DIALOGUE_ENABLED` / `DIALOGUE_MIN_CHARS` / `DIALOGUE_MIN_EXTRACTOR_CONF`（零硬编码，对话通道专用阈值不受 .env GATE_* 覆盖影响）
- **Candidate Review Queue（候选可见队列）**: `memorycandidate.review_due_at` 字段 + `pending_review` 状态——gate REJECT 不再静默丢弃（evolve_worker 落队，TTL `CANDIDATE_TTL_DAYS=7` 自动归档）；`remembrance/services/candidate_service.py`（enqueue_rejected / list_pending_candidates / review_candidate / run_candidate_ttl_once）；REST `GET /candidates/pending` + `POST /candidates/{id}/review`（approve→提案链并应用 / reject→归档）；MCP `candidates_pending` / `candidate_review`；每日 TTL 任务 `run_candidate_ttl`（digest_worker.py，`CANDIDATE_TTL_CRON_HOURS=24`）；幂等列迁移
- **Retrieval noise filtering**: `RetrievalEvent.is_system_noise` field + `is_system_noise()` classifier (deterministic prefixes + length gap), `scripts/mark_retrieval_noise.py` for idempotent backfill of legacy events
- **Hermes desktop injection plugin**: `remembrance-hook` Python plugin registering `pre_llm_call` (serve mode runs no shell hooks — `_AGENT_COMMANDS` excludes `serve`); resident `shell_hook.py --serve` NDJSON loop eliminates cold-start cost
- **Hermes onboarding scripts**: `scripts/migrate_home.py` (safe REMEMBRANCE_HOME migration), `scripts/verify_remembrance.py` (8-point self-check), `docs/hermes-install-handoff.md`
- **Manual call guide**: `docs/remembrance-manual-call.md` — Hermes chat / CLI JSON-RPC / REST API entry points
- **Dry-run evaluation pipeline**: `remembrance/eval/` — `EvalQuerySet`/`EvalRun` tables, `build_query_set()`, `compute_metrics()` (zero_result / avg_result_count / jaccard / weak_hit_rate), `run_dry_run()` with `param_overrides` + `intent_mode`, `scripts/run_dry_run.py` CLI; first report `docs/dry-run-report-v1.md` (179 samples, zero_result 0.0%)
- **Step 7 shadow observation**: `ShadowWindow` table + `shadow.py` decision logic (evaluate_window 3-guardrail: zero_result/avg_result/jaccard; conservative hold) + `runtime.py` integration (open_shadow with MAX_ACTIVE_SHADOW_WINDOWS guard, check_shadow_due periodic dry-run comparison, rollback_snapshot guardrail). DEDUP shadow-only (shadow params never write ParamOverride), manual gate preserved (promote marks only, application stays human-approved)
- **Step 8 verification feedback**: `SignalReliabilityStat` table (venue_class-level pass/fail/fail_streak) + `reliability.py` (record_verification_result, reliability_penalty with PENALTY_* thresholds, apply_penalty_to_weight) + `resolve_gating` venue_class hook — penalty only lowers weight (只降不升), TTL expiry restores, manual gate unchanged

### Fixed
- **全量顺序测试污染（调度器线程泄漏）**: 11 个测试文件经 `from api_server import app` + TestClient 触发 lifespan，会启动真实 BackgroundScheduler（evolve/ingest/forget 等 worker 对真实库做真实 LLM 调用——拖慢全量、写脏真实库），且 `stop_scheduler(wait=False)` 不等待在跑任务留下僵尸线程——`tests/conftest.py` 新增 autouse fixture 置空 `api_server.start_scheduler`，测试进程内永不启动真实调度器（零生产代码改动）。排查见 `.scratch/v0.6-aidumei-absorb/issues/03-fullrun-scheduler-pollution.md`
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **UTF-8 stdin corruption**: force `sys.stdin/stdout.reconfigure(encoding="utf-8")` in `mcp_server.py` and `shell_hook.py` — Windows GBK decoding turned Chinese queries into mojibake (「你好」→「浣犲ソ」) causing zero-recall + `no_signal`
- **Hermes shell-hook interpreter**: hooks config now points to `.venv-audit` python (hermes venv lacked sqlmodel); serve mode uses plugin channel instead
- **shell_hook timeout semantics**: single-shot mode returns `{}` on timeout instead of `os._exit` (serve mode needs resilience)

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- **used_ids weak-label backfill channel (direction-2)**: `POST /retrieval/backfill` REST route (`routes_retrieval.py`) + MCP `backfill` tool + `event_id` surfaced in `search` responses (REST + MCP + shell_hook). Generation side (Hermes) records which memories actually went into an answer → `backfill_used_ids()` → dry-run `weak_hit_rate` goes live. `run_dry_run` now loads `used_ids_map` by event_id (honest `None` when no backfill data)
- **Position-sensitive param-matrix analysis**: `scripts/run_param_matrix.py` — batch dry-run across weight tuples + top1/top3 consistency / position-drift metrics (Jaccard set-blindness fix); report `docs/param-matrix-report.md` (empirical: W_VECTOR 0.6→0.75 shifts top1 on 14/179 queries)
- **Step 8 人工验证入口**: POST /verification REST 路由（记录人工验证结果）+ GET /verification/stats（列出各信号类别可靠性统计与当前降权系数）——
ecord_verification_result 此前仅有函数无入口，现闭环打通
- **Backfill channel self-check**: `scripts/verify_backfill.py` — 8-point verification (MCP backfill tool registered / search returns event_id / handler / table+column / real write-read / `_load_used_ids_map` / production fill rate); guide `docs/used-ids-backfill-guide.md` updated with self-check usage

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **FTS5 MATCH 特殊字符语法错误**: search_fts 此前把原始查询直接拼进 FTS5 MATCH（AND.join(split)），含 = @ . ? / 的查询触发 syntax error 使整条 FTS 通道降级（真实查询大量触发）；现逐词引号包裹 + 双引号转义，trigram 子串语义不变（实测矩阵 1284 次检索警告 0）
- **e2e 测试外部网络 mock 补齐**: 	est_e2e.py 此前未 mock 提取器 chat_json 与 mbed（外部 LLM/embedding API），上游网络慢时每条用例拖 20-30s 甚至卡死——已按测试纪律补 mock（仅外部网络，业务逻辑真实执行）: Edit/Write to Windows-mounted files could drop trailing bytes (null-fill) — use bash + Python writes for mounted-path edits

### Changed
- **项目中文名定为「兰台记忆（Lantai）」**: 取自汉代皇家档案馆「兰台」——为 AI 保存、检索、演化、遗忘长期记忆的档案库；英文代号定为 Lantai。待审候选队列（`pending_review`）别名定为「锦囊」
- **内部包名统一为 lantai**: Python 包 `remembrance/` → `lantai/`（全库导入路径同步）；pip 包名 `remembrance-system` → `lantai`；环境变量 `REMEMBRANCE_HOME` 更名 `LANTAI_HOME`（旧名兼容回退）；MCP serverInfo 更名 lantai；Docker 镜像标签与文档路径同步。数据文件（remembrance.db / .chromadb）保留不变
- **Hermes 插件更名 lantai-hook**: hermes-plugin/remembrance-hook/ → lantai-hook/（manifest、日志前缀、部署脚本、测试、文档同步）；已重装到 Hermes 并清理旧插件目录

## [0.3.7] - 2026-08-04

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- **Data loss fix**: `apply_proposal` now accepts `APPROVED` status — human approval and `run_pending` paths were previously broken (found in live deployment)
- **SQLite self-deadlock**: Use outer session for `MemoryEdge` in `apply_proposal` — nested session caused deadlocks under concurrent writes (found in live deployment)
- **Gate threshold isolation**: Pin `GATE_MIN` in test to isolate from host `.env` pollution

### Changed
- Untrack `.workbuddy` session metadata (keep on disk), keep parallel-session prompt doc in `docs/`

### Removed
- Root-level empty `remembrance__init__.py` (0-byte junk re-added in previous commit)
- P2 plan (tidal-coalescing + MCP) — superseded by v0.3.1/v0.3.3 implementations
- Accidentally removed `docs/plans/` restored

## [0.3.6] - 2026-07-31

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Comprehensive README with architecture diagram, features table, quickstart, API reference, and testing guide
- README rewritten in aiduMEM style (with adaptation credit)
- MIT LICENSE

### Fixed
- **FTS5 短词毒化 AND 链**: `search_fts` 剔除 <3 字符 token（trigram 最小成词长度）——2 字词（如「密钥」）在索引侧无法成词，却让整条 `"API" AND "密钥"` 查询整体失效（评测集 superseded 用例暴露）；短词在 trigram 下本就零命中，剔除不改变任何既有命中结果
- Removed empty `remembrance__init__.py` from root

## [0.3.5] - 2026-07-28

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Test suite: 120 tests, all green
  - FTS5 integration tests
  - SSRF safety tests
  - Backup/recovery tests
  - MCP protocol tests
  - Shell Hook timeout tests

### Security
- Supply chain hardening: GitHub Actions pinned to commit SHA (not mutable tags)
- Docker images run as non-root

## [0.3.4] - 2026-07-25

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- FTS5 trigram parallel recall + BM25 caching ([ADR-0008](docs/adr/0008-fts5-parallel-recall.md))

## [0.3.3] - 2026-07-22

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- SSRF hardening: external fetch protocol whitelist + DNS resolution IP blocking
- Atomic backup/recovery with online backup + manifest SHA256 validation
- MCP server: input validation + exception isolation

## [0.3.2] - 2026-07-18

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- FTS5 schema + Chronos timezone + BM25 compatibility fixes

## [0.3.1] - 2026-07-15

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- P0 audit remediation:
  - Repository hygiene
  - Binding authentication enforcement
  - Test baseline establishment

## [0.1.0] - 2026-06-20

### Added
- **Raw Drawer 原文直存（P0-1）**: `POST /add/raw`——verbatim 记忆零 LLM 直写（只 embedding + FTS5），内容 sha256 幂等去重，不走提取/闸门/演化；MCP `raw_add` 工具。决策见 [ADR-0009](docs/adr/0009-raw-drawer-verbatim.md)
- **冲突消解确定性层（P0-2）**: `gate/conflict_rules.py` 互斥规则集（settings 可配）优先、LLM 回落双通道；规则命中写 `ConflictEvent` 账本（可溯源、可裁决）；REST `GET /conflicts` + `POST /conflicts/{id}/resolve`，MCP `conflicts_list` / `conflict_resolve`；闸门决策语义不变（仍走待审队列人工裁决）。决策见 [ADR-0010](docs/adr/0010-conflict-resolution-layer.md)
- **MCP 工具扩容（第一批）**: 8 → 12 工具，新增 `raw_add` / `rollback` / `conflicts_list` / `conflict_resolve`
- Initial release adapted from [aiduMEM](https://github.com/monkey2jack/aiduMIT)
- Storage layer: SQLite + FTS5 + ChromaDB
- Four-path hybrid retrieval: vector + BM25 + FTS5 trigram + decay
- Relevance gate, Tidal coalescing, Fastpath, Dedup, Ebbinghaus forgetting, Chronos
- Shell Hook + MCP dual-mode integration
- Security baseline: loopback binding, SSRF guard, atomic backup, endpoint whitelist




