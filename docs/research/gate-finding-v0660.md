# gate-finding-v0660.md — v0.66.0 发布门禁判定（纯审计，不改产品代码）

- 判定人：Mirasim 门禁 Agent（票 `.scratch/v0.66-next-window-2026-11/issues/05-v0660-release-gate.md` 续13）
- 日期：2026-11-03（Asia/Shanghai）
- 被判定对象：`master` HEAD = `553be338`（`ahead origin/master 12`，无 `v0.66*` tag，未 push）
- 版本线：pyproject/FastAPI/MCP serverInfo 等 6 处引用 + CHANGELOG 最新段 = **0.66.0**（实测一致）
- 本轮性质：**只判定、只记录**。未修改任何产品代码、测试、CI、迁移或默认暴露策略；
  独立验证探针写在 `.scratch/gate/verif_r13/`（gitignore 内，故意不入库：门禁不代开发补测）。
- 续14 追记（2026-11-03）：判定后用户改口授权修复，**G2 已在 `38042aa5` 闭合**（15 处装配期全局裸写 → 唯一登记式写入器 + 会话退出复原 + AST 静态扫描锁，票面口径 2797 → 2807 passed / exit 0）。被判定对象随之变为 `master` HEAD = `38042aa5`（`ahead origin/master 14`，仍无 `v0.66*` tag，未 push）。
  **VERDICT 不随修复自动改判：仍为 FAIL** —— G3（MCP 默认暴露）、X-05（历史隐私）、G1 风险面（CI 评测线静默缺席）、N-07（CI 未在候选上跑过）四条未闭合。
- 续15 追记（2026-11-03）：把票面要求的两条口径对照跑齐，并新查出**生产侧同族病灶**——
  `lantai/cli/mcp.py` 在 **import 期**改写 `sys.path` 与标准流编码（门禁 MCP 探针与全部测试
  都走这条 import）。已在 `533ea8aa` 修复：`lantai/**` 模块级全局改写 AST 普查 3 处 → **0 处**，
  并落 6 例子进程取证 + 网络出口绊线（7 例自身回归）。同轮取证回答「MCP 默认 96/96 有没有
  真的外传」：插桩全量 2820 passed / `armed=true` / `count=1` / **`undeclared=0`**（唯一一条为
  绊线自申报项）。被判定对象随之变为 `master` HEAD = `533ea8aa`（`ahead origin/master 16`，
  仍无 `v0.66*` tag，未 push）。**VERDICT 保持 FAIL**。
- 续16 追记（2026-11-03）：逐条重跑 10 项门禁 + 假绿面普查，抓到一条前 15 轮未登记的
  **真实假绿通道**并已永久关闭：`tests/cognitive/test_cognitive_loop.py::test_belief_persists`
  （BL-03）在「被测函数 `run_reflection()` 不产出 BELIEF candidate」时 `pytest.skip` 弃权——
  把晋升出口摘掉后，同前提的 BL-02 判 `AssertionError`，BL-03 只是 `Skipped`。已改为断言，
  并新增 `tests/test_skip_mask_inventory.py`（6 例）把 `tests/**` 每一处不执行点做成
  **双向相等的申报锁**（文件+形态+reason multiset，当前 6 条全为环境能力类）+「守卫必须引用
  环境能力谓词」规则；植入演示文件即判红、删除即复绿。另测得 MCP 未知/拼错 profile 亦
  **静默回退全量 96/96**（G3 修法需一并处理）。被判定对象 = `master` HEAD `95752dcd`
  （`ahead origin/master 19`，本地/远端最新 tag 仍是 `v0.65.0`）。票面口径 2820 → **2826 passed**，
  权威门禁仍 **9/10（唯一红项 MCP 默认暴露）、exit 1**。**VERDICT 保持 FAIL**。
- 续17 追记（2026-11-03）：先立红基线——本 HEAD（`089b4972`）票面口径 **2826 passed / 0 failed / 0 error / 0 skipped**，exit 0（289.7s），**「v0.66 主干是红的」这一前序论断在该 HEAD 上不成立**；唯一技术红项仍是 G3 MCP 默认暴露。本轮关掉一条前 16 轮只登记未修的**生产侧仓库外路径污染**：`lantai/eval/offline.py::run_offline_eval()` 与 `lantai/eval/deterministic_env.py::staged_deterministic_env()` 各自 `mkdtemp` 后从不回收（修复前 `lantai/` 全树 `rmtree` 命中 = 0），一轮票面全量净增 3 个 `lantai-fq-check-*` + 6 个 `lantai-staged-env-*`，本机累计 981 个 / 452 MB 且逐轮单调增长。已在 `a767fad2` 修复（新增 `lantai/eval/temp_paths.py` 双所有权判据 + 两处站点接线 + 12 例回归测试；红分两级：ImportError → 5 failed/7 passed → 12 passed；三组变异对照均判红）。票面口径 2826 → **2838 passed**（差值 = 新增 12 例），插桩口径同数且 `undeclared=0`，权威门禁在 `a767fad2` 重跑仍 **9/10、唯一红项 MCP 默认暴露 96/96、exit 1**（`WALL=396.2s`、`passed=2838`、`temp_home_removed=True`）。**VERDICT 保持 FAIL**。
- 续18 追记（2026-11-03）：按「发布一致性」验收线查出**一条真实依赖元数据漂移且门禁从不看它**：`pyproject.toml` = 0.66.0，而 tracked 的 `uv.lock` 根包版本仍是 **0.65.0**（0.66 bump 只改了被 `VERSION_REF_SPECS` 覆盖的 5 处引用，没有重新 lock）；外部校验器 `uv lock --locked --offline` 修复前 **exit 1**（`The lockfile at uv.lock needs to be updated`），而 `release_check.py` 的版本一致性项照样报「6 处版本引用」全绿。已在 `803c9f96` 双向闭合：判据新增 `uv.lock 根包版本`（锚定 `name = "lantai"` + 紧邻 version，只取根包），锁由 **`uv lock --offline` 本体重生成**（`git diff` = 1 行，反证其余 128 包与依赖边本就同步）。红分两级（判据未覆盖 2 failed → 判据已加/锁未修仍判红且门禁实跑打印 `[FAIL] 版本一致性 — uv.lock 根包版本（uv.lock）= 0.65.0，期望 0.66.0`，连既有 `test_collect_refs_real_repo_all_match_pyproject` 哨兵也随即点名同一漂移）→ 修复后 `tests/test_release_check.py` **33 passed**、`uv lock --locked --offline` **exit 0**、变异（退回 0.65.0）2 failed / 复原 3 passed。票面口径 2838 → **2840 passed**，**连续三轮**独立隔离全量 2840 / 0 failed / 0 skipped / exit 0（289.4–293.4s），权威门禁 @`803c9f96` = **9/10、唯一红项 MCP 默认暴露 96/96、exit 1**（`WALL=396.1s`、`passed=2840`、版本一致性项现报「7 处版本引用」）。另登记一条新暗病（未修）：`lantai/services/async_ingest_service.py:37` 无守卫取 `_TASKS[task_id]` 抛 `KeyError` 后，`:59` 的失败处理器再抛一次，导致 `:64` `settle(binding, ok=False)` 永不执行——间歇出现在全量 stderr 且从不判红（详见 §6.11）。**VERDICT 保持 FAIL**。

