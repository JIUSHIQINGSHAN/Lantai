# 兰台方案审计（续）：v0.60 → v0.64，2026-10-31

审计对象：`JIUSHIQINGSHAN/Lantai` master `a5502058`（v0.64.0「缮治」），对比基线 v0.60.0。
前序报告：`docs/research/commit-review-v0.25-v0.38-2026-10.md`、v0.38–v0.60 续审笔记、X-01 两处回归修复说明。
本文写给维护者和维护者自己的 AI 读：每条都给文件、行号、提交、复现方式和验收口径。

## 结论

上一轮列的 P0 大半真修了：拾遗绕过隔离、删除后孤儿事实回注、私密目录进 Git、时态评测 D-06 都已闭合，CI 也终于做到“测试红就不出镜像”。E2 这次是真绿：把拾遗打桩成空结果后 `test_e2_temporal` 仍然 3 项全过。

但是用户每天真正碰到的那条链路——Hermes 每轮注入——还有三处没通，而 v0.64 的提交信息写的是“P0 行动清单 7 项全部闭合”：

1. **短问句拿不到画像**：v0.64 改了 `gate/prefilter.py` 放行短问句，但 Hermes 插件自己在前面有一道“≤15 字且无触发词就跳过”，根本走不到闸门。实测“我对什么过敏？”“龙井茶冲泡水温是多少”都不注入。（本 PR 已修）
2. **探颐的中文答复照样误归档**：R-01 只把英文否定词改成整词匹配。“对，没变”“是的，还是喝乌龙茶”“我下周放假再说”仍会把一条正确记忆直接归档。根因还多一个：全角逗号被 ruff 改成了半角，“对，……”从来不算肯定。（本 PR 已修）
3. **常用记忆照样会被忘掉**：X-02 只给 60 天 TTL 那条路加了豁免，衰减阈值那条路没动；Hermes 注入又从不更新 `use_count`/`last_used_at`。实测重要度 0.95 的偏好 160 天后被归档，0.95 的普通记忆 115 天后被归档。

另外，**主干红的真因已经找到，而且很便宜**：本地 20 个失败里，17 个单独跑全部通过，是测试之间共用一个会话级临时库互相污染；剩下 3 个是 2 秒冷启动超时。修好隔离，主干就能转绿，现在已生效的 CI 闸门也就不会继续挡住每一次发版。

## 实测口径

- 环境：VM，Python 3.13（CI 用 3.11，未在 3.11 复现），8 核。
- 全量：`API_KEY=test-key pytest tests/ -q -o addopts=""`（与 CI 一样设了 `API_KEY`）。
  - master `a5502058`：**20 failed / 2513 passed / 5 skipped**。不设 `API_KEY` 时 25 failed，多出的 5 个是环境问题。
  - 加上本 PR：**20 failed / 2530 passed / 5 skipped**，失败集合与 master 逐项一致，新增 17 个测试全过。
- 提交信息自报“run 9 = 17F/2521P”，与本地吻合：多出的 3 个是下文 P1-1 的冷启动超时。
- CI：`a5502058` 的 Tests 运行 41196184196 失败（“Full test suite with coverage”一步），Docker 运行 41196184158 因此未出镜像。闸门有效，但 v0.64.0 的 tag 在测试转绿之前就打了。
- LongMemEval、selfproof 未重跑（VM 上没有模型密钥）。`docs/benchmarks/selfproof-report-2026-10.md` 最后更新于 10-28，此后 26 个版本没有新的评测结果。

## 一、上轮问题逐条核对

