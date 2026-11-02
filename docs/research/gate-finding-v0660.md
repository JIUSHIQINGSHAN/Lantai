# gate-finding-v0660.md — v0.66.0 发布门禁判定（纯审计，不改产品代码）

- 判定人：Mirasim 门禁 Agent（票 `.scratch/v0.66-next-window-2026-11/issues/05-v0660-release-gate.md` 续13）
- 日期：2026-11-03（Asia/Shanghai）
- 被判定对象：`master` HEAD = `553be338`（`ahead origin/master 12`，无 `v0.66*` tag，未 push）
- 版本线：pyproject/FastAPI/MCP serverInfo 等 6 处引用 + CHANGELOG 最新段 = **0.66.0**（实测一致）
- 本轮性质：**只判定、只记录**。未修改任何产品代码、测试、CI、迁移或默认暴露策略；
  独立验证探针写在 `.scratch/gate/verif_r13/`（gitignore 内，故意不入库：门禁不代开发补测）。

## 0. 执行的环境与命令

| 目的 | 命令 | 环境 |
|---|---|---|
| 干净环境全量（票面 Step 1.1） | `python -m pytest tests/ -q -o addopts="" --tb=short` | 一次性临时 `LANTAI_HOME`；剥 `API_KEY/OPENAI_API_KEY/RERANKER_API_KEY/OPENAI_BASE_URL/RERANKER_BASE_URL/DATABASE_URL/LANTAI_MCP_PROFILE/CHROMADB_PATH` |
| 全量（CI 口径含插桩） | `pytest tests/ -q --cov=lantai --cov-report=term-missing` | 同上 + `API_KEY=test-key` |
| 权威门禁 | `python scripts/release_check.py v0.66.0`（无旗标） | 同上 + 绊线盯宿主真实库 |
| 独立探针 | `pytest .scratch/gate/verif_r13 -q` | 一次性临时 home + 内存 SQLite |

Runner：`.scratch/gate/gate_run.py`（自建临时 home、跑完删目录、记录 exit/wall/log 路径）。

## 1. Step 1 — 前序评审编号逐条状态（取证强度分三档）

评审清单来源：`docs/research/commit-review-v0.38-v0.60-2026-10.md`。

**A 档 = 本轮写独立探针复现验证（见 §3）**

| 编号 | 病灶 | 状态 | 修复 commit | 残余风险 |
|---|---|---|---|---|
| X-01 (P0) | 已删隐私经原子事实回流并注入 prompt | **已修**：读侧单一真源 `fact_source_alive_clause()`（`lantai/retrieval/wanbi_retriever.py:17`）+ 贯珠/探颐/完璧三处 `.where(...)`；`_attach_wanbi_facts`（`lantai/retrieval/hybrid.py:931`）取消「孤儿事实挂首位」；注入前第二道闸 `scripts/shell_hook.py:84 _live_atomic_facts`（异常时 fail-closed 全丢） | `93e73ac7` | 级联**物理删除**未做（软删口径）；`fact_atomic` 表仍留已删来源的行，任何**新增**读通道若忘了带这个 clause 就会复发——目前无「所有 fact_atomic 读通道必须带存活谓词」的静态门禁 |
| X-02 (P0) | 60 天 TTL 误杀重要记忆；注入不回写保活 | **已修**：`lantai/memory/forgetting.py:39 _decay_exempt` 走 `settings.FORGET_IMPORTANCE_EXEMPTION_FLOOR=0.8`（`lantai/core/settings.py:242`）；保活回写 `lantai/observability/retrieval_log.py:137 _touch_keepalive`（`use_count+1`、`last_used_at=utcnow()`），由 `log_retrieval`/`backfill_used_ids` 触发，shell_hook/MCP/routes_search 三条注入回执通道都接 | `93e73ac7` | `FORGET_IMPORTANCE_EXEMPTION_FLOOR` 无边界校验（配成 2.0 会静默关掉豁免且无人报警）；`helpful_count` 刻意不回写（交裁决），若将来判据改依赖它需重新对齐 |
| R-01/C-16 | probe reply/resolve 用裸子串判否定→误改记忆 | **已修**：`lantai/services/probing_service.py` 逐小句 + 锚定边界类判定（`_AFFIRMATIVE_PATTERNS:75`、`_NEGATIVE_PATTERNS:102`、`_is_affirmative_reply:413`） | `93e73ac7` | **miss 方向**残留：`确认无误`/`未确认` 既不判确认也不判否定（落 unclear，探针不推进、超龄按 CANDIDATE_TTL 归档）。已用现状锁记录，不产生脏写 |
| D-01 | `shell_hook.py:243-246` 生产代码改写 `hybrid` 模块全局 | **已修**：改为显式参数传导 `hybrid_search(query, top_k=..., budget_s=budget_s)`（`scripts/shell_hook.py:317`）；全仓 grep `setattr(|global ` 在 hook 路径 **0 命中** | `93e73ac7` | — |
| D-02 | 插件 15 字预过滤使短查询不注入画像 | **已修**：`_MIN_QUERY_CHARS = 2`，触发词表整体移除并把「要不要检索」交回 `gate.prefilter.relevance_check`（`hermes-plugin/lantai-hook/__init__.py:94,443-447`） | `93e73ac7` | 边界仍由 shell_hook 判定；若 relevance_check 将来加长度门槛会回退，本轮未对 relevance_check 复现 |
| N-04/C-15/R-02 | MCP 工具默认全量暴露 | **仍存活**（见 §2） | — | 唯一在册技术红项 |