- 续19 追记（2026-11-03）：本轮把审计对象换成**门禁自身**，抓到两条前 18 轮未登记的 fail-open 并已关闭（`6b428143`）。**D1**：ruff 不可用时 `lint_gate_issues()` 打印一行提示就 `return []`，`main()` 随即打印 `[PASS] CI lint 门禁`，且这一项**不登记 skips**——没执行过的检查被记成通过，门禁自己造假绿。**D2**：模块全局账本 `results` 从不清空，同进程第二次 `main()` 实测把分母算成「**16/19 通过, 2 失败**」（第一次 8/10），通过率/失败条数全部失真，而既有测试对 `main()` / `check()` / `results` 的覆盖是 **0 条**。修法：缺 ruff 走 `announce_skip` + `skips` 账本上提到 lint 之前 + PASS 分支加守卫（与既有 `Git 状态` 同口径，未取证一律 exit 1）；`main()` 入口 `results.clear()`。另按验收线补齐 **D3**：每条 `[FAIL]` 现在自带「失败阶段｜根因分类｜最小复现」，分类落在 8 类固定词表内，清单缺项显式标「未分类」，入口另打印本次门禁命令。TDD：7 例先红（`7 failed, 33 passed`）→ 修复后 `tests/test_release_check.py` **40 passed**；变异对照 M1–M4 逐个抽掉牙齿分别判红 1/1/2/2 例。票面口径 2840 → **2847 passed / exit 0**；权威门禁在 `6b428143` **连跑三轮**逐项同结论、汇总逐字相同（9/10、唯一红项 MCP 96/96、exit 1、`passed=2847`、`skips=0`）——「连续 3 次通过」在 G3 由维护者解除前技术上不可能达成，本轮达成的是「3 次可复现、同结论、无缓存污染」；同类扫描再抓出一条**D4**并已一并关闭（`2c197571`）：`addopts_fidelity_issues()` 在「门禁命令不再钉 `-o addopts=`」或「pyproject 读不到」时都 `return []`——核对被拆掉这件事自己永远绿；两处改显式判红 + 3 例（含「真实仓库现况不得误伤」的反向保险）。最终代码 HEAD 的权威门禁三连（`r19_authfinal{1,2,3}.log`）= 9/10、1 失败（MCP 96/96）、exit 1、`passed=2850`、`skips=0`、汇总逐字相同。**VERDICT 保持 FAIL**。
- 续20 追记（2026-11-03）：正面回答票面那条「只在正序全量绿 ⇒ 必须单独排查隔离污染」验收线，查出一条**确定性顺序污染**并关闭（`cea3bd84`，**纯测试侧，未改一行生产代码**）。`test_seam1_jiaochou_service` 在**逆序全量**与**乱序全量（seed=20261103）**各判红一条（`1 failed, 2853 passed / exit 1`，工件 `r20_reverse_pre-nokey.log` / `r20_shuffled_pre-nokey.log`），单跑与正序全量绿（`r20_forward_pre-nokey.log` = 2854 passed / exit 0）；两次判红是同一条用例、同一断言，前置文件集不同 ⇒ 不是 flaky。根因三层叠加：`lantai/cognition/jiaochou.py:171-172` 扫描无 ORDER BY（行序＝插入序）、`:200-203` 凑够 `limit` 就 break 而 `:205` 才排序（`limit` 真实语义是「先扫到的 N 对」而非 top-N）、`tests/conftest.py:324-371` 会话级共享 SQLite；用例把自己撒进公共 `default` 租户，于是「窗口里有没有我」成了跑测顺序的函数。只读诊断插件（`.scratch/gate/r20/diag.py`，`-p diag`）实测逆序跑到该用例时 `active_total=46 / owner_null=0 / swarm_shared=0 / accessible_for_foreign_tenant=0 / pairs_before_seed=0` ⇒ 换专用租户是**隔离**（对手被 `_check_access` 挡在门外），不是绕过。TDD：Step1 红 `1 failed, 5 passed`（判红者正是新写的隔离锁）→ Step2 绿 `6 passed`，断言由 `total_pairs >= 1` **收紧**为 `== 1`；机制锁用私有库钉住截断事实，变异（摘掉两层 `break`）当场判红 `assert 277 == 10`，`jiaochou.py` 按字节还原（sha 同为 `d68c5bb7636adc61…`）。同轮另关一处测试护栏自身的泄漏：`tests/test_release_check.py` 的门禁账本护栏原是裸 `yield`，用例抛错时还原语句不执行、残渣串给后续用例，改 `try/finally` + 可直调生成器 `_ledger_guard()`，红 1 failed（残渣实测 `[('FAIL','用例内炸之前的污染','x')]`）→ 绿 47 passed，变异 T1/T2/T3 分别判红 2/3/1 例。**登记不修（§6.13）**：J-01 `limit` 非 top-N；J-02 `_check_access` 对 `user_id IS NULL` / `swarm_shared` 无条件放行且 `Principal.tenant_id=None` 时租户比对整条跳过 ⇒「按租户隔离」对默认身份是空操作。修复后正序/逆序/乱序三口径全量各 **2856 passed / 0 failed / exit 0**，票面口径 **2856 passed / exit 0 / 289.5s**，权威门禁三连 @`cea3bd84` = **9/10、1 失败（MCP 96/96）、exit 1、passed=2856**、汇总/失败项/exit/HEAD 四项逐字相同。**VERDICT 保持 FAIL**。
- 续21 追记（2026-11-03）：本轮审计对象再次回到**门禁自己**，抓到一条比续19 D1/D2 更阴的缺陷——**红灯提示给维护者开的处方是假的**（`6c616c3d`）。直调 `lantai/cli/mcp.py:3705-3713` 实测：不设 profile ⇒ 96/96；`hermes|core|slim|minimal` ⇒ 收到 `HERMES_CORE_TOOLS`（5 个，大小写/空格不敏感）；`safe|none|拼错|空串` ⇒ **96/96，静默、无告警、不报错**；`MCP_TOOL_PROFILE` 是生效别名；`LANTAI_MCP_EXPOSE` 全仓 grep 0 命中。而 `mcp_exposure_issue()` 旧文案写着「或在部署侧设 `LANTAI_MCP_PROFILE` 并把它写进发布流程」——本项判据只看默认档（`isolated_probe_env()` 主动剥掉 profile 变量，既有哨兵钉着），照做**永远不会**让本项变绿；若维护者挑个「看着安全」的取值，运行时暴露面一点没变却以为收窄了。修法只动提示文本、不动判据：处方必须点名实测真能收窄的那组取值，并写破「只看默认档 / 其余静默全量无告警 / `LANTAI_MCP_EXPOSE` 不存在」。TDD 走了一次自我证伪：第 1 版新用例只做子串打卡，变异 M-B（处方塞进 `safe`）**没咬住** ⇒ 改成双向对账（提示点名集合 == monkeypatch 真调测得的能收窄集合，另断言 `safe/none` 不收窄、不设 profile 是全量）；红 `1 failed, 47 passed` → 绿 **48 passed**，变异 M-A/M-B/M-C/M-D 各判红 1 例、逐字节还原。**顺序无关性补验**（续20 自认的残余）：新增节点级乱序探针（模块内类序＋方法序，守住「不丢用例／文件序不动／类相邻」并由真实 `--collect-only` 对账证明探针非空转），seed=20261105 得 `2856 passed / exit 0`、文件×节点组合乱序 seed=20261106 得 `2856 / exit 0`、逆序文件＋节点乱序 `2856 / exit 0`；本轮代码 HEAD 再跑票面/逆序/组合乱序（seed=20261107）三口径各 **2857 passed / exit 0 / FAILED=0**，未再挖出第二条顺序依赖。**过程自曝两处**：①一次整块替换误删既有用例 `test_isolated_probe_env_isolates_home_and_forbids_dev_mode`（收集数 48→47 暴露），已按 HEAD 原文逐字恢复，`git diff -- tests/` 只有新增行；②「加了测试但测不到东西」由变异当场暴露，今后为缺陷补的测试必须配变异组。**权威门禁三连 @`6c616c3d`** = 9/10、1 失败（MCP 96/96）、exit 1、`passed=2857`、`[SKIP]`=0、WALL 390.9/395.3/399.1s、汇总/失败项/exit/HEAD 四项逐字相同。本轮**未解除任何阻断**（G3 本体、默认档、未知值 fail-open 一律未动），只让红灯不再说谎并补验顺序。**VERDICT 保持 FAIL**。


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
- 续15 副作用取证（默认暴露是**策略缺陷**还是**已发生的外泄事件**，此前无人验证）：
  - 门禁探针 `_MCP_PROBE_CODE`（`scripts/release_check.py:246`）只做 `from lantai.cli import mcp`
    + 读注册表：不执行工具、不绑端口、不注册服务。
  - 测试路径**确实大量执行**工具：`tools/call` 字面量分布在 34 个测试文件（112 处），全部在同一轮
    插桩全量里真实跑过；插桩口径 = 票面同命令 + `-p network_egress_tripwire`（只监听不阻断，
    `connect` 原样放行），结果 `count=1 / undeclared=0`，唯一一条是自申报的绊线自检项
    （`192.0.2.1:9`，RFC 5737 保留不可路由地址）⇒ 本进程内**未发生未申报出网**。
  - 宿主真实数据目录：权威门禁第 9 项带 `real_home_tripwire`，仍 PASS（全程未连宿主库），
    `temp_home_removed=True`。
  - 因此 G3 记为「默认档全量 + 无 `LANTAI_MCP_EXPOSE` 三档开关」的契约违反，**不**升级为「已外泄」，
    也**不**因「没外泄」而弱化：潜在暴露面（任何拿到该进程 stdio 的对端可调全部 96 工具）原样保留。
  - 边界：绊线只观测本进程，子进程（例如起 uvicorn 的服务冒烟）不受管；环回不计出网；
    `scripts/*.py` 的 `sys.path.insert` 属脚本入口语义，未纳入 `lantai/**` 的 0 判据。
