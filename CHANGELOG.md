# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- **MCP 工具扩容（第二波，借鉴 aiduMEI v18 工具面 37 个反查兰台已有服务，21 → 28）**: 新增 `mem_recent`（最近记忆只读，按更新时间倒序）、`mem_stats`（overview 聚合：总数/分布/待审候选/检查点/待审提案）、`mem_health`（深度健康：SQLite + 向量存储，不触发外部 LLM）、`autodream_report`（蒸馏预演 dry-run 不写库）、`autodream_trigger`（执行一轮蒸馏落 pending 提案，宁 miss 不脏写）、`proposals_list` / `proposal_decide`（待审提案查看/裁决，approve 先落 Checkpoint 可回滚、reject 记 decision_reason）；`tests/test_mcp.py` 追加 8 例（不 mock 冒烟：真实 SQLite+FTS，仅 mock embedding/向量存储）；明确不吸收：`mem_delete`/`mem_delete_all`（硬删除无审计链）、`mem_update`（原地编辑以 Checkpoint 回滚替代）、`session_*`/`code_*`/`crystals_*`/`knowledge_tree`（会话归宿主、Code Graph 正交、树状/结晶为后续赛道）。票据 09
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
- **冷启动导入·对话链（历史会话 JSONL → 摄取链 + 时间戳继承，借鉴 TencentDB Agent Memory L0 + v2.0.1 时间戳修正）**: `lantai/ingestion/import_service.py`——L0 会话格式（{role, content, timestamp[, session]}）经 `scripts/run_import.py` 批量喂既有对话摄取链；`ingest_dialogue(created_at=...)` 透传原始时间戳（RawDocument.fetched_at / MemoryCandidate.created_at），provenance.prompt=dialogue-session-import，promoter 按 import provenance 把 created_at 继承到 MemoryItem（时间线不压平；非导入路径不覆盖）；--dry-run 零写库预览，`IMPORT_MAX_LINES=5000` 防护。测试 `tests/test_import.py`（9 例，纯函数不 mock + 真实 SQLite/tmp_path + 演化链对照），票据 01，ADR-0018
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




