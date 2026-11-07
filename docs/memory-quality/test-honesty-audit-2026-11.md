# 测试诚实性审计报告（v0.68 · 2026-11）

> 票据：`.scratch/v0.68-test-honesty/issues/01-audit-and-smoke.md`（并行流 B）
> 工具：`.scratch/v0.68-test-honesty/tools/audit_honesty.py`（AST 交叉盘点，可重跑）
> 基线：全量 pytest **3044 passed / 5 skipped / 0 failed**（2026-11-07，venv=主检出 `.venv`，`PYTHONPATH=.`）

## 1. 口径

**核心函数**：被 API 路由 / worker / service / MCP / 调度装配调用（含传递可达）的
产品函数与方法，覆盖 storage / retrieval / gate / cognition / memory / core /
services / workers / evolution / ingestion / ops / observability / parameters /
parsing / llm / security。传输层（`lantai.api.routes_*` 路由函数、`lantai.runtime`、
`lantai.cli.mcp` 工具 handler）是调用面不是被测面，单列口径见 §6。

**判定**（AGENTS.md 测试纪律）：

| 判定 | 含义 |
| --- | --- |
| `real-smoke` | 存在真实构造最小输入、直调该函数、内部逻辑不 mock 的测试 |
| `mocked-only` | 只有经 mock（替身替换被测函数自身逻辑或其内部步骤）才通过的测试 |
| `hollow-smoke` | 直调用例存在，但每个直调用例都 patch 了被测函数的内部步骤 |
| `indirect-only` | 无直调，但经调用栈真实执行过（HTTP / JSON-RPC 帧 / 上层服务），无内部 mock |
| `never-executed` | 任何测试都从未真实执行过 |

conftest 既定替身（`_stub_external_llm` 的 embed/chat_json/vision_caption、
`param_env` 的向量库 DummyVS 与 `db.get_session`、`_disable_reranker`）是纪律
**允许**的外部依赖替身，不计入不诚实；「mocked-only / hollow」只针对被测函数
自身逻辑或其内部步骤被替换的情形。

**方法**：AST 解析全部产品模块建调用图（含类构造、实例方法调用、同模块裸名），
从 surface 入口求传递闭包得核心函数集（1080 个，剔传输层/DTO/私有/dunder 后
604 个进判定表）；测试侧按测试函数粒度解析直调与 patch 目标（含
`patch("lantai...")` / `monkeypatch.setattr` / `patch.object` 三种形态），另做
两条回填：FastAPI 装饰器路径 × TestClient 调用路径匹配（HTTP 真实执行证据）、
JSON-RPC 帧工具名 × `handle_<tool>` 动态分发回填。判定结果经 13 个残留项
逐一人工甄别（grep 调用点 + 读产品代码），工具与人工结论的差异全部归因：
动态分发、实例流、DTO、死代码（§5、§6）。

## 2. 总量结论

| 判定 | 数量（核心表 604 项） |
| --- | --- |
| real-smoke | **526**（+3 LoopGuard 方法经人工证据归真，见 §6） |
| indirect-only | 70 |
| never-executed | 8（其中 4 个为死代码，另立票；其余见 §5） |
| mocked-only / hollow-smoke | **0**（审计前 4 + 1，本轮补强后清零，见 §4） |

分包概览（节选，全部明细见 `tools/audit.json`）：

- **cognition 31 个引擎模块全部 real-smoke**——票内基线的结论在方法级复核成立
  （如 `CanshangEngine.arbitrate_deep_conflict` →
  `tests/test_canshang_engine_smoke.py::test_arbitrate_deep_conflict:210`）。
- 纪律具名例子 `hybrid_search` / `apply_forgetting` / `search_fts` /
  `apply_proposal` / `resolve_conflict` / `relevance_check` 全部 real-smoke，
  证据均为真实直调（如 `tests/test_prefilter.py:27`
  `relevance_check("不对，不是这样的")`）。
- `indirect-only` 70 项集中在 parameters（registry/queue/advisor/validation）、
  services 读侧包装、retrieval 内部件（`mmr_select`、`signature_bonus`、
  `filter_and_resolve_layer`）与 `core.text`。它们**都被真实执行过**（经 HTTP
  / MCP 帧 / 上层服务，证据见 `tools/audit.json` 的 `indirect_files`），缺的只是
  「直调」这一纪律字面形态。属可排期的低风险债，不是诚实性风险。

## 3. 审计前的不诚实测试点名（证据）

以下用例的名字/注释声称测 X，但 X 的关键内部逻辑被 patch——函数自身从未在
测试里真实跑过主路径（本轮已全部补真冒烟，见 §4）：