- 续16 实测（独立子进程探针，不经门禁代码）：默认 `96/96`；`LANTAI_MCP_PROFILE=hermes` ⇒ `5/96`；
  **`LANTAI_MCP_PROFILE=HERMES-TYPO`（拼错）⇒ 仍 `96/96`** ⇒ 收窄默认档时必须同时让未知 profile
  fail-closed，否则一个大小写笔误就把暴露面从 5 放回 96，且当前无任何告警。
- 结论：**G3 违反**（默认档 = 全量；且没有 `all|safe|none` 显式三档开关，只有 profile 名 + 未知值即回退全量）。
  在册冲突：`map.md:41` N-04 记为「有意语义、不开票」——门禁不接受「有意」而无人签字的默认全暴露。

### 2.1 续21 实测补正（直调 `_get_active_tools()`，不 mock；`.scratch/gate/r21/mcp_narrow.py`）

| 输入 | 返回 | 判读 |
|---|---|---|
| 不设 profile | 96/96 | 默认档仍全量（G3 本体，未动） |
| `LANTAI_MCP_PROFILE=hermes/core/slim/minimal` | 收到 `HERMES_CORE_TOOLS`（5） | 唯一真能收窄的一组；`HERMES`/`" hermes "` 同样生效 |
| `=safe` / `=none` / `=typo-value` / `=""` | 96/96 | **静默 fail-open**，无告警、不报错 |
| `MCP_TOOL_PROFILE=core` / `=safe` | 5 / 96 | 生效别名，同样只认那一组 |
| `LANTAI_MCP_EXPOSE=all\|safe\|none` | 96/96 | 该变量在 `lantai/` 内 grep **0 命中**，不存在 |

推论：门禁这一项只看默认档（探针剥变量），所以「部署侧设 `LANTAI_MCP_PROFILE`」既不能变绿、取值不当也不收窄——
旧提示把这条写成处方，属于门禁自己递出的假绿通道，已在 `6c616c3d` 只改文本关闭（判据一字未放宽）。

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
| **G1** 全量 fail=0 error=0，无 skip/xfail 掩盖 | **部分违反** | 干净环境（剥密钥）`pytest tests/ -q -o addopts="" --tb=short` → **2797 passed / 0 failed / 0 error / 0 skipped**，exit 0，266.2s（`.scratch/gate/baseline/r13_nokey-nokey.log`）。CI 口径（含 `--cov`）同数 2797（上一轮权威门禁实测，`wall=342.6s`）。**但存在制度外 skip 面**：`tests/test_longmemeval_core.py:32` 整模块 `skipif(not DATASET.exists())`，而 `data/` 在 `.gitignore:70` ⇒ **CI 上这条评测线恒 0 采集且不报错**；另有 `test_wheel_ships_ui.py:19`（无 uv 即跳）、`test_release_check.py:43`（无 git 即跳）、`test_mcp_principal_wiring.py:617,657`（AUTODREAM 关则跳）、`test_longmemeval_core.py:141`（预注册缺失跳）。本机全部实际执行（skip=0），故 G1 主口径成立；**评测/构建线的「CI 静默不可见」记为残余风险**，非本轮放行理由。**续16 补（本轮新登记的真实掩盖，已修 `95752dcd`）**：上表四类之外另有一处`tests/cognitive/test_cognitive_loop.py:171`（BL-03 `test_belief_persists`）以**被测函数返回值**为条件 `pytest.skip`——晋升链路打挂后 BL-02 判红、BL-03 弃权（复现 `.scratch/gate/r16/repro_bl03_mask.py`：`MASKED_AS_SKIP`）。已改为断言，并落`tests/test_skip_mask_inventory.py`：`tests/**` 全部不执行点逐条申报（双向相等，实测 6 条，行号随本轮漂移：`test_release_check.py` 现 `:44`）+ 守卫必须引用环境能力谓词 + 扫描器不许空转；植入/删除演示弃权点分别判红/复绿（牙齿对照）。`xfail` 全仓 0 处 |
| **G2** 无测试污染生产全局 | **违反 → 已修（`38042aa5`，票 05 续14）** | ① `tests/conftest.py:19-20` **导入期**写 `os.environ.setdefault("OPENAI_API_KEY","test-key")` 与 `("OPENAI_BASE_URL","https://api.openai.com/v1")`，全程不恢复：ambient 有真实 key 时 setdefault 不覆盖 ⇒ 测试可打到**真实端点**，且门禁的绊线只盯 `sqlite3.connect` 路径（`tests/support/real_home_tripwire.py`），**网络出口无人监控**；② `tests/conftest.py:29-33` 直接改生产单例 `settings.FEATURE_OBSIDIAN/WIKI/WORK_ITEMS/VISION/TERMINAL = True`，无 teardown ⇒ 全量绿不代表默认配置行为；③ `tests/test_release_check.py:41` 永久改 `os.environ["PATH"]`；④ `tests/test_recall_chain.py:137/151` 用 `__enter__/__exit__` 写删 `LANTAI_INTENT_OFF`（异常路径依赖 with，且会吞掉用户原值）。**正向**：模块绑定污染有结构检测（`_GUARDED_BINDINGS` 10 项 + `_repair_canonical_bindings` + `unexpected_binding_residue`，未申报残留判红，申报白名单仅 1 条自测用例），且 A 档探针 `test_g2_hybrid_module_globals_unchanged_by_search` 证明 hybrid 全局不再被 hook/检索路径改写；宿主真实库 0 连接（绊线 2797 用例 0 HIT），`%TEMP%`/工作区无残留。**续14 修复**：AST 静态扫描对 HEAD 取证 **15 处裸写/5 文件**（比本节点名的 4 处多 11 处，并抓出审计漏网的 `test_coalesce_session_key.py`），全部收进 `tests/support/harness_state.py` 这个 tests/ 唯一登记式写入器 + `pytest_unconfigure` 会话退出精确复原（原值缺失才删除，不吞宿主配置）；`OPENAI_API_KEY/OPENAI_BASE_URL` 由 `setdefault` 改为**强制覆盖**成不可路由黑洞 `http://127.0.0.1:9/v1`，并直调生产校验取证：黑洞被 `validate_api_url()` 拒、旧端点 `https://api.openai.com/v1` 被放行（= 旧写法确实留着真外传通道）；`PATH`/`LANTAI_INTENT_OFF`/`sys.path.insert` 三处手写全局分别改 `monkeypatch.setenv`/`env_context`/`importlib` spec 直载；`LANE_COALESCE_PROFILES` 不再原地 mutate 嵌套 dict。回归锁：`tests/test_harness_global_state_isolation.py` 10 例（静态扫描 + 进程内事实 + 真实子会话退出复原 + 植入/合规双对照 + 两次变异）。票面口径 2797 → **2807 passed, exit 0**。**续15 追加（生产侧同族病灶，已修 `533ea8aa`）**：G2 的判据原先只盯「测试改写生产全局」，本轮反向查「生产模块 import 期改写自己所在进程的全局」，命中 3 处全在 `lantai/cli/mcp.py`（`:18 sys.path.insert(0, <仓库根>/lantai)`、`:25-26 sys.stdin/stdout.reconfigure("utf-8")`），引入提交 `bea001c1`。那条 insert 注释声称兜「Hermes 拉 MCP 时 cwd 不可控」，实际插的是**包目录本身**（`import lantai` 需要仓库根），且本文件几行后就要 `from lantai.core... import` ⇒ 从未兜住声称场景；实测给每个 importer 的 `sys.path` **头部**（先于 site-packages）塞 16 个影子解析名（core/models/services/retrieval/gate/storage/llm/ingestion/memory/parameters/observability/api/eval/evolution/parsing/cli），而仓库里平铺 `from core|…` 消费者数 = 0 ⇒ 风险有主、收益为零。stdio UTF-8 是真实需求（Windows GBK 下「你好」→「浣犲ソ」，中文检索零命中），但属**进程入口**，已收进 `main()` 首行调用的 `_configure_stdio_utf8()`。判据：`tests/test_mcp_import_side_effects.py`（6 例，事实取证跑在隔离子进程里，不用 monkeypatch 掩盖被修路径；修复前 **4 failed/1 passed**，修复后 6 passed；在最终 HEAD 上把生产文件临时退回 `533ea8aa~1`（测试文件不动）重放 before = **4 failed, 2 passed, exit 1**（四条判据各自命中：sys.path 被插入包目录 / 16 个影子名 / `assert 'gbk' == 'utf-8'` / AST 锁命中 `:18/:25/:26`），`git checkout --` 复原后 13 passed；把 `main()` 内调用改成 `pass` 的变异对照 2 failed——`UnicodeDecodeError` byte `0xcb` 正是 GBK 字节）。`lantai/**` 模块级全局改写 AST 普查：3 处 → **0 处**。 |
| **G3** MCP 必须有显式开关，禁止默认全量 | **违反** | 见 §2：`LANTAI_MCP_EXPOSE` 不存在；`_get_active_tools()` 未设 profile 即 `return TOOLS`（96/96）。续15 补测：默认全暴露在测试/门禁路径内**未产生未申报出网**（插桩全量 2820 passed，`undeclared=0`），故仍按契约违反处置，不自批改默认档。**续21 实测补正**：真能收窄的取值只有 `core\|hermes\|minimal\|slim`（收到 `HERMES_CORE_TOOLS`），`safe\|none\|拼错\|不设` 静默保持 96/96；本项判据只看默认档，故旧提示「在部署侧设 `LANTAI_MCP_PROFILE`」是无效处方——已由 `6c616c3d` 改正（提示只准写实测有效的取值，由双向对账用例钉住），默认档与未知值 fail-open **本体仍未改**，仍需维护者签字 |
| **G4** CI 不得用 `--deselect`/`if: false` 跳过 integration/e2e | **通过** | `Select-String -Path .github\workflows\*.yml -Pattern 'deselect','continue-on-error','if: false','|| true','--ignore','-k ','xfail' -SimpleMatch` → **0 命中**；两个 workflow 都跑全量 `pytest tests/`；`ci.yml` 的 build job `needs: tests`（红灯不出镜像）。注意：仓库里没有独立的 "integration" job/标记，全量即唯一口径（不是被跳过，而是未分层——记入残余） |
| **G5** 每个修复必须有新增测试，禁止改旧断言凑绿 | **通过** | `git show --numstat -- tests/`：`93e73ac7` +2586/−55（24 文件，含 `test_x01_*`、`test_forgetting_exempt_archive.py`、`test_probe_reply_cjk_semantics.py`、`test_plugin_short_query_persona.py`）；`012ffeea` +59/−2；`9b062b1b` +119/−0；`eb507821` +10126/−0（含 DDL-only 快照，实测 `contains_row_data=False`、CJK 0、最长 token 是索引名）；`06049a7f` +205/−3；`2787abc9` +39/−0；`553be338` +90/−0。本轮另以变异对照证明判据有牙（前序 `hybrid.embed` 去 timeout ⇒ 新断言判红） |
| **G6** 门禁脚本不得 `|| true`/吞退出码 | **通过** | workflows 无 `|| true`/`continue-on-error`；`scripts/run_forgetting_quality.py:148` `return 0 if ok else 1`（非 `--check` 的报告分支才 return 0）；`scripts/release_check.py` 的 `summarize()` 把 SKIP 计入分母且返回非 0（实测 `--skip-suite` → `8/10 通过, 1 失败, 1 跳过`，exit 1），所有探针异常路径返回**判红**文案而非放行 |