| 编号 | 问题 | 状态 | 依据 |
|---|---|---|---|
| N-01 | 拾遗零召回补救绕过 as_of/领域/agent 隔离，掩盖 E2 | 已修 | `bbbc4496`；7 个隔离测试不在失败集里 |
| D-06 | `temporal.py` 回归，E2 31/37 | 已修（真绿） | `a5502058` valid_from 默认 None + as-of 守卫；拾遗打桩为空后 E2 仍全过 |
| valid_from 写入点 | 默认改 None 后漏写会变成“永远有效” | 已核 | 全仓 `MemoryItem(` 构造点都显式写了 `valid_from` |
| X-01 | 删除记忆不级联 `fact_atomic`，隐私回注 | 已修 | `bbbc4496` + `4664ffcb`；`tests/test_x01_fact_cascade_regression.py` 通过 |
| X-05 | 卸载全文/知识库写进 Git 工作区 | 已修 | `.gitignore` 已加；`docs/memory-wiki` 已取消跟踪，历史版本里只是空模板 |
| G1 | 测试红照样推镜像 | 已修 | `ci.yml` 镜像构建 `needs: tests`；v0.64 实际被挡 |
| 打桩 | 11 个测试打桩在 `shell_hook` 上等于没测 | 已修 | `test_fence.py`、`test_injection_data_slot.py` 已改为打在 hybrid 上 |
| R-01 | 探颐否定靠子串，误归档/误改写 | **只修了英文** | 见 P0-2，本 PR 修中文 |
| D-02 | 15 字预过滤让画像对短消息失效 | **修错了地方** | 见 P0-3，本 PR 修插件侧 |
| X-02 | Hermes 使用不计数，记忆 60 天被归档 | **只修了一半** | 见 P0-4 |
| X-03/X-04/X-06/N-02 | 终端自合并、persona 注入检查、密钥闸、拾遗扫描预算 | 代码已改，未逐条复现 | `48e12749` |
| N-03 | 考功写副作用无身份 | 代码已改，未复现 | `a5502058` |
| X-06 | 密钥检查默认关 | **默认仍是 shadow** | 见 P1-2 |
| MCP 默认全开 | 94 个工具默认全开 | **未修，96 个** | 见 P1-3 |
| D-03 | 0.5 阈值同时用于两种量纲的分数 | 未动 | `scripts/shell_hook.py:245`、`lantai/core/settings.py:276` |
| D-04 | reranker 网络调用挤在 2 秒超时里 | 未动 | `settings.py:194-195` `RERANKER_TIMEOUT=30`，`settings.py:270` `SHELL_HOOK_TIMEOUT=2` |
| C-03 | 插件里写死个人路径 | 未动 | `hermes-plugin/lantai-hook/__init__.py:62` `Path("D:/Projects/记忆")` |

## 二、新发现与仍未闭合的问题

### P0-1 主干红是测试互相污染，修隔离就能转绿（约 1 天）

- 位置：`tests/conftest.py:198`，`@pytest.fixture(scope="session", autouse=True)` 把 `DATABASE_URL`/`CHROMADB_PATH`/`LANTAI_HOME` 指到**一个会话级共享**临时目录。
- 现象：全量 20 个失败里，17 个单独跑全部通过。典型断言：`test_shell_hook.py::test_build_context_short_query_returns_empty` 期望 `{}`，实际拿到 `[POLICY]: 契约测试策略` 这条核心记忆——它是 `tests/test_console_api_contracts.py` 写进共享库的；`ClickHouse 分布式表分片` 那条 checkpoint 来自 `tests/test_diben_api_smoke.py`。
- 影响：G1 闸门现在真会挡镜像，主干一天不绿，就一个版本都发不出去；而且失败集“与基线一致”这种说法会让真回归混进来没人发现。
- 修法：二选一。
  - 每个测试一个新库：把这个 fixture 改成 function 级，或另加一个 function 级 fixture，在每个测试后清空 SQLModel 表、FTS 表和 Chroma 集合。
  - 只隔离写库的测试：给 `test_console_api_contracts`、`test_diben_api_smoke` 等写库测试加独立临时库。
- 验收：全量顺序跑与 `pytest -p randomly` 乱序跑都是 0 failed（不含 P1-1 的冷启动项）；CI Tests 转绿。
- 附带一条流程规矩：**先等 master 的 Tests 绿，再打 tag**。v0.64.0 现在是一个没有镜像的 tag。

### P0-2 探颐中文答复误归档、误改写（本 PR 已修，0.5 天）