**B 档 = 有专门回归测试且全量绿，但本轮未逐条独立复现**

| 编号 | 状态 | 证据 |
|---|---|---|
| X-03 终端合并/编辑绕过闸门 | 有覆盖 | `tests/test_terminal_merge_gate.py`、`test_terminal_write_guards.py`、`test_terminal_get_ownership.py` |
| X-04 persona 写入不过注入闸门 | 有覆盖 | `tests/test_persona_injection_fence.py`、`test_persona_ownership.py` |
| N-01/N-02 拾遗零召回补救绕过滤 / 热路径全表扫 | 隔离面有覆盖（域/agent/时态/verbatim 隔离测试全绿） | `tests/test_cross_isolation.py`、`test_domain_isolation.py`、`test_probing*.py`；**性能面（N-02 全表扫）本轮未取证** |
| N-05 工具数断言过期 | 全量绿 ⇒ 已同步 | 2797 passed |
| N-06 仓库根垃圾文件（`票据目录：。`/`遵循`/评审补丁） | **已清**：`git ls-files` 对这三个模式 0 命中 | 实测 |
| N-07 21 个标签未经 CI 出镜像 | **结构性缓解**：`ci.yml` tag push 先 `needs: tests` 再 build/push（ci.yml:41-43） | 但「候选提交从未跑过 CI」在当前线上仍成立（未 push、无 tag） |
| D-13 `shared_scope NOT NULL` 无 server default | **半修**：老库补列迁移 `_migrate_v36`（`9b062b1b`）+ 老库回放门禁（`eb507821`）；4 列 NULL 方向差异仍按书面豁免清单放行 | 权威门禁原文：「NULL 漂移仅在书面清单内」 |

**C 档 = 仍存活 / 本轮取证为未修**

| 编号 | 实测证据 |
|---|---|
| **X-05 隐私文档进过公开仓库** | `.gitignore` 已含 `docs/memory-offload/`、`docs/memory-wiki/`，`git ls-files` 两目录 = 0；**但 git 历史仍有**：`git log --all --name-only -- docs/memory-offload docs/memory-wiki` 命中 `docs/memory-wiki/index.md`(×2)、`overview.md`(×3)，涉及 `7152e338/2b41b47b/bbbc4496`；且 origin 存在、历史上已推送 ⇒ **内容未从可见副本中消失** |
| **X-08 文档写 WAL、代码没开** | `README.md:107` 等处仍写 WAL；`lantai/**/*.py` 中 `journal_mode` **0 命中** |
| X-09 密钥外发口径 | `lantai/retrieval/reranker.py:14` 改为显式 host 白名单 `{api.siliconflow.cn, api.openai.com}`：不再是「拿 OpenAI key 打第三方」的无界行为，但 OpenAI 密钥与 SiliconFlow 同属一个白名单，跨域携带仍未按「一域一钥」隔离（本轮未探针复现，仅记录现状） |
| X-10 README 与实不符 | `README.md:272` 已改成「全量测试以 CI 为准」，但 `:281` 仍留 `v0.3.5 测试全绿 120/120` 这类硬编码历史数（口径已声明「历史记录」，不再冒充当前状态） |
| D-03/D-05/D-06(E2)/D-08…D-16、N-03、X-06、X-07 | 本轮**未逐条取证**；不声明已修，也不作为本判定的新增依据。E2 时态回归（D-06/N-01 相关）依旧是未闭环评测线 |