## 5. 全量数字（Step 2.3）

| 口径 | passed | failed | error | skipped | exit |
|---|---|---|---|---|---|
| `pytest tests/ -q -o addopts="" --tb=short`（无 key、临时 home） | 2797 | 0 | 0 | 0 | 0 |
| `pytest tests/ -q --cov=lantai --cov-report=term-missing`（CI 口径，`API_KEY=test-key`） | 2797 | 0 | 0 | 0 | 0 |
| `.scratch/gate/verif_r13`（独立探针） | 23 | 0 | 0 | 0 | 0 |
| 续14 before（`cb9b9f57`，未改动，票面口径） | 2797 | 0 | 0 | 0 | 0 |
| 续14 after（`38042aa5`，票面口径） | **2807** | 0 | 0 | 0 | 0 |
| 续14 after（`38042aa5`，CI 口径含 `--cov`，权威门禁内实测） | 2807 | 0 | 0 | 0 | 0 |
| 续15 早段（`a488f278` + mcp 6 例，票面口径） | 2813 | 0 | 0 | 0 | 0 |
| 续15（`a488f278` + 出口绊线 5 例，票面口径） | 2818 | 0 | 0 | 0 | 0 |
| 续15 after（`533ea8aa`，票面口径） | **2820** | 0 | 0 | 0 | 0 |
| 续15 after（`533ea8aa`，票面口径 + `-p network_egress_tripwire`） | 2820 | 0 | 0 | 0 | 0 |
| 续15 after（`533ea8aa`，CI 口径含 `--cov`，权威门禁内实测） | 2820 | 0 | 0 | 0 | 0 |
| 续16 before（`0a98b916`，票面口径，本轮重测） | 2820 | 0 | 0 | 0 | 0 |
| 续16 after（`95752dcd`，票面口径） | **2826** | 0 | 0 | 0 | 0 |
| 续16 after（`95752dcd`，票面口径 + `-p network_egress_tripwire`） | 2826 | 0 | 0 | 0 | 0 |
| 续16 after（`95752dcd`，CI 口径含 `--cov`，权威门禁内实测） | 2826 | 0 | 0 | 0 | 0 |
| 续17 before（`089b4972`，票面口径，本轮重测） | 2826 | 0 | 0 | 0 | 0 |
| 续17 after（`a767fad2`，票面口径） | **2838** | 0 | 0 | 0 | 0 |
| 续17 after（`a767fad2`，票面口径 + `-p network_egress_tripwire`） | 2838 | 0 | 0 | 0 | 0 |
| 续17 after（`a767fad2`，CI 口径含 `--cov`，权威门禁内实测） | 2838 | 0 | 0 | 0 | 0 |
| 续18 before（`1afa0595`，票面口径） | 2838 | 0 | 0 | 0 | 0 |
| 续18 after（`803c9f96`，票面口径，稳定第 1/2/3 轮） | **2840** | 0 | 0 | 0 | 0 |
| 续18 after（`803c9f96`，CI 口径含 `--cov`，权威门禁内实测） | 2840 | 0 | 0 | 0 | 0 |
| 续19 before（`5387654a`，票面口径；主干不红） | 2840 | 0 | 0 | 0 | 0 |
| 续19 D1–D3 after（`6b428143`，票面口径，`r19_full`） | 2847 | 0 | 0 | 0 | 0 |
| 续19 D4 after（`2c197571`，票面口径，`r19_d4_full`） | **2850** | 0 | 0 | 0 | 0 |
| 续19 after（`2c197571`，CI 口径含 `--cov`，三轮权威门禁内实测） | 2850 | 0 | 0 | 0 | 0 |
| 续20 before（`484222a5`，正序全量 322 文件显式列表，`r20_forward_pre`） | 2854 | 0 | 0 | 0 | 0 |
| 续20 before（`484222a5`，**逆序**全量，`r20_reverse_pre`） | 2853 | **1** | 0 | 0 | 1 |
| 续20 before（`484222a5`，**乱序** seed=20261103，`r20_shuffled_pre`） | 2853 | **1** | 0 | 0 | 1 |
| 续20 after（`cea3bd84`，票面口径，`r20_full`） | **2856** | 0 | 0 | 0 | 0 |
| 续20 after（`cea3bd84`，正序/逆序/乱序三口径各一次） | **2856** | 0 | 0 | 0 | 0 |
| 续20 after（`cea3bd84`，CI 口径含 `--cov`，三轮权威门禁内实测） | 2856 | 0 | 0 | 0 | 0 |
| 续21 before（`ecd0d04d`，节点级乱序 seed=20261105） | 2856 | 0 | 0 | 0 | 0 |
| 续21 before（`ecd0d04d`，文件×节点组合乱序 seed=20261106 / 逆序文件＋节点乱序） | 2856 | 0 | 0 | 0 | 0 |
| 续21 after（`6c616c3d`，票面口径 `r21_full`） | **2857** | 0 | 0 | 0 | 0 |
| 续21 after（`6c616c3d`，逆序全量 / 组合乱序 seed=20261107） | **2857** | 0 | 0 | 0 | 0 |
| 续21 after（`6c616c3d`，CI 口径含 `--cov`，三轮权威门禁内实测） | 2857 | 0 | 0 | 0 | 0 |