1. **`tests/test_coalesce_async.py::test_run_coalesce_idle_persists:226`**
   名为「persists（持久化）」，实际 `_create_candidate_with_extraction`
   被 patch 成 `return_value={"document_id": "doc1"}`——持久化从未发生；
   `get_coalesce_buffer` 也被换入手工实例。`test_coalesce_session_key.py::
   test_worker_persists_session_lineage:116` 同型。
2. **`tests/test_record_lifecycle.py::test_retract_vector_failure_reported_sql_authoritative:178`**
   patch `hybrid.delete_memory_item` 本体；`test_unarchive_resync_failure_
   warned_not_blocked:320` patch `hybrid.has_memory_item` 本体。故障注入本身
   合法（验 SQL 权威语义），但两函数直调面为零。
3. **`tests/test_x01_fact_cascade_regression.py:217` / `:249`**
   patch `record_ops_service.purge_facts_for_memory` 本体验证级联失败回滚
   （合法失败路径），完璧级联删除主路径零直调。
4. **`tests/test_provenance.py::test_dialogue_chitchat_candidate_carries_provenance:119`**
   patch `dialogue._is_chitchat` 内部判定来驱动闲聊分支，判定函数自身无直调。
5. **`lantai.workers.ingest_worker.run_coalesce_idle`（hollow）**：它的全部三个
   直调用例都落在上面 1 的形态里。

不算不诚实（注明口径）：`tests/test_digest_ownership.py::digest_owned_env:75`
patch `digest_worker._digest_dir` 是**文件系统接缝**（纪律允许的文件系统副作用
替身），digest 主逻辑仍真实执行。

## 4. 本轮补强（全部「真输入直调 + 仅外部替身」，20 例全绿）

| 新文件 | 补的缺口 |
| --- | --- |
| `tests/test_worker_entry_smoke.py` | `run_forgetting_once`、`run_candidate_ttl`（此前从未执行，且 `run_candidate_ttl` 是「宁 miss 不脏写」超龄归档落地点）；注册表名→入口一致性护栏 |
| `tests/test_ingest_run_smoke.py` | `run_ingest` 直调 + `POST /ingest/run` 真实路由（此前零覆盖） |
| `tests/test_mcp_frame_gaps_smoke.py` | `crystal_promote` / `recall_report` 两个已广告、零驱动的 MCP 工具（真晋升断言到 DB：promoted + swarm_shared + leads_to 边） |
| `tests/test_coalesce_idle_real_flush_smoke.py` | `run_coalesce_idle` 真单例 + 真库持久化（§3-1 的反例正写） |
| `tests/test_vector_item_wrappers_smoke.py` | `delete_memory_item` / `has_memory_item` 直调（记录型向量替身验派发契约） |
| `tests/test_purge_facts_smoke.py` | 完璧级联删除主路径 + 无事 noop |
| `tests/test_decision_helpers_smoke.py` | `novelty_score`、`detect_secret_shapes`/`route_secret_hits`（enforce → pending_review）、`viewer_of`/`is_system_viewer`、`_is_chitchat` |

补强过程中验证的两条既有结论：`is_system_viewer` 只认
`user_id == SYSTEM_VIEWER`（role 无关）；`NO_MEMORY_PATTERNS` 字符类不含全角
逗号（`好的，再见` 不命中，测试用例已按真实行为写）。

## 5. 真缺口（从未执行，非伪影）——已在 §4 收口 + 一项另立票

审计前的 never-executed 甄别后，真实缺口 7 项全部属「入口/接缝未测」：

- worker 分发入口 2（forgetting / candidate_ttl）→ 已补
- 摄取总入口 1（run_ingest + 路由）→ 已补
- MCP 广告工具 2（crystal_promote / recall_report）→ 已补
- `lantai.cli.mcp.main`（CLI 子命令入口，薄壳）→ 未补（低价值，登记待排期）
- `LoopGuard.begin/cancel/finish` → 工具伪影（见 §6）

## 6. 静态工具的已知边界（诚实声明）

- **动态分发**：`worker_operation_service` 按字符串分发 worker、MCP 按
  `TOOL_HANDLERS` 名字分发 handler、FastAPI 按 HTTP 路径分发路由——三者静态
  不可见，审计用「测试文件中出现分发名/路径」回填执行证据，粒度是文件级。
- **LoopGuard.begin/cancel/finish**：`tests/test_mcp_loop_guard.py:114-135`
  用真 LoopGuard 实例（只 patch `_get_guard` 取件器）经 handle() 真实驱动，
  判 real-smoke（工具因 `_get_guard()` 返回值上的方法调用无法静态解析而漏记）。