## 2. MCP 暴露面核查（G3）

- 规定的开关 **不存在**：`grep -rn "LANTAI_MCP_EXPOSE" lantai/` → 0 命中。
- 实际开关：`lantai/cli/mcp.py:3705-3713`
  ```
  def _get_active_tools() -> dict:
      profile = (os.environ.get("LANTAI_MCP_PROFILE") or os.environ.get("MCP_TOOL_PROFILE") or "").strip().lower()
      if profile in ("hermes", "core", "slim", "minimal"):
          return {k: v for k, v in TOOLS.items() if k in HERMES_CORE_TOOLS}
      return TOOLS          # ← 默认（未设/拼错 profile）= 全量注册表
  ```
- 注册面是单一 dict（`TOOLS = {` @ `lantai/cli/mcp.py:2068`），无逐工具装饰器（`register_tool`/`@mcp.tool` = 0 命中），因此默认档位**一处即决定全部暴露**。
- 本轮实测：`release_check.py` 的「MCP 默认暴露」项报 `不设 profile 时 tools/list = 96/96`；设 profile 时 5/96。
- 结论：**G3 违反**（默认档 = 全量；且没有 `all|safe|none` 显式三档开关，只有 profile 名 + 未知值即回退全量）。
  在册冲突：`map.md:41` N-04 记为「有意语义、不开票」——门禁不接受「有意」而无人签字的默认全暴露。

## 3. Step 2 — 独立验证探针（`.scratch/gate/verif_r13/`，23 passed / 0 failed）

`python -m pytest .scratch/gate/verif_r13 -q` → `23 passed in 7.47s`，exit 0（临时 home，内存 SQLite，不 mock 被测逻辑）。

| 探针 | 断言（真对象直调） |
|---|---|
| `test_x01_deleted_source_fact_not_retrieved` | 存活事实查得到（前提锁）+ 已软删来源事实 **不**返回 |
| `test_x01_orphan_fact_never_attached_to_first_hit` | 无血统事实不得挂到任何结果条目（回归「挂第一条」） |
| `test_x01_injection_guard_is_fail_closed` | 注入前只放行来源 active 的事实；来源查不动时返回 `[]` |
| `test_x02_high_importance_survives_ttl_but_low_importance_is_not_exempt` | 0.95/65 天不被归档；同时 `_decay_exempt(0.2)=False` ⇒ 不是「全员豁免」 |
| `test_x02_injection_receipt_bumps_keepalive` | `log_retrieval` + `backfill_used_ids` ⇒ `use_count+1` 且 `last_used_at` 刷新 |
| `test_r01_cjk_negation_is_never_affirmative` ×8 | 不是/并非/没有/未曾/勿/别/不可/禁止 全部不判确认 |
| `test_r01_affirmative_still_recognised` ×4 | 是的/对，就是这样/没错，记下来吧/确认，无误 仍判确认（反向保护，防过度收紧） |
| `test_r01_known_miss_lock` ×2 | 现状锁：`确认无误`/`未确认` 落 unclear（miss，不脏写） |
| `test_r01_short_and_empty_replies_do_not_trigger_mutation` | 空串/空白/`?`/`。` 不判确认 |
| `test_g2_hybrid_module_globals_unchanged_by_search` | 真跑 `hybrid_search(..., budget_s=1.0)`，逐属性 `id()` 快照比对 ⇒ `hybrid` 模块全局 0 处漂移 |
| `test_d02_short_query_still_calls_hook` / `..._single_char_query_still_skipped` | 7 字问句抵达注入；1 字仍按 `_MIN_QUERY_CHARS` 跳过 |

## 4. 底线核查（G1–G6）