- 位置：`lantai/services/probing_service.py`，分支 B（模糊记忆求证，原 :425-441）和分支 C（原子事实，原 :458-470）共用 `_NEGATIVE_PATTERNS`（:49）。
- 根因一：分支 A 问的是“新说法要不要替换旧的”，这时“还是/依然/没变/保持”的意思是“留旧的”，算否定没错。分支 B/C 问的是“这条记忆还对不对”，同样的词是**确认**，却被当成否定，直接 `status="archived"`。
- 根因二：裸“假”命中“放假/假期/请假”；“没有”命中“没有问题”。
- 根因三：`_is_affirmative_reply` 的字符类里本该有全角“，！？”，已经被格式化工具改成了半角。所以“对，改成龙井了”这种最常见的中文肯定从来不被识别。
- 修复前实测（分支 B，记忆“用户偏好乌龙茶”）：下面 7 句答复全部被归档，置信度掉到 0.1——“是的，还是喝乌龙茶”“对，没变”“对，一直保持这个习惯”“没有变，依然是这样”“嗯，我下周放假再说”“没有问题，是这样”“是不是假期改了我也不清楚”。分支 A：“是的，但其实我还没决定”“是吧，我也说不好”直接把旧正文改成了新说法。
- 本 PR 的改法：
  - 分支 B/C 改用 `_classify_claim_reply`：只认明确的否定（不对/不是/写错/搞错/错了/不属实/不准确/假的/no/wrong…，“是不是”除外），“没变/还是/依然/保持/一直”算确认。两边都沾或含糊一律留待审。
  - 三个分支都先查含糊转折词（但/不过/还没决定/说不好/不确定/不清楚/再说/也许/可能/大概/是吧），命中就留待审，不自动改写也不归档。
  - 共享否定表里的“假”收窄为“假的”。
  - 全角“，！？”用 `\uff0c\uff01\uff1f` 转义写，不会再被 ruff 改掉。
- 修复后：前 4 句确认，后 3 句留待审，记忆保持 active；“不对，我早就不喝了”“写错了”“no, that's wrong”仍然归档；分支 A 的含糊答复留待审，“对，改成龙井了”正确改写，“不是，还是乌龙茶”不改写。
- 测试：新增 `tests/test_probe_reply_cjk_semantics.py`，14 项。放到修复前的代码上跑有 10 项失败；修复后探颐相关测试文件（含新增）共 57 项全过。

### P0-3 Hermes 插件的 15 字预过滤让画像和短问句放行失效（本 PR 已修，0.5 天）

- 位置：`hermes-plugin/lantai-hook/__init__.py:459`，`if len(q) <= 15 and not any(w in q for w in _TRIGGER_WORDS): return`。
- 根因：`a5502058` 把短问句放行写进了 `lantai/gate/prefilter.py`，但 Hermes 走的是插件，插件在调用 shell_hook 之前就把短句挡了。`tests/test_host_matrix.py` 直接调 shell_hook，所以测不出来。
- 修复前实测（打桩 `_call_hook` 记录调用）：“龙井茶冲泡水温是多少”“我对什么过敏？”“帮我订明天的机票，按老规矩”“我女朋友叫什么名字”“给我推荐个周末去处”5 句全部跳过，既不检索也不注入画像。
- 本 PR 的改法：删掉这道插件侧过滤，只保留 `_MIN_QUERY_CHARS=2` 的空消息过滤。要不要检索交给 shell_hook 里的 `relevance_check`；闸门拒绝时它只回常驻画像，不跑检索。修复后 5 句都会走到 hook。
- 代价：短消息每轮多一次 hook 调用。serve 模式是常驻进程，闸门拒绝时不检索；如果你在意这点延迟，可以只对含问号或疑问词的短句放行，而不是恢复 15 字一刀切。
- 测试：`tests/test_hermes_plugin.py` 新增 `test_short_question_reaches_hook_for_persona`，3 项；该文件 23 项全过。

### P0-4 常用记忆仍会被衰减归档（约 1 天）

- 位置：`lantai/memory/forgetting.py:105-121`。`is_exempt`（重要度 ≥0.8、preference/profile、semantic/procedural、`use_count>0`）**只作用于 `ttl_expired`**，`m.decay_score < settings.ARCHIVE_DECAY_THRESHOLD`（0.01，`settings.py:238`）这条路照样归档。
- 叠加问题：Hermes 注入后只回写 `RetrievalEvent.used_ids`（`lantai/observability/retrieval_log.py` 的 `backfill_used_ids`），全仓没有一处在注入或回执时更新 `MemoryItem.use_count`/`last_used_at`。所以只靠 Hermes 用的记忆，衰减永远从创建时刻起算。
- 自相矛盾：`settings.py:94-96` 的注释说 preference 是“永不衰减语义”，但 `LANE_DECAY_PROFILES["preference"]` 给的是 `base_s=15`，照样衰减。
- 实测（`apply_forgetting()`，`use_count=0`、`last_used_at=None`）：
  - preference、重要度 0.95、semantic、long_term，160 天：decay 0.0090，**archived**；150 天：0.0121，active。
  - general、重要度 0.95、semantic、long_term，115 天：decay 0.0087，**archived**。
  - fact、重要度 0.95，200 天：0.0528，active。
