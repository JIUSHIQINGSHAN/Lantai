> 本文由 Maya（Sesame 的 AI 助手）每周自动生成，经 GitHub 小号 jiushiqingshand-dev 提交，只含文档，未经人工逐行审阅。

# 兰台 · 每周发展方向与代码改进建议 2026-11-08

> 覆盖时间：2026-11-01 至 2026-11-08。整理者：Maya（Sesame 的 AI 助手），每周日自动生成。
> 兰台基线：origin/master `7c553374`（2026-11-08 06:32，合入 PR #42）。Hermes 基线：`NousResearch/hermes-agent` main（含 v0.23.0 及之后的 canary 提交）。
> 与昨天的《AI 记忆研究周报 2026-11-07》（PR #35）分工：那份看论文和外部项目，这份看兰台自己这周怎么走、已有代码哪里该改。两份不重复列同一条建议。

## 一句话结论

这周最值得做的一件事：**让 Hermes 的定时任务（cron）和子智能体（subagent）只读兰台、不写兰台**。现在插件和 shell hook 都不看 Hermes 传来的 `platform`，一次 cron 运行就会把它的任务提示词写成下一场会话开头的“上次在做”，还会送进对话写通道、给注入的记忆加使用次数。Hermes 自己的接口文档明确写着非主会话要跳过写入。改法大约半天到一天。

## 一、兰台这一周

数字（`git log --since=2026-11-01 --no-merges`，origin/master）：

- 170 个提交：`fix` 61、`docs` 84、`test` 22、`feat` 2（两个都是发布门禁，`eb507821`、`553be338`）。
- 代码量：`lantai/ scripts/ hermes-plugin/` 新增 5,034 行；`tests/` 新增 25,648 行；`docs/ CHANGELOG.md` 新增 3,779 行。测试文件现在 371 个。
- 发版：v0.66.0 打了标签；v0.67、v0.68 的票大量合入，但还没打标签。v0.68 的发布候选在你自己开的 PR #43（119 个文件）里，还没合。
- 审计和复验：标题带 `docs(audit)`、`审计报告增补` 或 `并行流C 第…次` 的提交有 34 个，**全部集中在 11-07 一天**。其中 18 个的标题本身就写着“零新发现”“零增量”“master 未前进”或“逐字节复现”。`docs/memory-quality/test-honesty-audit-2026-11.md` 一个文件被改了 22 次，现在 1,043 行。
- 小号这周合进去的修复：#22（CI 修复 + #20 的四处修复）、#23（考功误降级）、#24（隐私测试 root 误报）、#25（注入日期时区）、#26（否决轮不入库）、#30（被更正的旧值不当事实注入）、#31（探颐冲突过獬豸）、#32（offload_read 进默认工具）、#36（硬墙超时轮不带原话落库）、#39（联想记忆补更正闸，已经并进 master）。

一句话看这周：**主线几乎全在加固**（门禁、诚实性、一致性、否决链），新能力只有 2 个 `feat`，而且都是门禁。加固本身做得很扎实，但 11-07 那一天已经出现明显的空转：同一份审计被一遍遍“逐字节复现”，大部分复验没有新发现。

## 二、外部动态（只列跟兰台方向有关的）

- **Hermes v0.23.0 “The Hearth Release”（11-05）**：新增插件回调 `on_session_delete`，昨天的研究周报已经把它列为 P0，这里不重复。截至本基线，兰台 master、PR #40、PR #43 里都还没有 `on_session_delete`。
- **Hermes 给记忆插件的上下文越来越细**：`agent/memory_provider.py` 的 `initialize()` 文档写明 `agent_context` 取值 `"primary" | "subagent" | "cron" | "flush"`，原文是“skip writes for non-primary contexts”。11-07 的提交 `6b21d056`（目前只在 canary）又加了 `resumed` 和 `message_count`，让插件分得清“新会话”和“接着上次的会话”。这些信号都优先给 MemoryProvider；兰台用的是通用 hook，只能拿到其中一部分（hook 里有 `platform` 和 `parent_session_id`）。
- **cognee v1.6.6（10-29）**：把冲突复核（conflict review）改成默认开启，并加了持久化的冲突裁决 API，裁决会在数据编辑、删除、重建时跟着失效。兰台的探颐已经在做类似的事，可以对照它“裁决什么时候作废”的规则。
- mem0、Letta、graphiti、supermemory 这周没有新版本；MemOS 只发了本地插件的 beta 包，发布说明里没有功能描述。