| # | 判定 | 证据 |
|---|---|---|
| **G1** 全量 fail=0 error=0，无 skip/xfail 掩盖 | **部分违反** | 干净环境（剥密钥）`pytest tests/ -q -o addopts="" --tb=short` → **2797 passed / 0 failed / 0 error / 0 skipped**，exit 0，266.2s（`.scratch/gate/baseline/r13_nokey-nokey.log`）。CI 口径（含 `--cov`）同数 2797（上一轮权威门禁实测，`wall=342.6s`）。**但存在制度外 skip 面**：`tests/test_longmemeval_core.py:32` 整模块 `skipif(not DATASET.exists())`，而 `data/` 在 `.gitignore:70` ⇒ **CI 上这条评测线恒 0 采集且不报错**；另有 `test_wheel_ships_ui.py:19`（无 uv 即跳）、`test_release_check.py:43`（无 git 即跳）、`test_mcp_principal_wiring.py:617,657`（AUTODREAM 关则跳）、`test_longmemeval_core.py:141`（预注册缺失跳）。本机全部实际执行（skip=0），故 G1 主口径成立；**评测/构建线的「CI 静默不可见」记为残余风险**，非本轮放行理由 |
| **G2** 无测试污染生产全局 | **违反** | ① `tests/conftest.py:19-20` **导入期**写 `os.environ.setdefault("OPENAI_API_KEY","test-key")` 与 `("OPENAI_BASE_URL","https://api.openai.com/v1")`，全程不恢复：ambient 有真实 key 时 setdefault 不覆盖 ⇒ 测试可打到**真实端点**，且门禁的绊线只盯 `sqlite3.connect` 路径（`tests/support/real_home_tripwire.py`），**网络出口无人监控**；② `tests/conftest.py:29-33` 直接改生产单例 `settings.FEATURE_OBSIDIAN/WIKI/WORK_ITEMS/VISION/TERMINAL = True`，无 teardown ⇒ 全量绿不代表默认配置行为；③ `tests/test_release_check.py:41` 永久改 `os.environ["PATH"]`；④ `tests/test_recall_chain.py:137/151` 用 `__enter__/__exit__` 写删 `LANTAI_INTENT_OFF`（异常路径依赖 with，且会吞掉用户原值）。**正向**：模块绑定污染有结构检测（`_GUARDED_BINDINGS` 10 项 + `_repair_canonical_bindings` + `unexpected_binding_residue`，未申报残留判红，申报白名单仅 1 条自测用例），且 A 档探针 `test_g2_hybrid_module_globals_unchanged_by_search` 证明 hybrid 全局不再被 hook/检索路径改写；宿主真实库 0 连接（绊线 2797 用例 0 HIT），`%TEMP%`/工作区无残留 |
| **G3** MCP 必须有显式开关，禁止默认全量 | **违反** | 见 §2：`LANTAI_MCP_EXPOSE` 不存在；`_get_active_tools()` 未设 profile 即 `return TOOLS`（96/96） |
| **G4** CI 不得用 `--deselect`/`if: false` 跳过 integration/e2e | **通过** | `Select-String -Path .github\workflows\*.yml -Pattern 'deselect','continue-on-error','if: false','|| true','--ignore','-k ','xfail' -SimpleMatch` → **0 命中**；两个 workflow 都跑全量 `pytest tests/`；`ci.yml` 的 build job `needs: tests`（红灯不出镜像）。注意：仓库里没有独立的 "integration" job/标记，全量即唯一口径（不是被跳过，而是未分层——记入残余） |
| **G5** 每个修复必须有新增测试，禁止改旧断言凑绿 | **通过** | `git show --numstat -- tests/`：`93e73ac7` +2586/−55（24 文件，含 `test_x01_*`、`test_forgetting_exempt_archive.py`、`test_probe_reply_cjk_semantics.py`、`test_plugin_short_query_persona.py`）；`012ffeea` +59/−2；`9b062b1b` +119/−0；`eb507821` +10126/−0（含 DDL-only 快照，实测 `contains_row_data=False`、CJK 0、最长 token 是索引名）；`06049a7f` +205/−3；`2787abc9` +39/−0；`553be338` +90/−0。本轮另以变异对照证明判据有牙（前序 `hybrid.embed` 去 timeout ⇒ 新断言判红） |
| **G6** 门禁脚本不得 `|| true`/吞退出码 | **通过** | workflows 无 `|| true`/`continue-on-error`；`scripts/run_forgetting_quality.py:148` `return 0 if ok else 1`（非 `--check` 的报告分支才 return 0）；`scripts/release_check.py` 的 `summarize()` 把 SKIP 计入分母且返回非 0（实测 `--skip-suite` → `8/10 通过, 1 失败, 1 跳过`，exit 1），所有探针异常路径返回**判红**文案而非放行 |