- **DTO / dataclass / `to_dict` / 私有 helper**：不进核心表；`_digest_dir` 等
  私有函数的 patch 见 §3 口径注。
- **调用方 grep 范围**：死代码判定的 prod_calls grep 初版只扫 `lantai/` +
  `api_server.py`，会把 `scripts/` 里的装配点误判成零调用方——§7 的
  `build_scene_navigation_lines` 误报即源于此；triage 工具已修
  （扩为 `lantai/` + `scripts/` + 根目录 `*.py`）。
- 工具脚本与全部判定明细（含每个函数的证据行）：
  `.scratch/v0.68-test-honesty/tools/audit_honesty.py` + `audit.json`
  （`.scratch` 不入库，重跑即可再生）。

## 7. 死代码发现（另立票）

以下核心函数**产品代码零调用方**（grep + 调用图双重确认），不是测试问题：

| 函数 | 位置 |
| --- | --- |
| `KnowledgeLifecycleManager.scan_and_retire` | `lantai/cognition/lifecycle.py:106` |
| `evaluate_and_evolve` | `lantai/services/evolution.py:8` |
| `autodream_cron_days` | `lantai/workers/autodream_worker.py:19`（docstring 称「零硬编码」，实际无人读它，scheduler 直接用 settings） |

→ `.scratch/v0.68-test-honesty/issues/02-dead-code-findings.md`（needs-triage）。

**勘误（2026-11-07 第六次复验）**：本节初稿曾把 `build_scene_navigation_lines`
（`lantai/services/scene_service.py:444`）也列为死代码，系**误报**：triage 的
prod_calls grep 只扫 `lantai/` + `api_server.py`，漏掉 `scripts/`——实际
`scripts/shell_hook.py:433 → _build_scene_lines → 本函数`（import 在 277 行）
自 v0.6（`2b41b47b`，2026-08-11）就是宿主 hook 检索响应的活链路。上表已剔除
该项，其余 3 项经真正全仓 grep（含 scripts/、hermes-plugin 与非 .py 引用）
复核仍零调用方。误报函数已补真冒烟（`tests/test_scene_navigation_smoke.py`，
2 例），triage 工具 grep 范围已修（见 §6）。

## 8. 门禁

- 全量 pytest：绿（数字见票 01 Answer 收口），**隔离护栏台账零新增**
  （`_MODULE_STATE_RESIDUE` / `_BINDING_RESIDUE` 无本票点名；补强测试为此
  改用过两处写法：MCP 帧测试走 exec 副本模块避免弄脏 `_guard_instance`
  通道、向量包装测试不叠 param_env 的 `get_vector_store` 双层 patch——
  都是 conftest 注释里记载过的病根，见各测试文件 docstring）。
- 非目标遵守：未改任何产品代码；未追溯改名；未引入覆盖率数字指标。

## 9. 合并流 A 后的复核（2026-11-07）

流 A 的 6 笔更正闸半步（`22099ac9…3b1e8d23`，触及 hook/plugin/retrieval/
cognition/mcp）并入本分支（合并提交 `0fef5cc5`，零冲突，两侧改动文件无重叠）
后，按 §1 口径全量重验：

- **审计工具重跑**（`audit_honesty.py`）：核心集 1080 → 1083，新增 3 项全部为
  `lantai/retrieval/supersede.py` 私有 helper（`_apply_supersede_guard` /
  `_superseded_marker` / `_superseded_date_str`），判定均 `indirect-only`，
  证据为流 A 自己的真库真链路守闸测试（`tests/test_mcp_search_supersede_guard.py`
  等 6 文件，唯一 patch 是 `SHELL_HOOK_SUPERSEDED_GUARD` settings 开关，属
  §1 允许口径）；私有 helper 本不进 §2 判定表。`mocked-only` / `hollow-smoke`
  与合并前逐项一致（工具原始口径唯一残留仍是 §3 注明的 `_digest_dir` 文件
  系统接缝豁免）。**合并零新增诚实性缺口，无需补强。**
- **全量门禁**：**3078 passed / 5 skipped / 0 failed**（5m24s，exit 0，
  venv=主检出 `.venv`，`PYTHONPATH=.`；3078 = 合并前 3064 + 流 A 侧新增 14 例）。
  隔离护栏台账零新增：模块级残留仍为既有 8 通道（_guard_instance / kaogong /
  consolidation / routes_health / llm gate×2 / cehou / param runtime），绑定
  残留仅 test_3a 申报哨兵；本票 7 个新测试文件与流 A 新测试文件在两本台账
  全文零出现。
- 分支状态：审计报告 + 7 个补强测试文件 + 流 A 6 笔同分支交付，push/PR 留
  维护者闸门。