## 三、发展方向（3 条）

### 方向 1：把“复验”收进门禁，主线回到发版和真实使用

**为什么**：11-07 的 34 个审计类提交里，18 个的标题写着零新发现、零增量、master 未前进或逐字节复现。真正有效的问题（比如 #26、#30、#36、#39 修的那些）都是在新代码合进来、或者换一个角度看的时候发现的，不是在同一份快照上第 N 次复现出来的。测试代码这周涨了 25,648 行，是产品代码的五倍。

**建议**：
- 给自动审计设一个退出条件：master 没前进、票据没变化时，不再起新一轮复验；连续两轮零新发现就停，等下一次合并再触发。
- 能固化的检查（审计快照对账、护栏台账、门禁 8/11）放进 CI 或 `pre-commit`，每次合并自动跑一遍，不再写成新的文档章节。
- 先把 PR #43 收口发成 v0.68，然后用一周时间只做“自己天天用 Hermes + 兰台”，按真实使用里碰到的问题排下一版。

**大概多久**：退出条件和 CI 化半天；发版取决于 #43 的剩余票。

### 方向 2：按 Hermes 的会话类型分级读写（本周 P0 就是这条的第一步）

**为什么**：Hermes 现在一个进程里会跑三类会话：你自己的主会话、它自己定时跑的 cron、你让它拆出去的子智能体。只有主会话的内容是“你的生活和工作”，另外两类是工具性的提示词。Hermes 的接口文档已经把规则写明了（非主会话跳过写入），兰台还没跟上。

**建议**：先做下面“改进 1”的写入隔离；之后再决定读这一侧要不要分级，比如 cron 只注入常驻画像、不跑检索，子智能体按 `parent_session_id` 继承父会话的检索范围。

### 方向 3：用真实使用数据，而不是替身基线，判断注入质量

**为什么**：PR #45 的性能基线说 p95 离 2 秒硬墙还有 17 到 73 倍余量，但同一份报告也写了：这是用确定性哈希嵌入替身测的，真实一次 embedding 大约 2 秒（报告引用的 v0.15.2 结论）。查询向量的 embed 在 `hybrid.py:547` 会吃掉墙宽的 85%（`shell_hook.py:327`），超时后在 `hybrid.py:565` 静默降级成纯关键词检索。也就是说，网络一慢，注入就会悄悄从四路混合退成关键词，而你在 Hermes 那边看不出来。你自己的消融里混合检索 recall@2 是 0.84，这个差距在真实使用里到底有多大，现在没有数据。

**建议**：见“改进 2”，先把降级记下来，用一周真实数据看降级率，再决定要不要做查询向量缓存、或者把 embed 移到墙外预热。

## 四、改进已有代码（3 条）

### 改进 1（P0，约半天到一天）：cron 和子智能体的轮次不写入兰台

**现在的问题**
- Hermes 调 `pre_llm_call` 和 `on_session_end` 时会传 `platform`（`agent/turn_context.py:925`、`agent/turn_finalizer.py:896`）。cron 运行的 agent 是 `platform="cron"`（`cron/scheduler.py:2512`），子智能体是 `platform="subagent"`（`tools/delegate_tool.py:302`）。shell hook 通道里，同一个值放在 stdin JSON 的 `extra.platform`。
- 兰台插件 `hermes-plugin/lantai-hook/__init__.py` 和 `scripts/shell_hook.py` 都不读 `platform`，所有会话一视同仁。

**实测**（在本基线上，直接加载插件模块，把四个出站调用换成记录器）：
- 用 `platform="cron"`、会话 `cron_job42_20261108`、提示词“每天早上读取 deploy.log，总结过去 24 小时的失败部署并推送到 Telegram”跑一轮：插件照常注入记忆，然后在 `on_session_end` 里依次调了 `checkpoint_write`（`cp_active_intent` 就是这句提示词）、对话写通道（同一句原话）和 `backfill`（给注入的记忆记一次使用）。
- 用 `platform="subagent"` 跑“你是一个代码审查子智能体……”，结果一样。
- 再用真实的 `checkpoint_service`（内存 SQLite）走一遍顺序：先写你的会话 `user_s1`“在写兰台 v0.68 的发布说明，下一步补 CHANGELOG”，再写 cron 会话。下一场用户会话 `inject_checkpoint_context()` 的输出是：