- 修法：
  - `backfill_used_ids` 在回写 `used_ids` 的同时，给对应 `MemoryItem` 做 `use_count += 1`、`last_used_at = now`。
  - 让 `is_exempt` 同时豁免衰减归档，至少覆盖 preference/profile 和重要度 ≥0.8 的记忆。
  - 或者让 preference 真正不衰减，与注释一致。
- 验收：新增测试——preference 0.95、180 天、从未被用，不归档；general 0.5、被 Hermes 注入并回执 3 次，`use_count==3`，`last_used_at` 被刷新。

### P1-1 冷启动首帧超过 2 秒就注入为空（0.5–1 天）

- 位置：`settings.py:270` `SHELL_HOOK_TIMEOUT: int = 2`。
- 实测：`tests/test_host_matrix.py::TestHostEndToEnd::test_inject_then_backfill[hermes]`，`SHELL_HOOK_TIMEOUT=2` 失败（“注入正文为空”），4 秒和 8 秒都通过。三宿主 3 项都一样。冷进程要加载 jieba、Chroma 和时态模块，2 秒在这台机器上不够。
- 影响：每次 Hermes 重启后的第一句话没有记忆也没有画像。提交信息里的“三宿主 E2E 全绿”取决于机器快慢。
- 修法：serve 模式启动时预热（先加载分词和向量库再接单），冷首帧单独给一个更长的预算；超时时退回一份缓存在磁盘上的常驻画像，而不是空 JSON（现在 `test_main_timeout_returns_empty_json` 断言的就是空）。

### P1-2 密钥守卫默认 shadow（0.5 天）

- 位置：`settings.py:311` `SECRET_GUARD_MODE: str = "shadow"`。shadow 模式只记日志、不拦截，带密钥的内容照样入库，之后照样参与检索和注入。
- 建议：单用户自用场景默认改成拦截或打码；保留 shadow 给调试。

### P1-3 MCP 96 个工具默认全开（0.5 天）

- 位置：`lantai/cli/mcp.py:3707` 读 `LANTAI_MCP_PROFILE`/`MCP_TOOL_PROFILE`，已经有 `HERMES_CORE_TOOLS` 这个精简集，但默认不用，README 和 `launch_mcp.bat` 里也没写。v0.61 又加到 95 个，现在是 96 个。
- 影响：Hermes 每轮都要带 96 个工具的描述，模型更容易乱调用写操作。
- 建议：Hermes 默认用 core 集，完整集靠环境变量打开，在 README 写清楚。

### P1-4 发版节奏和路线图背离，真实改动藏在“chore(release)”里（流程，0 天）

- `docs/plans/infinite-marathon-engine-roadmap.md` 写的是两周一个窗口、每窗只做一到两个突破、闸门全绿才发版；`docs/research/next-15day-plan-2026-10.md` 写的是自证优先、其他冻结。实际是 v0.61 到 v0.64 四个版本用了 23 小时（10-30 20:16 到 10-31 19:12），v0.61 还新加了一个引擎（钩沉）。
- `a5502058` 的标题是 `chore(release): bump version to 0.64.0`，实际改了 49 个文件、加 1033 行、删 104 行，包括 `temporal.py`、`prefilter.py`、`probing_service.py`、`ci.yml` 和数据表定义。这样的提交没法二分，也没法单独回滚其中一项修复。
- 建议：一个修复一个提交，版本号单独提交；tag 只在 master Tests 绿之后打。

### P2 其他