## 5. 全量数字（Step 2.3）

| 口径 | passed | failed | error | skipped | exit |
|---|---|---|---|---|---|
| `pytest tests/ -q -o addopts="" --tb=short`（无 key、临时 home） | 2797 | 0 | 0 | 0 | 0 |
| `pytest tests/ -q --cov=lantai --cov-report=term-missing`（CI 口径，`API_KEY=test-key`） | 2797 | 0 | 0 | 0 | 0 |
| `.scratch/gate/verif_r13`（独立探针） | 23 | 0 | 0 | 0 | 0 |

权威门禁（`python scripts/release_check.py v0.66.0`，无旗标，干净树）= **9/10 通过, 1 失败（MCP），exit 1**。

## 6. 残余风险（不得被上面的绿灯冒充）

1. MCP 默认 96/96（G3），等维护者在「写进 tracked 发布流程声明」与「收窄默认档 + 改 ~16 个用例」之间签字。
2. X-05 隐私文档仍在 git 历史且已推送过；`docs/memory-wiki/` 目录在本地磁盘仍存在（已 ignore）。需历史清理决策（force push + 远端可见副本处置），属人工闸门。
3. CI 从未在候选提交上跑过（ahead 12 / 无 tag）；门禁不是 CI 的替代。
4. `data/` gitignored ⇒ LongMemEval 评测线在 CI 上静默缺席（G1 风险面）。
5. `tests/conftest.py` 的 env/settings 单例写入未纳管（G2）；网络出口无绊线监控。
6. `FORGET_IMPORTANCE_EXEMPTION_FLOOR` 无边界校验；X-01 的存活谓词靠人工携带、无静态门禁；X-08 WAL 仍未开；N-02 热路径性能未取证；E2/D-06 时态评测仍未闭环；D-03/D-05/D-08…D-16、N-03、X-06、X-07 本轮未取证。
7. 门禁自身的 `CI_STEP_OWNERS` / `CI_STEP_EXEMPTIONS` 是 tracked 可编辑清单，硬删一行可灭红（结构性替代需改 CI 与门禁共享命令定义）。
8. CI 用 `pip install -e .[dev]` 浮解析、不读 `uv.lock`。

VERDICT: FAIL
BLOCKERS: [G2（tests/conftest.py:19-20 导入期写 os.environ 且不恢复、OPENAI_BASE_URL 默认真实端点、:29-33 改生产 settings 单例无 teardown、test_release_check.py:41 改 PATH、网络出口无绊线）, G3（lantai/cli/mcp.py:3705-3713 默认 return TOOLS = 96/96；规定的 LANTAI_MCP_EXPOSE=all|safe|none 不存在）, X-05（docs/memory-wiki/index.md、overview.md 仍在 git 历史且已推送，仅 untrack + gitignore）, G1 风险面（tests/test_longmemeval_core.py:32 整模块 skipif + data/ 被 gitignore ⇒ CI 评测线静默缺席）, N-07 残余（ahead 12、无 v0.66* tag ⇒ CI 从未在候选提交上跑过）]
EVIDENCE: [干净环境 `pytest tests/ -q -o addopts="" --tb=short` → 2797 passed / 0 failed / 0 error / 0 skipped, exit 0, 266.2s（.scratch/gate/baseline/r13_nokey-nokey.log）; CI 口径含 --cov → 2797 passed, exit 0（权威门禁 passed=2787→2797 计数链与逐提交 tests/ 增删行数一致：93e73ac7 +2586/−55、012ffeea +59/−2、9b062b1b +119、eb507821 +10126、06049a7f +205/−3、2787abc9 +39、553be338 +90）; `python scripts/release_check.py v0.66.0` → 9/10 通过 1 失败 exit 1，失败项 = MCP 默认暴露 96/96（lantai/cli/mcp.py:3705-3713）; 独立探针 23 passed（.scratch/gate/verif_r13，X-01/X-02/R-01/D-01/D-02 复现验证 + 现状锁 2 条）; workflows 抑制项 grep 0 命中; LANTAI_MCP_EXPOSE 0 命中; journal_mode 0 命中 vs README.md:107; git log --all --name-only -- docs/memory-offload docs/memory-wiki 命中 index.md/overview.md; 宿主真实库绊线 0 HIT、%TEMP% 与工作区 0 残留、git status --porcelain = 0]