权威门禁（`python scripts/release_check.py v0.66.0`，无旗标，干净树）= **9/10 通过, 1 失败（MCP），exit 1**。续14 在 `38042aa5` 重跑同结论（`WALL=367.6s`、`passed=2807`、`temp_home_removed=True`）；续15 在 `533ea8aa` 重跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=376.4s`、`passed=2820`、`temp_home_removed=True`），并在文档提交 `551a6086` 上再跑一次同结论（`WALL=372.7s`、`passed=2820`、`temp_home_removed=True`）——其后的文档提交只是追加记录，与 10 项判据无因果（判据不含 markdown 内容检查）——计数差分 +6（mcp import 副作用）+5（出口绊线）+2（申报哨兵）= 2807→2820，全部为新增用例，无删测/skip/xfail/断言放宽；续16 在 `95752dcd` 再跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=390.4s`、`passed=2826`、`temp_home_removed=True`），计数差分 `2820 → 2826 = +6`（不执行点申报锁），BL-03 由「可弃权」变「必判红」不改变当前通过数。 续17 在 `a767fad2` 再跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=396.2s`、`passed=2838`、`temp_home_removed=True`），计数差分 `2826 → 2838 = +12`（`tests/test_eval_temp_dir_cleanup.py` 临时目录回收回归锁），同样全为新增用例，无删测/skip/xfail/放宽断言。 续18 在 `803c9f96` 再跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=396.1s`、`passed=2840`、`temp_home_removed=True`），计数差分 `2838 → 2840 = +2`（`uv.lock` 判据 + 合成仓库决定性反例），并额外满足「连续 ≥3 轮稳定」这条验收线（2840/2840/2840，exit 全 0）。 续19 的 D1–D3 收口在 `6b428143` 连跑三轮（`r19_auth{1,2,3}.log`：9/10、1 失败 MCP、exit 1、`passed=2847`、`skips=0`、WALL 392.4–398.8s），加入 D4 后在**最终代码 HEAD** `2c197571` 重取三连（探针 `.scratch/gate/r19/auth3_final.py`，`r19_authfinal{1,2,3}.log`）：三轮均 **9/10 通过、1 失败（MCP 默认暴露 96/96）、exit 1、`passed=2850`、`skips=0`、`temp_home_removed=True`**，WALL 397.8/398.2/399.8s，且**汇总行逐字相同、失败项集合逐字相同**——本轮修的正是「同进程/同配置重跑会不会变数」这条确定性；票面口径 2840 → 2850 = +10（D1–D3 七例 + D4 三例），全为新增用例，无删测/skip/xfail/放宽断言，warning 数与基线同为 2 条同源告警。 续20：三连改在 `cea3bd84` 重跑（`r20_authfinal{1,2,3}.log`）同结论 **9/10、1 失败（MCP 96/96）、exit 1、passed=2856、temp_home_removed=True**，WALL 400.1/396.4/400.5s，汇总/失败项集合/exit/HEAD 四项逐字相同；票面口径 2850 → 2854（账本护栏 4 例）→ **2856**（校雠 2 条锁），无删测/skip/xfail/放宽断言（唯一断言变动是把 `>= 1` **收紧**成 `== 1`），warning 仍为 2 条同源告警。

## 6. 残余风险（不得被上面的绿灯冒充）

1. MCP 默认 96/96（G3），等维护者在「写进 tracked 发布流程声明」与「收窄默认档 + 改 ~16 个用例」之间签字；续16 补一条硬约束：**未知/拼错 profile 也须 fail-closed**（现静默回退全量 96/96，无告警）；续21 再补一条实测口径：能收窄的取值只有 `core|hermes|minimal|slim`，且本项判据只看默认档，「部署侧设变量」不是出路——旧提示把它写成处方这件事已修（`6c616c3d`），默认档本体未修。
2. X-05 隐私文档仍在 git 历史且已推送过；`docs/memory-wiki/` 目录在本地磁盘仍存在（已 ignore）。需历史清理决策（force push + 远端可见副本处置），属人工闸门。
3. CI 从未在候选提交上跑过（续16 实测本地 `master` 领先 `origin/master` 19 个提交，此后每轮文档提交继续 +1；本地/远端最新 tag 均为 `v0.65.0`，无 `v0.66*`）；门禁不是 CI 的替代。
4. `data/` gitignored ⇒ LongMemEval 评测线在 CI 上静默缺席（G1 风险面）；另 `pyproject.toml`
   `filterwarnings = ["ignore::DeprecationWarning"]` 全局抑制一类告警（不影响退出码，但会压低
   未来弃用信号；本轮未动它，改它需单独立项核对存量告警）。
5. ~~`tests/conftest.py` 的 env/settings 单例写入未纳管（G2）~~ 已由 `38042aa5` 纳管并复原（票 05 续14，见 §4 G2 行）；**网络出口仍无绊线监控**——黑洞端点把「失败即外传」降级成「失败即断网」，但那不是可取证的出站事件观测；**只监听绊线已落地**为 `tests/support/network_egress_tripwire.py`（票 05 续15，含 `DECLARED_EGRESS_PROBES` 申报哨兵与隔离子进程「牙齿」反证），但**尚未成为 `scripts/release_check.py` 的判据**——目前靠人工执行插桩口径取证；下一条该做的是把它变成第 11 项（判据取 `undeclared == 0`，报告缺失按未取证 = FAIL）。
6. `FORGET_IMPORTANCE_EXEMPTION_FLOOR` 无边界校验；X-01 的存活谓词靠人工携带、无静态门禁；X-08 WAL 仍未开；N-02 热路径性能未取证；E2/D-06 时态评测仍未闭环；D-03/D-05/D-08…D-16、N-03、X-06、X-07 本轮未取证。
7. 门禁自身的 `CI_STEP_OWNERS` / `CI_STEP_EXEMPTIONS` 是 tracked 可编辑清单，硬删一行可灭红（结构性替代需改 CI 与门禁共享命令定义）。
8. CI 用 `pip install -e .[dev]` 浮解析、不读 `uv.lock`——**锁文件自身一致性已于续18 闭合**（`uv.lock` 根包版本纳入 `VERSION_REF_SPECS`，`uv lock --locked --offline` exit 0），但「CI 不用锁解析」这一条依旧成立：依赖集在 CI 上仍是浮动解析，发布可复现性未闭环。
9. **不执行点申报锁已落地**（`tests/test_skip_mask_inventory.py`，续16）：新增/改名/改 reason/删除
   skip 都会判红。它管住的是「测试侧弃权」，不覆盖「评测线因数据缺失整体缺席」那条（§6.4）。