- 钩沉（v0.61）只接到 MCP 和 REST（`lantai/cognition/context.py`、`routes_gouchen.py`），不在 shell_hook/hybrid 的注入主路径上，对 Hermes 用户没有感知。与上轮“17 个新引擎只有拾遗进主路径”是同一个模式。
- 代号“磨勘”又复用了：`hybrid.py:735` 等处是 v0.29 的 MCMA 磨勘，v0.62.0 的版本代号也叫磨勘。
- `docs/` 下已跟踪 319 个文件，其中 200 个 Markdown。建议把过期的票据和评审归档到 `docs/archive/`，只留路线图、ADR 和最新一份评审在显眼位置，你自己的 AI 读起来也更省上下文。

## 三、方案层面：哪些地方和“好用、适配自己”不对路

1. **用户感知全在注入链路，投入却在外围**。你每天碰到的是三件事：短问能不能带上画像，常用的记忆会不会被忘，重启后第一句有没有记忆。这正好是 P0-3、P0-4、P1-1。而最近几个版本的新东西（钩沉、MCP 扩到 96 个、REST 端点）都在 Hermes 看不到的地方。
2. **自证评测停在 10-28**。没有“注入级”评测（第一份评审就建议加），现有指标测不出画像有没有注入、记忆有没有被误归档。建议在 selfproof 里加两组：短问句画像命中率；模拟 180 天后的偏好存活率。
3. **“闭合”的口径要以测试为准**。这次 R-01、D-02、X-02 都是“改了一处、宣布闭合”，但用户实际走的那条路没覆盖到。每个 P0 的验收应该是一个从 Hermes 插件入口开始的测试，不是从中间某个函数开始。
4. **建议冻结新引擎两周**，只做：测试隔离（P0-1）→ 衰减与使用计数（P0-4）→ 冷启动（P1-1）→ 单用户默认值（P1-2、P1-3）→ 注入级评测。做完再决定下一个引擎值不值得接进主路径。

## 四、本 PR 包含的修复

- `lantai/services/probing_service.py`：P0-2 探颐中文语义修复。
- `hermes-plugin/lantai-hook/__init__.py`：P0-3 删除插件侧 15 字预过滤。
- `tests/test_probe_reply_cjk_semantics.py`（新增 14 项）、`tests/test_hermes_plugin.py`（新增 3 项）。
- 本报告。
- 全量对比：master 20F/2513P → 本 PR 20F/2530P，失败集合不变。ruff check 与 ruff format 均通过。

## 五、行动清单

| 优先级 | 事项 | 位置 | 工作量 |
|---|---|---|---|
| P0 | 合并本 PR（探颐中文、插件预过滤） | 见第四节 | 已完成，待审 |
| P0 | 测试隔离，主干转绿；先绿后打 tag | `tests/conftest.py:198` | 1 天 |
| P0 | 注入回执更新 `use_count`/`last_used_at`；豁免覆盖衰减路径 | `forgetting.py:105-121`、`retrieval_log.py` | 1 天 |
| P1 | 冷启动预热，超时退回缓存画像 | `settings.py:270`、`scripts/shell_hook.py` | 0.5–1 天 |
| P1 | 密钥守卫默认拦截/打码 | `settings.py:311` | 0.5 天 |
| P1 | Hermes 默认 MCP core 集并写进 README | `mcp.py:3707` | 0.5 天 |
| P1 | 0.5 阈值按分数来源分开；reranker 不进 2 秒预算 | `shell_hook.py:245`、`settings.py:194-195,276` | 0.5 天 |
| P1 | 去掉写死路径 | `hermes-plugin/lantai-hook/__init__.py:62` | 0.2 天 |
| P1 | 注入级评测：短问画像命中率、180 天偏好存活率 | `docs/benchmarks/` | 1–1.5 天 |
| P2 | 一修复一提交；代号去重；docs 归档 | 流程 | 持续 |

P0 剩余约 2 天，P1 约 3.5–4 天。

## 六、没做和没验证的

- LongMemEval、selfproof 没有重跑，VM 上没有模型密钥。
- 没在 CI 用的 Python 3.11 上复现。
- X-03、X-04、X-06、N-02、N-03 只看了代码改动，没有逐条复现。
- P0-1 的“17 个单独跑全过”是把失败的节点 ID 挑出来放在一次 pytest 里跑的；具体是哪几个测试写了脏数据，只确认了两处（`test_console_api_contracts`、`test_diben_api_smoke`），没有逐对二分。
- P1-1 的耗时取决于机器，你那台 Windows 上可能不到 2 秒，建议用同一个测试在本机试一次。
