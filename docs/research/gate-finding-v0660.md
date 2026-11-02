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
| **G3** MCP 必须有显式开关，禁止默认全量 | **违反** | 见 §2：`LANTAI_MCP_EXPOSE` 不存在；`_get_active_tools()` 未设 profile 即 `return TOOLS`（96/96）。续15 补测：默认全暴露在测试/门禁路径内**未产生未申报出网**（插桩全量 2820 passed，`undeclared=0`），故仍按契约违反处置，不自批改默认档 |
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

权威门禁（`python scripts/release_check.py v0.66.0`，无旗标，干净树）= **9/10 通过, 1 失败（MCP），exit 1**。续14 在 `38042aa5` 重跑同结论（`WALL=367.6s`、`passed=2807`、`temp_home_removed=True`）；续15 在 `533ea8aa` 重跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=376.4s`、`passed=2820`、`temp_home_removed=True`），并在文档提交 `551a6086` 上再跑一次同结论（`WALL=372.7s`、`passed=2820`、`temp_home_removed=True`）——其后的文档提交只是追加记录，与 10 项判据无因果（判据不含 markdown 内容检查）——计数差分 +6（mcp import 副作用）+5（出口绊线）+2（申报哨兵）= 2807→2820，全部为新增用例，无删测/skip/xfail/断言放宽；续16 在 `95752dcd` 再跑仍 **9/10、唯一红项 MCP 默认暴露**（`WALL=390.4s`、`passed=2826`、`temp_home_removed=True`），计数差分 `2820 → 2826 = +6`（不执行点申报锁），BL-03 由「可弃权」变「必判红」不改变当前通过数。

## 6. 残余风险（不得被上面的绿灯冒充）

1. MCP 默认 96/96（G3），等维护者在「写进 tracked 发布流程声明」与「收窄默认档 + 改 ~16 个用例」之间签字；续16 补一条硬约束：**未知/拼错 profile 也须 fail-closed**（现静默回退全量 96/96，无告警）。
2. X-05 隐私文档仍在 git 历史且已推送过；`docs/memory-wiki/` 目录在本地磁盘仍存在（已 ignore）。需历史清理决策（force push + 远端可见副本处置），属人工闸门。
3. CI 从未在候选提交上跑过（续16 实测本地 `master` 领先 `origin/master` 19 个提交，此后每轮文档提交继续 +1；本地/远端最新 tag 均为 `v0.65.0`，无 `v0.66*`）；门禁不是 CI 的替代。
4. `data/` gitignored ⇒ LongMemEval 评测线在 CI 上静默缺席（G1 风险面）；另 `pyproject.toml`
   `filterwarnings = ["ignore::DeprecationWarning"]` 全局抑制一类告警（不影响退出码，但会压低
   未来弃用信号；本轮未动它，改它需单独立项核对存量告警）。
5. ~~`tests/conftest.py` 的 env/settings 单例写入未纳管（G2）~~ 已由 `38042aa5` 纳管并复原（票 05 续14，见 §4 G2 行）；**网络出口仍无绊线监控**——黑洞端点把「失败即外传」降级成「失败即断网」，但那不是可取证的出站事件观测；**只监听绊线已落地**为 `tests/support/network_egress_tripwire.py`（票 05 续15，含 `DECLARED_EGRESS_PROBES` 申报哨兵与隔离子进程「牙齿」反证），但**尚未成为 `scripts/release_check.py` 的判据**——目前靠人工执行插桩口径取证；下一条该做的是把它变成第 11 项（判据取 `undeclared == 0`，报告缺失按未取证 = FAIL）。
6. `FORGET_IMPORTANCE_EXEMPTION_FLOOR` 无边界校验；X-01 的存活谓词靠人工携带、无静态门禁；X-08 WAL 仍未开；N-02 热路径性能未取证；E2/D-06 时态评测仍未闭环；D-03/D-05/D-08…D-16、N-03、X-06、X-07 本轮未取证。
7. 门禁自身的 `CI_STEP_OWNERS` / `CI_STEP_EXEMPTIONS` 是 tracked 可编辑清单，硬删一行可灭红（结构性替代需改 CI 与门禁共享命令定义）。
8. CI 用 `pip install -e .[dev]` 浮解析、不读 `uv.lock`。
9. **不执行点申报锁已落地**（`tests/test_skip_mask_inventory.py`，续16）：新增/改名/改 reason/删除
   skip 都会判红。它管住的是「测试侧弃权」，不覆盖「评测线因数据缺失整体缺席」那条（§6.4）。
10. **生产评测代码留 `%TEMP%` 临时目录**（续15 新查出的既有残余，非本轮引入、本轮未修）：
   `lantai/eval/offline.py:69`（前缀 `lantai-fq-check-`）与 `lantai/eval/deterministic_env.py:63`
   （前缀 `lantai-staged-env-`）都 `mkdtemp` 后不 `rmtree`（实测这两个文件 `rmtree` 0 命中）⇒
   本机累计 **915 个目录 / 442 MB**（265 + 650，内含 `eval.db`、`chroma/chroma.sqlite3`，
   10-31 起累积）。门禁自身临时物残留为 0（`lantai_gate_*`/`lantai_tripwire_*`/`lantai_legacy_*`/
   一次性 `LANTAI_HOME` 全部实测为 0，`temp_home_removed=True`）。属「临时产物自动回收」验收线的
   真实缺口，需独立小单修，不与本判定混批。

VERDICT: FAIL
BLOCKERS: [G3（lantai/cli/mcp.py `_get_active_tools()` 默认档 return TOOLS = 96/96，且未知/拼错 profile 同样回退全量；规定的 LANTAI_MCP_EXPOSE=all|safe|none 不存在——续15 取证：测试/门禁路径内未申报出网 = 0，故这是契约/策略违反而非已发生外泄，仍需维护者签字收窄默认档）, 出口绊线未纳入门禁判据（`tests/support/network_egress_tripwire.py` 已落地，`release_check.py` 仍无该项，undeclared=0 目前靠人工取证）, X-05（docs/memory-wiki/index.md、overview.md 仍在 git 历史且已推送，仅 untrack + gitignore）, G1 风险面（tests/test_longmemeval_core.py:32 整模块 skipif + data/ 被 gitignore ⇒ CI 评测线静默缺席）, N-07 残余（本地领先 origin/master 19+ 提交、本地/远端均无 v0.66* tag ⇒ CI 从未在候选提交上跑过）]
EVIDENCE: 票面口径 `pytest tests/ -q -o addopts="" --tb=short` @533ea8aa → 2820 passed / exit 0 / 286.9s（.scratch/gate/baseline/r15_final3-nokey.log）；插桩同命令 + `-p network_egress_tripwire` → 2820 passed / exit 0，报告 armed=true count=1 undeclared=0（.scratch/gate/r15/r15_egress_final2-egress.json）；权威门禁 `python scripts/release_check.py v0.66.0` → 9/10 通过 1 失败（MCP 默认暴露 96/96）exit 1 / 376.4s / passed=2820 / temp_home_removed=True；红→绿：tests/test_mcp_import_side_effects.py 修复前 4 failed 1 passed → 修复后 6 passed；同一 HEAD 重放 before（生产文件临时退回 533ea8aa~1，测试不动）= 4 failed 2 passed exit 1，git checkout 复原后 13 passed（.scratch/gate/baseline/r15_before_at_head-nokey.log、r15_after_at_head-nokey.log；提交态 blob sha256 前缀 7cd588c293694377，工作树因 core.autocrlf=true 落成 CRLF，LF 归一后与 blob 逐位相等 ⇒ EOL 不是判据载体）；变异对照 mcp.py sha256 前缀 7cd588c293694377、network_egress_tripwire.py sha256 前缀 f7dc789003d9e628 均逐位还原；lantai/** 模块级全局改写 AST 普查 3 处 → 0 处（.scratch/gate/r15/scan_prod_pruned.py）。临时物审计：门禁临时物（lantai_gate_* / lantai_tripwire_* / lantai_legacy_* / 一次性 home）残留 = 0；既有生产评测代码留 %TEMP% 目录 915 个 / 442 MB（§6.10，未修）；续16 复跑：票面口径 2826 passed / exit 0 / 0 skipped，插桩口径 2826 passed 且 undeclared=0，权威门禁 @95752dcd = 9/10 通过 1 失败（MCP 96/96）exit 1 / 390.4s / passed=2826；BL-03 假绿通道关闭前后对照：repro 由 MASKED_AS_SKIP → DETECTED，申报锁红→绿（修复前 2 failed：未申报点 + guard='not report.failure_belief_candidates'；修复后 6 passed），植入演示弃权点 → 2 failed，删除 → 6 passed（.scratch/gate/baseline/r16_teeth-nokey.log / r16_teeth_after-nokey.log）；MCP 独立探针：默认 96/96、hermes 5/96、拼错 profile 96/96（fail-open，见 §2）；顺序无关对照：`tests/cognitive` + 申报锁正序 62 passed / 逆序 62 passed、BL-03 单跑 + 锁 7 passed（均 -rs 零 skip）；权威门禁最终复跑 @dab3100c（文档提交）= 9/10、passed=2826、exit 1、WALL=390.1s、temp_home_removed=True ⇒ 判定落在可发布候选的最终 HEAD 上。