10. **生产评测代码留 `%TEMP%` 临时目录**（续15 查出、**续17 已修 `a767fad2`**）：`lantai/eval/offline.py`（前缀 `lantai-fq-check-`）与 `lantai/eval/deterministic_env.py`（前缀 `lantai-staged-env-`）此前 `mkdtemp` 后不 `rmtree`（修复前 `lantai/` 全树 `rmtree` 命中 = 0）⇒ 本机累计 981 个目录 / 452 MB、逐轮单调增长（续15 记 915/442MB → 续17 开工 286+686 → 一次全量后 289+692）。修复：`lantai/eval/temp_paths.py` 以「前缀在册 + temp 根直接子目录」双判据做所有权识别，`run_offline_eval` 走 `engine.dispose()` + 即时回收（实测全量差分 **+3 → +0**），`staged_deterministic_env` 走 `created` 标志只回收自建目录（调用方 `tmp_dir`/`data_dir` 属跑测证据，绝不删）。**残余（诚实边界，非零残留）**：`chromadb` 0.6.3 把连接池挂在类属性 `SharedSystemClient._identifier_to_system` 上，进程存活期内删不掉被 Chroma 用过的目录（`gc.collect()` + 四轮退避 `rmtree` 实测仍失败），故该家族改由「下一次分配时清扫超龄（>1h）同前缀目录」回收——泄漏上界从「无限」压成「滚动 1 小时窗口内的目录数」，实测一轮全量后遗留 6 个、门禁后 12 个，由生产 API `reclaim_stale()` 跨进程实测 6→0 / 12→0。未选择「清 Chroma 全局 client 缓存」这条捷径：清后 `PersistentClient(同路径)` 会新建第二套 system/连接池，正是 `tests/conftest.py:318` 记过的Chroma 顺序污染面。回归锁 `tests/test_eval_temp_dir_cleanup.py`（12 例，含反向保险与三组变异对照）。
11. **续18 新登记（未修，非本轮引入）**：异步对话摄取的失败处理器自身会再抛异常——`lantai/services/async_ingest_service.py:37` 无守卫直取 `_TASKS[task_id]`（模块级进程内注册表 `:22`），键缺失即 `KeyError: <task_id>`；`except` 分支 `:59-61` 又写同一个键，二次异常被 ThreadPoolExecutor 吞掉，于是 `:64` 的 `settle(binding, ok=False)` **永不执行**，与 `:62-63` 注释承诺「失败必须释放键，重试才能重新执行」直接矛盾。取证：同一隔离口径的历史全量日志逐份扫描（`.scratch/gate/r17/scan_async.py`），`r10_cov-ci / r14_before / r14_after / r15_suite / r15_final3 / r17_baseline / r18_stab2` 命中、`r18_stab1 / r18_stab3` 未命中 ⇒ **间歇性且从不判红**（典型「静默失败」形态）。建议独立小单：注册表改 `setdefault`/存在性检查、失败分支兜底后仍必须 `settle(ok=False)`、并补不 mock 的冒烟断言。
12. **门禁自身的两处 fail-open（D1/D2）已于 `6b428143` 关闭（续19）**，但记账仍是「模块全局 + 入口清空」这一最小改法：真正局部 ledger 化要动 ~25 处 `check()` 调用面，留给独立小单。在那之前，**任何绕过 `main()` 直接调 `check()` 的新代码（含未来的门禁子命令）都会重新引入跨调用累积**。D3 的诊断清单 `FAILURE_DIAGNOSIS` 是手工登记的字典，新增门禁项若忘记登记会打印「未分类」（显式可见，不静默），且已由 `test_every_gate_item_exercised_by_main_has_a_diagnosis_entry` 反向锁住不许扩大盲区。同轮追加的 **D4**（`addopts_fidelity_issues()` 的两个静默 `return []`）也已在 `2c197571` 改为显式判红；但它提醒一件事：**`return []` 在这套门禁里既是「没问题」也是「没法查」的返回值**，二者共用一个空列表本身就是结构性缺陷，长期解法是给每项区分 `issue / no-issue / not-measurable` 三态（独立小单）。
13. **校雠读侧两条结构性缺陷（续20 登记，未修，非本轮引入）**：**J-01** `lantai/cognition/jiaochou.py:200-205` 先截断后排序，`limit` 不是 top-N——用户问「最该合并的 10 对」，拿到的是「前 10 个碰巧相似的」；修法要么全量配对后排序截断（O(n²) 成本需量级护栏），要么把候选生成下推到 SQL，属功能票（放宽 `limit` 属本票禁改的阈值操纵，改 `break` 会动返回契约与成本曲线）。已由机制锁钉住事实，变异摘掉 `break` 当场判红。**J-02** `:79-100` `_check_access` 对 `user_id IS NULL` 与 `shared_scope == "swarm_shared"` 无条件放行，且 `Principal.tenant_id is None` 时租户比对整条跳过 ⇒「按租户隔离」对默认身份是**空操作**；本轮专用租户边界成立靠的是实测数据分布（`owner_null=0 / swarm_shared=0`），不是代码保证，故已用 `total_pairs == 1` 把它变成可判红哨兵（一旦有 NULL 属主行进入共享测试库并成对，seam1 直接判红而非悄悄挤位）。**取证纪律变更**：自本 HEAD 起「全量绿」结论必须同时给出正序/逆序/定种子乱序三口径（`.scratch/gate/r20/order3.py`），单靠正序绿不再可引用；（续20 实测四种文件顺序——正序、逆序、seed=20261103、seed=20261104——修复后均 2856 passed / exit 0）；修复前口径一律带 `_pre` 后缀，避免同名覆盖（本轮发生过一次，见工单续20 §4.1 的自我披露）。
14. **门禁红灯的处方失实（续21 查出，`6c616c3d` 只修文本）**：`mcp_exposure_issue()` 曾建议
   「在部署侧设 `LANTAI_MCP_PROFILE`」，实测既不能让本项变绿（探针剥变量），取值不当也不收窄
   （`safe/none/拼错` 静默 96/96）。现在的防线是用例把「提示点名的取值集合」与「生产实测能收窄集合」
   做**双向对账**，生产侧改档位而不同步提示会直接判红。残余边界：对账是在显式采样集
   `hermes/core/slim/minimal/safe/none/typo-value` 上做的，若生产侧新增第 5 个可收窄档而不扩采样集，
   提示仍可能不完整（用例 docstring 已把这条写成需要显式付出成本的提醒）。**G3 本体未修**。