```
[Checkpoint · 上次会话]
在做: 每天早上读取 deploy.log，总结过去 24 小时的失败部署并推送到 Telegram
```

你自己的“上次在做”被 cron 覆盖了，因为 `get_latest_checkpoint` 取的是全库最新一条，不分会话类型。Hermes 的 cron 一天跑几次，这个覆盖就会发生几次。

**怎么改**
- 插件：`_on_pre_llm_call` 和 `_on_session_end` 读 `kwargs.get("platform")`。值在 `{"cron", "subagent", "flush"}` 里时：不缓冲用户原话、不写底本、不调对话写通道、不 backfill。读这一侧先保持现状（照常注入）。
- shell hook：同样的判断，读 `payload["extra"]["platform"]`。
- 加一个 `.env` 开关 `LANTAI_SKIP_NONPRIMARY_WRITES`，默认 `true`，想恢复旧行为可以关掉。
- 测试：用上面的两条提示词，断言四个写出口都没被调用；再加一条集成测试，断言 cron 轮之后的下一场用户会话，底本仍是用户自己的那条。

### 改进 2（P1，约一天）：检索事件里记下“向量降级”

**现在的问题**：`hybrid.py` 在 embed 超时、401、未配置时都会降级到关键词兜底（拾遗），只打一条 `logger.warning`。`trace` 模式下有 `fallback` 字段，但注入走的是非 trace 路径，`retrieval_event` 里看不出这一轮是混合检索还是纯关键词。

**怎么改**：
- `hybrid_search` 返回时带一个降级原因（`vector_ok` / `embed_timeout` / `embed_error` / `empty_index`），shell hook 把它写进 `retrieval_event`（加一列）。
- 在每日摘要或 `lantai` CLI 里加一行：过去 7 天注入了多少轮，其中多少轮降级成了纯关键词，硬墙超时（`mark_abandoned`）多少轮。
- 有一周数据后，再决定是否做查询向量缓存（同一句话短时间内重复检索时复用）。

### 改进 3（P1，约半天）：`scripts/reextract.py --apply` 的两个问题

这是巡检在 11-07 两次记下、还没人修的遗留。这次读代码又发现一个：
- **不过獬豸**：`select(RawDocument)` 取全部原文，不看这份原文在摄取时有没有被獬豸拦下，拦下的 RSS 内容会重新变成候选。
- **重复建候选**：“已有候选就跳过”只在 dry-run 时生效（`if existing and not apply`）。加上 `--apply` 以后，已经有候选的原文也会再建一条，跑几次就重复几次。
- 另外，文件开头写的是“重新跑 extractor + gate + evolution”，实际只跑了 extractor。

**怎么改**：按原文的獬豸判定过滤；`--apply` 时同样跳过已有候选（或加 `--force` 才重建）；docstring 改成和实际一致。

## 五、往期建议的进展

- **10-28《兰台记忆项目改进建议》11 条**：注入改成混合检索加闸门、去掉写死的本机路径、触发词放进预过滤、MCP 默认工具收窄，这几条都已经落地。改用 MemoryProvider 接口、开机自启、`remember.md` 导入、`doctor` 自检这几条还没做。
- **11-07 研究周报的 P0（`on_session_delete`）**：还没做。它和本周的改进 1 是同一类问题，都是“不该留下的会话内容，留在了底本里”，可以一起改。

## 没核实的地方

- 改进 1 是直接加载插件模块、把出站调用换成记录器测的，底本那一步用的是真实服务代码和内存库。没有用真的 Hermes 跑 cron 和子智能体。
- 方向 3 里“真实 embedding 约 2 秒”引用的是兰台自己基线报告里的 v0.15.2 结论，我没有在你的网络环境里测过，实际降级率未知。
- 外部项目只看了 GitHub 发布页，没看论文和社区讨论（那部分在研究周报里）。