VERDICT: FAIL
BLOCKERS: [G3（lantai/cli/mcp.py `_get_active_tools()` 默认档 return TOOLS = 96/96，且未知/拼错 profile 同样回退全量；规定的 LANTAI_MCP_EXPOSE=all|safe|none 不存在——续15 取证：测试/门禁路径内未申报出网 = 0，故这是契约/策略违反而非已发生外泄，仍需维护者签字收窄默认档）, 出口绊线未纳入门禁判据（`tests/support/network_egress_tripwire.py` 已落地，`release_check.py` 仍无该项，undeclared=0 目前靠人工取证）, X-05（docs/memory-wiki/index.md、overview.md 仍在 git 历史且已推送，仅 untrack + gitignore）, G1 风险面（tests/test_longmemeval_core.py:32 整模块 skipif + data/ 被 gitignore ⇒ CI 评测线静默缺席）, N-07 残余（本地领先 origin/master 19+ 提交、本地/远端均无 v0.66* tag ⇒ CI 从未在候选提交上跑过）]
EVIDENCE: 票面口径 `pytest tests/ -q -o addopts="" --tb=short` @533ea8aa → 2820 passed / exit 0 / 286.9s（.scratch/gate/baseline/r15_final3-nokey.log）；插桩同命令 + `-p network_egress_tripwire` → 2820 passed / exit 0，报告 armed=true count=1 undeclared=0（.scratch/gate/r15/r15_egress_final2-egress.json）；权威门禁 `python scripts/release_check.py v0.66.0` → 9/10 通过 1 失败（MCP 默认暴露 96/96）exit 1 / 376.4s / passed=2820 / temp_home_removed=True；红→绿：tests/test_mcp_import_side_effects.py 修复前 4 failed 1 passed → 修复后 6 passed；同一 HEAD 重放 before（生产文件临时退回 533ea8aa~1，测试不动）= 4 failed 2 passed exit 1，git checkout 复原后 13 passed（.scratch/gate/baseline/r15_before_at_head-nokey.log、r15_after_at_head-nokey.log；提交态 blob sha256 前缀 7cd588c293694377，工作树因 core.autocrlf=true 落成 CRLF，LF 归一后与 blob 逐位相等 ⇒ EOL 不是判据载体）；变异对照 mcp.py sha256 前缀 7cd588c293694377、network_egress_tripwire.py sha256 前缀 f7dc789003d9e628 均逐位还原；lantai/** 模块级全局改写 AST 普查 3 处 → 0 处（.scratch/gate/r15/scan_prod_pruned.py）。临时物审计：门禁临时物（lantai_gate_* / lantai_tripwire_* / lantai_legacy_* / 一次性 home）残留 = 0；既有生产评测代码留 %TEMP% 目录 915 个 / 442 MB（§6.10，续17 已修 a767fad2）；续16 复跑：票面口径 2826 passed / exit 0 / 0 skipped，插桩口径 2826 passed 且 undeclared=0，权威门禁 @95752dcd = 9/10 通过 1 失败（MCP 96/96）exit 1 / 390.4s / passed=2826；BL-03 假绿通道关闭前后对照：repro 由 MASKED_AS_SKIP → DETECTED，申报锁红→绿（修复前 2 failed：未申报点 + guard='not report.failure_belief_candidates'；修复后 6 passed），植入演示弃权点 → 2 failed，删除 → 6 passed（.scratch/gate/baseline/r16_teeth-nokey.log / r16_teeth_after-nokey.log）；MCP 独立探针：默认 96/96、hermes 5/96、拼错 profile 96/96（fail-open，见 §2）；顺序无关对照：`tests/cognitive` + 申报锁正序 62 passed / 逆序 62 passed、BL-03 单跑 + 锁 7 passed（均 -rs 零 skip）；权威门禁最终复跑 @dab3100c（文档提交）= 9/10、passed=2826、exit 1、WALL=390.1s、temp_home_removed=True ⇒ 判定落在可发布候选的最终 HEAD 上。 续18：修复前外部校验器 `uv lock --locked --offline` = exit 1（「The lockfile at `uv.lock` needs to be updated, but `--locked` was provided」），根包版本实测 0.65.0 对 pyproject 0.66.0；TDD 红三级 r18_lock_red-nokey.log（判据未覆盖：2 failed，其中 `assert lock_refs` 直接坐实盲区）、r18_lock_red2b/r18_lock_red3（判据已加、锁未修：1 failed 与 2 failed，门禁 `--skip-suite` 实跑打印 `[FAIL] 版本一致性 — uv.lock 根包版本（uv.lock）= 0.65.0，期望 0.66.0`，见 r18_rc_direct-nokey.log）、r18_lock_green-nokey.log（33 passed / exit 0，且 `uv lock --locked --offline` 转 **exit 0**）；变异对照 `.scratch/gate/r17/mut_lock.py`：锁退回 0.65.0 → 2 failed，逐字节复原 → 3 passed；稳定性 `.scratch/gate/baseline/r18_stab{1,2,3}-nokey.log` = 2840/2840/2840 passed，exit 0；权威门禁 @803c9f96 = 9/10、passed=2840、exit 1、WALL=396.1s、temp_home_removed=True，版本一致性项现报「7 处版本引用」；锁一致性静态取证 `.scratch/gate/r17/lock_block.py`（lantai 依赖 21 条 ↔ pyproject 20 个包名，双向差集空；`git diff uv.lock` = 1 行）。发布一致性其余维度：CONTEXT.md:106 已登记 v0.66.0 代号「探赜」；schema 侧 D-13 族 4 条文豁免由 `scripts/schema_parity.py:35` + `release_check.py:698-707`（含「豁免必须可复现」反向检查）纳管，非静默。 续17（2026-11-03）：红基线 @089b4972 票面口径 2826 passed / 0 failed / 0 skipped / exit 0 / 289.7s（.scratch/gate/baseline/r17_baseline-nokey.log）——「主干红」在本 HEAD 不成立；泄漏复现：开工计数 286+686 → 一次全量后 289+692（净增 +3 fq / +6 staged，探针 r17/probe_offline.py 单点复现 LEAKED 列表）；TDD 红两级 r17_leak_red1-nokey.log（收集期 ImportError）与 r17_leak_red2-nokey.log（5 failed：4 条泄漏断言+ 1 条接线检查 / 7 passed 反向保险），修复后 r17_leak_green-nokey.log 12 passed / exit 0；变异对照（r17/mutate.py）：reclaim 假成功 → 6 failed、清扫忽略年龄 → 1 failed、去掉所有权判据 → 1 failed、复原字节相同后 12 passed；票面口径 @a767fad2 = 2838 passed / exit 0 / 312.4s（.scratch/gate/baseline/r17_full-nokey.log），插桩口径同数 2838 且 armed=true / count=1 / undeclared=0（.scratch/gate/r15/r17_egress-egress.json）；调用方无害对照：遗忘质量/回答质量/staged 对表/anchor miss + 新测试 = 34 passed / exit 0 / 零 skip；泄漏复测：全量前后 lantai-fq-check-* 净增 0，一轮全量后遗留的 6 个与门禁后遗留的 12 个 lantai-staged-env-* 由生产 API reclaim_stale() 跨进程实测 6→0 / 12→0（.scratch/gate/r17/sweep_proof.py）；权威门禁 @a767fad2 = 9/10 通过 1 失败（MCP 默认暴露 96/96）exit 1 / WALL=396.2s / passed=2838 / temp_home_removed=True ⇒ 本轮只减少一条真实污染面，未解除任何 P0 阻断。 续19（2026-11-03）：D1 复现 `.scratch/gate/r19/repro_d1_failopen.py`（修复前输出含 `[PASS] CI lint 门禁`、汇总只有 1 跳过；修复后同脚本打印 `[SKIP] CI lint 门禁`、汇总带「1 跳过」、exit 1）；D2 复现 `.scratch/gate/r19/repro_d2_accumulate.py`（修复前两次汇总「8/10」vs「16/19」、同名判定项 2 次；修复后两次逐字相同、同名项 1 次）；TDD 红 `.scratch/gate/baseline/r19_red-nokey.log`（8 failed，其中 1 条是新插入代码未过 `ruff format --check`、被既有哨兵 `test_lint_gate_passes_on_real_repo` 当场抓住）→ 格式化后红基线 `r19_red2-nokey.log`（7 failed / 33 passed / exit 1）→ 绿 `r19_green-nokey.log`（40 passed / exit 0）；变异对照 `.scratch/gate/r19/mutation_r19.py`（M1 1 failed / M2 1 failed / M3 2 failed / M4 2 failed），收尾字节比对暴露了探针自身用 `read_text()` 把 CRLF 拍平成 LF 的缺陷并已按字节还原（复跑 format/check 640 files + All checks passed + 40 passed）；票面口径 `.scratch/gate/baseline/r19_full-nokey.log` = 2847 passed / 0 failed / 0 skipped / exit 0 / 297.0s（warning 数与续18 基线同为 2、同类同源）；权威门禁三连 `.scratch/gate/baseline/r19_auth{1,2,3}.log` = 9/10、1 失败（MCP 96/96）、exit 1、passed=2847、skips=0、temp_home_removed=True、WALL 398.8/393.3/392.4s，三次汇总逐字相同；收尾卫生：`lantai-fq-check-*`=0、三轮门禁后 `lantai-staged-env-*` =24 由生产 API `reclaim_stale()` 实测 24→0、`lantai_gate_*/lantai_tripwire_*/lantai_legacy_*/probe-*`=0、一次性 home 残留 0、ambient `LANTAI_HOME/OPENAI_*/API_KEY/RERANKER_*/LANTAI_MCP_PROFILE/MCP_TOOL_PROFILE/LANTAI_INTENT_OFF/DATABASE_URL/CHROMADB_PATH/REMEMBRANCE_HOME` 全 `<unset>`、`git ls-files docs/memory-offload docs/memory-wiki`=0、`git status --porcelain` 空 ⇒ 本轮只增加阻断，未解除任何 P0 项。 续19 D4：`-k=addopts` 修复前 2 failed / 1 passed （`.scratch/gate/baseline/r19_d4_red-nokey.log`，passed 那条是真实仓库不误伤的反向保险）→ 修复后 `tests/test_release_check.py` 43 passed（`r19_d4_green-nokey.log`）；变异 M5/M6 （`.scratch/gate/r19/mutation_d4b.py`，行级手术 + 字节级还原）各自只判红对应那条，还原后 3 passed；票面口径 `r19_d4_full-nokey.log` = 2850 passed / 0 failed / 0 skipped / exit 0 / wall 290.7s；权威门禁三连 `r19_authfinal{1,2,3}.log`（HEAD `2c197571`）= 9/10、1 失败（MCP 96/96）、exit 1、passed=2850、skips=0、temp_home_removed=True、WALL 397.8/398.2/399.8s，汇总逐字相同；提交态与工作树在 LF 归一后逐位相等（`git show HEAD:…` 对比工作树字节），EOL 不是判据载体。 续20（2026-11-03）：修复前红工件 `r20_reverse_pre-nokey.log` / `r20_shuffled_pre-nokey.log`（各 `1 failed, 2853 passed`、`### exit=1`，判红者均为 `test_seam1_jiaochou_service`）与正序对照 `r20_forward_pre-nokey.log`（2854 passed / exit 0），三者均在 `tests/test_jiaochou_api_smoke.py` 回到 `HEAD~1` 版本后重跑取得，跑完按字节还原（`sha256` 前后 `42cfbf3e6edd99f7…` 一致、`git status` 空，脚本 `.scratch/gate/r20/pre_red.py` / `pre_red_fwd.py`）；只读诊断 `r20_rev_diag_pre-nokey.log` + `.scratch/gate/r20/diag.txt`（`active_total=46 / owner_null=0 / swarm_shared=0 / accessible_for_foreign_tenant=0 / pairs_before_seed=0`）；TDD 红 `r20_step1_red-nokey.log`（1 failed / 5 passed，隔离锁断言「窗口被别人的数据占满…10 对里没有它」）→ 绿 `r20_step2_green-nokey.log`（6 passed）；机制锁变异 `r20_mut_mech-nokey.log`（摘掉两层 `if len(pairs) >= limit: break` → `assert 277 == 10`）后按字节还原（`lantai/cognition/jiaochou.py` sha `d68c5bb7636adc61…`）；账本护栏异常路径红 `r20_leg_red-nokey.log`（1 failed）→ 绿 `r20_leg_green-nokey.log`（47 passed），变异 T1 无 clear 2 failed / T2 还原改 clear 3 failed（连带被 lint 哨兵抓 F841，证明变异是活的）/ T3 无 finally 1 failed，逐字节还原 `test_release_check.iso.bak`；三口径修复后 `r20_forward/reverse/shuffled-nokey.log` = 2856 / 0 failed / exit 0（wall 327.5/324.9/324.2s），单跑 `r20_solo_*-nokey.log` 各 `1 passed, 5 deselected`；票面口径 `r20_full-nokey.log` = 2856 passed, 2 warnings / exit 0 / 289.5s（无 `skipped` 字段即 0 跳过）；权威门禁三连 @`cea3bd84` = 9/10、1 失败（MCP 96/96）、exit 1、`passed=2856`、`temp_home_removed=True`、WALL 400.1/396.4/400.5s 且四项逐字相同；卫生：`lantai-fq-check-*`=0、`lantai-staged-env-*` 经生产 API `reclaim_stale()` 42→0（`.scratch/gate/r17/sweep_proof.py`）、一次性 `home-*`=0、ambient 密钥/路径变量全 `<unset>`、`git ls-files docs/memory-offload docs/memory-wiki`=0、`ruff check lantai tests scripts` All checks passed + `ruff format --check` 640 files already formatted；改动面 `git diff --name-only HEAD~1 HEAD` = `tests/test_jiaochou_api_smoke.py` + `tests/test_release_check.py`（无生产代码、无 CI、无迁移）。 续20 收尾补证：第二乱序种子 seed=20261104 在最终 HEAD 亦 `2856 passed / exit 0`、`FAILED` 命中 0（`r20_shuffled2-nokey.log`，wall 347.3s）；文档提交后 HEAD `00a3ea34` 再跑权威门禁 = 9/10、1 失败（MCP 96/96）、exit 1、passed=2856、temp_home_removed=True、WALL=427.9s（`.scratch/gate/r20/auth_final_docs.out.txt`）， 续21（2026-11-03）：MCP 暴露面实测 `.scratch/gate/r21/mcp_narrow.py`（默认 96/96；core/hermes/slim/minimal ⇒ 5 且大小写与空格不敏感；safe/none/typo/空串 ⇒ 96/96；MCP_TOOL_PROFILE 为生效别名；`LANTAI_MCP_EXPOSE` 经 `.scratch/gate/r21/mcp_probe.py` 设 all/safe/none 后仍 96/96，`grep.py lantai "LANTAI_MCP_EXPOSE"` 0 命中）；探针非空转双证 `.scratch/gate/r21/probe_selftest.py`（直驱钩子断言不丢用例/文件序不动/类相邻/确实重排）与 `realorder.py`（真实 `--collect-only`：53 个 nodeid 集合相等、顺序不等）；顺序补验 `r21_node_shuffle-nokey.log`（seed=20261105，2856/exit 0）、`r21_combo_all-nokey.log`（seed=20261106，2856/exit 0）、`r21_combo_rev_inmod-nokey.log`（逆序文件＋节点乱序，2856/exit 0）；TDD 红 `r21_red_msg-nokey.log`（第 1 版 1 failed/47 passed）→ 第 1 版被变异 M-B 证伪（`r21_mutB_*` 首跑 1 passed，说明只做子串打卡测不到东西）→ 改双向对账后红 `r21_red2b-nokey.log`（1 failed, 47 passed）→ 绿 `r21_green2-nokey.log`（48 passed）；变异四组各判红 1 例：M-A 抽警告 `r21_M-A*.log`、M-B 处方塞 safe `r21_mutB-nokey.log`（失败信息 广告=['core','hermes','minimal','safe','slim'] vs 实测=['core','hermes','minimal','slim']）、M-C 漏 minimal `r21_M-C*.log`、M-D 抹「只看默认档」`r21_M-D*.log`，每组跑完按字节还原（sha256 与 `release_check.bak2` 一致）；误删恢复 `.scratch/gate/r21/restore_case.py`（按 `git show HEAD:` 原文回插，收集数 47→48，`git diff -- tests/` 无 `-def test_` 命中）；本轮三口径 `r21_full/reverse/combo-nokey.log` = 2857/2857/2857 passed、exit 0、FAILED 命中 0、汇总无 `skipped`；权威门禁三连 `r21_authfinal{1,2,3}.log`（HEAD `6c616c3d`）= 9/10、1 失败（MCP 96/96）、exit 1、passed=2857、`[SKIP]`=0、`temp_home_removed=True`、WALL 390.9/395.3/399.1s，汇总/失败项/exit/HEAD 四项逐字相同；`ruff check lantai tests scripts` All checks passed、`ruff format --check` 640 files already formatted；改动面 `git diff --name-only HEAD~1 HEAD` = `scripts/release_check.py` + `tests/test_release_check.py`（提示文本 + 用例，未动 MCP 默认暴露、未动 CI、未动迁移）。
