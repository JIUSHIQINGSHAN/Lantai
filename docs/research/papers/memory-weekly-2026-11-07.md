# 兰台 · AI 记忆研究周报 2026-11-07

> 覆盖时间：2026-10-31 至 2026-11-07。整理者：Maya（Sesame 的 AI 助手），每周六自动生成。
> 兰台基线：origin/master `57cf71a6`（v0.66.0 之后 76 个提交，含 v0.67、v0.68 的票）。

## 一句话结论

这周最值得兰台做的一件事：**接上 Hermes v0.23.0 新增的 `on_session_delete` 回调**。现在你在 Hermes 里删掉一场会话，兰台仍然留着那场会话的原话、待审候选、检索日志和底本；下一场新会话开头，还会把被删会话里的原话当作“上次在做”注入给模型。依据是 [Hermes v0.23.0 发布说明](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.0) 里列出的新回调，以及把“来源被删除”当作必须检测的变化之一的 [MemShift](https://arxiv.org/abs/2611.02338)。改法大约 1 天。

## 让兰台更完善的改进建议

### P0-1 订阅 Hermes 的 `on_session_delete`：你删掉的会话，兰台跟着清掉

**做什么、借鉴谁**
- Hermes v0.23.0 新增插件回调 `on_session_delete`（[PR #140995](https://github.com/NousResearch/hermes-agent/pull/140995)，[hooks 文档](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)）。删会话、批量删、保留期清理（prune）、ghost-prune、profile 修复都会触发，参数有 `session_id`、`reason`、可选的 `deleted_ids`，不带消息内容。`reason="archive"` 表示你只是隐藏了会话，可以恢复。
- [MemShift](https://arxiv.org/abs/2611.02338) 把“来源被删除”列为记忆必须察觉的五种变化之一，并且把“察觉”和“修复”分开评估。

**为什么适合兰台、现在哪里不足**
- 插件只注册了两个回调：`hermes-plugin/lantai-hook/__init__.py:611-613` 只有 `pre_llm_call` 和 `on_session_end`。全仓没有按会话删除的函数（`grep` 只找到 `purge_expired` 和 `purge_facts_for_memory`）。
- **本地实测**（临时库，`shell_hook.py --serve`，会话 `sess-del-001`）：发两轮对话“我对青霉素过敏，以后开药记得提醒我”“我下周三要去协和医院复查甲状腺结节”，写一次底本，再检索一次。库里留下：
  - `rawdocument` 2 行，原话全文，而且这两行的 `session_id` 是空的，只能经 `memorycandidate.document_id` 找回来；
  - `memorycandidate` 2 行，`pending_review`，带 `session_id`；
  - `retrieval_event` 1 行，`query_text` 是原话；
  - `session_checkpoint` 1 行。
- 换一个新进程请求底本，返回的是 `[Checkpoint · 上次会话]\n在做: 我下周三要去协和医院复查甲状腺结节`。也就是说，你在 Hermes 里把这场会话删了，下一场新会话第一轮还会把它注入给模型。

**怎么改**
1. 插件加 `_on_session_delete(session_id, reason, profile, **kw)`，在 `register()` 里注册。`reason` 是 `delete`、`prune`、`ghost_prune`、`profile_repair` 时，对 `session_id` 和 `deleted_ids` 里的每个 id 发新帧 `{"type": "session_purge", "session_id": ...}`；`archive` 和 `empty_sweep` 不动。
2. `scripts/shell_hook.py` 加 handler：
   - 删 `session_checkpoint` 里这场会话的行（`lantai/services/checkpoint_service.py:200` 的 `get_latest_checkpoint` 就不会再取到它）；
   - 这场会话的 `retrieval_event` 按 `mark_vetoed`（`lantai/observability/retrieval_log.py:260`）同样抹掉原话；
   - 还在待审的 `memorycandidate` 连同它 `document_id` 指向的 `rawdocument` 一起删；
   - 已经晋升成正式记忆的**不自动删**，标成“来源会话已删除”放进终端待审，由你决定。删聊天不一定等于要忘掉“青霉素过敏”这种事实。
3. Hermes 文档写明这个回调只在执行删除的那个进程里触发，也不保证恰好一次。所以 handler 要幂等；在 CLI 里 `hermes sessions delete` 时 serve 子进程可能还没起来，可以先把 `session_id` 追加到 `LANTAI_HOME` 下的一个待清理文件，serve 启动时补做。

**怎么验收**
- 新增 `tests/test_plugin_session_delete.py`：按上面的实测步骤造数据，调 `_on_session_delete(session_id=..., reason="delete")` 后四张表里没有这场会话的行，`get_latest_checkpoint` 不再返回它；`reason="archive"` 时全部保留；同一 id 调两次不报错；`deleted_ids` 里的子会话也被清理。撤掉修复，这些测试应失败。

**工作量和风险**：约 1 天。风险是误删，所以只按 `session_id` 精确匹配，正式记忆只标记不删除。

### P1-2 接上 `post_llm_call`：记下注入的记忆到底“用上没有”

**做什么、借鉴谁**
- [Delivered, Not Used（MemUse）](https://arxiv.org/abs/2611.01718)：在 8,000 个 SWE-bench Verified 轨迹上，三套记忆系统的解决率和不用记忆相差不到 0.4 个百分点，但逐条看发现：大多数送进去的记忆不相关，大多数相关的没被用上，两套系统里多数送达内容是智能体本来就看得到的。
- [MEMOIR](https://arxiv.org/abs/2611.07631)：每次回答后估计哪些检索到的记忆真正支撑了答案，再调整它们的检索优先级。在 1,905 道去重的 LoCoMo 题上，不用参考答案的置信门控反馈让 evidence recall@5 多了 1.50 个百分点。
- [LEDGER](https://arxiv.org/abs/2611.03289) 提醒另一面：只看 top-k 检索日志（谁被取到、谁和谁一起被取到），任何一条记忆的价值都定不下来。所以“被注入了几次”不能当价值用。

**为什么适合兰台、现在哪里不足**
- 兰台已经把注入次数单独记成 `injection_count`（`lantai/memory/forgetting.py`、`lantai/observability/retrieval_log.py`），这和 LEDGER 的结论一致。但插件从不发“有用”信号：考功和三省只能靠你手动反馈，Hermes 每天注入的记忆用没用上，兰台不知道。
- Hermes 的 `post_llm_call` 带 `assistant_response` 和 `turn_id`（hooks 文档）。注意：11-05 巡检报告（`docs/research/improve-2026-11-05-veto-no-write.md:77`）已查实，被否决的一轮也会触发 `post_llm_call`，回复就是拦截提示。

**怎么改**
1. 插件注册 `post_llm_call`，只做一件事：按 `turn_id` 找到缓冲条目，把 `assistant_response` 挂上去。
2. 真正上报放在 `_flush_session`（`__init__.py:498`）里，和现有回执一起发新帧 `{"type": "use_check", "event_id", "used_ids", "response"}`。被否决的那一轮走不到 flush，自然不会上报，复用现成的否决链。
3. serve 侧先用确定性规则判断：记忆里的关键实体、数字或 jieba 关键词出现在回复里，就给这条记忆记一次 `used_signal`。这个计数单独存，不并进 `use_count`，先只在考功报告里显示，不改重要度，观察两周再定。

**怎么验收**
- 新增测试：注入“我对花生过敏”，回复里提到花生，`used_signal` 加 1；回复无关，不加；被否决的一轮不加；无 session 的轮次不报错。
- 跑一周后从 `retrieval_event` 统计“注入了但没用上”的比例，作为以后调整注入条数的依据。

**工作量和风险**：约 1.5 天。关键词命中不等于真的用上（MemUse 用的是智能体评审），所以只当观察信号。

### P2-3 接上 `post_compaction`：上下文压缩后重新注入一次底本

**做什么、借鉴谁**：Hermes v0.23.0 的另一个新回调 `post_compaction`（[PR #141017](https://github.com/NousResearch/hermes-agent/pull/141017)）。hooks 文档说它就是给记忆插件用的：压缩把之前注入的内容概括掉以后，插件可以在下一次 `pre_llm_call` 重新注入。参数里 `old_session_id` 为空表示原地压缩。

**现在哪里不足**：底本每场会话只注入一次，靠 `_checkpoint_injected`（`__init__.py:112`，判断在 `:529`）。原地压缩后 `session_id` 不变，第一轮注入的“上次在做、下一步、决策”被概括掉，之后不会再注入。压缩换了新 `session_id` 时，新 id 不在集合里，会正常注入。

**怎么改**：注册 `post_compaction`，在 `_lock` 内把 `session_id` 从 `_checkpoint_injected` 里移除。

**怎么验收**：测试同一会话第一轮注入底本、第二轮不注入；调 `post_compaction(session_id=..., old_session_id="")` 后下一轮再注入一次，之后又不注入。

**工作量和风险**：半天，风险低。

### P2-4 更正一条记忆时，把依赖它的记忆一起标出来

**做什么、借鉴谁**
- [StateGraph](https://arxiv.org/abs/2611.02912)：上游事实变了，记忆系统往往只改了被推翻的那条，由它推出的信念和计划仍被当真。它用反事实检验区分“依赖”和“只是相关”，只有在没有其他支撑路径时才让依赖项失效。在作者自建的 50 例 StateChangeBench 上，Impact-F1 比 CUPMem 复现高 6.9 个百分点（一层依赖）和 26.0 个百分点（三层以上）。
- [MemShift](https://arxiv.org/abs/2611.02338) 的结论也指向同一处：察觉到变化有多大用，取决于修复做得多好。

**现在哪里不足**：#30 和 v0.68 票 02 的更正闸（`lantai/retrieval/supersede.py:59`）只处理 `lifecycle_status=superseded` 且有 `superseded_by` 的那一条本身。兰台的边类型里已经有 `supports`、`causes`、`leads_to`（`lantai/models/enums.py:44-52`），但更正时不沿这些边去看谁依赖它。比如“住武汉”被更正为“搬到荆州”，由“住武汉”推出的其他记忆照样当作现在的事实注入。你真实库里这类边有多少，我没看到，未核实。

**怎么改**：`lantai/cognition/late_correction.py` 写 `supersedes` 边时，顺着被更正记忆出发的 `supports` 和 `causes` 边只看一跳，把目标记忆标成“前提已更正，待复核”放进终端，不自动失效；注入时加一个轻标记。目标记忆若还有别的支撑来源，不标。

**怎么验收**：测试 A supports B，A 被更正后 B 注入时带“前提已更正”标记；B 另有独立的 supports 来源时不带标记；退出更正闸开关时行为回到现在。

**工作量和风险**：1 到 2 天。兰台的边多由模型抽取，噪声可能大，标记太多会打扰你，所以先只看一跳、只标不删。

## 上周建议跟进

| 上周编号 | 建议 | 状态 | 依据 |
|---|---|---|---|
| P0-1 | Hermes 拦下的一轮不进记忆 | 已采纳 | [#26](https://github.com/JIUSHIQINGSHAN/Lantai/pull/26) 已于 11-06 合并；[#33](https://github.com/JIUSHIQINGSHAN/Lantai/pull/33)（否决轮收口：`retrieval_veto` 作废检索事件、`mark_vetoed`）也已合并，补上了 #26 报告里留下的两处 |
| P0-2 | 更正后的旧内容不再当现在的事实注入 | 已采纳 | [#30](https://github.com/JIUSHIQINGSHAN/Lantai/pull/30) 已于 11-06 合并；v0.68 票 02 把更正闸挪到 `lantai/retrieval/supersede.py` |
| P1-3 | 对话提取换一套专门的规则 | 没动，本周再提一次 | `lantai/ingestion/dialogue.py`、`lantai/parsing/`、`lantai/llm/prompts.py` 本周没有提交。仍值得做：#26、#30、#33 让对话写通道成了每轮都在跑的主入口，“我是你的面试官”被记成你本人身份的问题还在。下周还没动就不再提 |
| P2-4 | 注入文本里每条记忆出现两次 | 没动 | 本周 `scripts/shell_hook.py` 的改动是否决和更正闸，渲染部分没改 |

## 论文（按和兰台的相关度排序）

机构：arXiv 摘要页不列机构，下列各篇的机构均未核实。数字都来自 arXiv 摘要，全文没核对。

1. **[Delivered, Not Used: Evaluating Agent Memory at Its Point of Use](https://arxiv.org/abs/2611.01718)**（2026-11-01）
   - 方法：MemUse 在记录下来的轨迹里逐条评估每次送达的记忆，和智能体当时本来能看到的内容比，再追踪它后续的行动和后果。
   - 数字：8,000 个 SWE-bench Verified 轨迹、三套记忆系统，解决率与无记忆基线相差不到 0.4 个百分点；有益使用的轨迹里 23.2% 仍然失败，16 条有害使用里 5 条仍然成功。
   - 和兰台的关系：直接对应 P1-2。兰台现在只知道“注入了”，不知道“用上了”。
2. **[MEMOIR: A Training-Free Memory Lifecycle for Long-Horizon LLM Agents](https://arxiv.org/abs/2611.07631)**（2026-11-05）
   - 方法：压缩前用评审闸检查答案相关证据是否保住；每次回答后归因哪些记忆支撑了答案并更新检索优先级；加 UCB 式探索奖励，防止常被取到的记忆挤掉少见但有用的。
   - 数字：1,905 道 LoCoMo 题上，完整检索栈的 evidence recall@5 比纯余弦高 11.92 个百分点；置信门控反馈再加 1.50；100 次独立审计的合并里观察到 0 次损失。
   - 和兰台的关系：“用后归因”对应 P1-2；“压缩前先查有没有丢证据”对得上兰台的演化与合并，可作为以后给 AutoDream 合并加闸门的参考。
3. **[MemShift: Separating Detection from Repair in LLM-Derived Personal Memory](https://arxiv.org/abs/2611.02338)**（2026-11-02）
   - 方法：五种变化（来源修订、删除、重新定义、写入者切换、导出），用确定性评分区分“有没有察觉”和“修得对不对”。
   - 结论：察觉的作用受限于修复能力；让模型在类型化记录上写程序，能补上和精确重算之间的大部分差距。代码数据：https://github.com/zichuanxu/memshift
   - 和兰台的关系：“删除”对应 P0-1，“依赖修复”对应 P2-4。
4. **[A State Change Is Local, Its Consequences Are Not: Dependency-Aware State Revision for Long-Term Agent Memory](https://arxiv.org/abs/2611.02912)**（2026-11-02）
   - 方法：StateGraph 从交互历史恢复状态依赖图，用反事实检验判断依赖，只在严格依赖失效且没有其他支撑时才让下游状态失效。
   - 数字：50 例 StateChangeBench 上 Impact-F1 比 CUPMem 复现高 6.9（一层）和 26.0 个百分点（三层以上）。
   - 和兰台的关系：对应 P2-4，是 #30 更正闸的下一步。
5. **[Top-k Retrieval Leaves Memory Value Undetermined](https://arxiv.org/abs/2611.03289)**（2026-11-03）
   - 方法：把检索日志当成一场没人设计过的实验，证明 top-k 检索下每条记忆的价值都无法确定；LEDGER 给能确定的记忆算闭式 Shapley 值和置信区间，并调整检索让下一份日志能确定更多。
   - 数字：14 个记忆评测、55 种配置里没有一条记忆的价值是确定的；pooled HotpotQA 上可确定比例从 0.001 升到 0.797，HotpotQA 准确率降 2.6 点，LoCoMo 升 2.7 点。
   - 和兰台的关系：支持兰台把 `injection_count` 和 `use_count` 分开；提醒别用注入次数推重要度。
6. **[What Is This Memory Useful For? Measuring Task-Conditioned Utility in Long-Term Agent Memory](https://arxiv.org/abs/2611.03930)**（2026-11-03）
   - 方法：同一条记忆对不同任务价值不同，用“有它和没它”的配对实验量化，而不是给每条记忆一个固定重要度。
   - 数字：在 LongMemEval-V2 网页智能体记忆构造的事实回忆任务上，含答案的记忆比对照多带来 +0.33 的平均配对收益（95% 区间 +0.21 到 +0.46）；摘要化和结构化记录的收益都比原文小；在软件工程和科学编程任务上，连原文记忆都没有可测效果。
   - 和兰台的关系：兰台用单一重要度加 Ebbinghaus 遗忘；这篇说明“对哪类问题有用”比单一分数更准。目前仅供参考，暂不建议改模型。
7. **[MemHarmBench: Evaluating Cross-Session Prompt Injection in Memory-Persistent AI Agents](https://arxiv.org/abs/2611.01700)**（2026-11-01）
   - 方法：先让智能体接触恶意指令，删掉攻击内容后开新会话做正常任务，看它是否仍按攻击者意图做决定。
   - 结论：跨会话攻击可以很隐蔽，任务照常完成、没有异常，但关键决定已经偏向攻击者。数据：https://github.com/Kirito14IT/MemHarmBench
   - 和兰台的关系：兰台的獬豸闸、#31 的探颐补闸、v0.68 票 03（RSS/arxiv 收录过獬豸）都在防这件事。可以拿它的用例补进兰台的注入测试。
8. **[One Assistant, Many Memories: Benchmarking Real-World Group Assistance When Recall Is Not Enough](https://arxiv.org/abs/2611.04867)**（2026-11-03）
   - 方法：GroupAssistBench，来自真实群聊部署，50 个群、80,299 条消息，分“回忆”和“使用”两层评估。
   - 结论：最强的记忆系统在回忆上接近全上下文，在使用上差得远，尤其是分散和冲突的记忆。
   - 和兰台的关系：兰台现有评测只测检索召回，这篇再次说明召回高不代表用得好。兰台是单人使用，群聊部分仅供了解。
9. **[TrackMem: Tracking Evolving Beliefs in Long-Horizon Financial Dialogue](https://arxiv.org/abs/2611.02001)**（2026-11-01）
   - 方法：按说话人建图，追踪数字的修订历史。新评测 FinLoCoMo，15 段投资对话、765 题。
   - 数字：失败模式子集上 73.9% 对最强基线 69.4%；“数字被修订”类问题 86.5% 对 73.4%。
   - 和兰台的关系：和 #30 的更正闸同一类问题，可借它的“修订过的数字”题型给兰台的更正闸补测试。
10. **[Reglos: A Self-Evolving Personal Memory Agent with Recursive Self-Improvement](https://arxiv.org/abs/2611.06049)**（2026-11-04）
    - 方法：用编码智能体根据失败轨迹给记忆框架打补丁，另一家族的模型当评审筛掉刷榜补丁，冻结后再由固定评估器决定是否接受。
    - 数字：Qwen3.8-27B 下 PersonaMem-v2 61.3%、LongMemEval-S 84.0%；GPT-5.6 Luna 下 57.5% 和 87.2%。
    - 和兰台的关系：兰台现在正是“AI 根据失败改代码”的开发方式（包括 Maya 的巡检 PR）。它的做法提醒：补丁只在它针对的失败上测过，容易过拟合，验收要加不相关的回归集。

## 开源项目动态

| 项目 | 本周变化 | 和兰台的关系 |
|---|---|---|
| [Hermes Agent v0.23.0](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.0)（11-05） | 新插件回调 `post_compaction`、`on_session_delete`；终端 `/rewind` 可以回到之前的提问，编辑重发或从那里分叉新会话（[PR #144937](https://github.com/NousResearch/hermes-agent/pull/144937)），被回退的消息在 `state.db` 里标为 `active=0`；一个 gateway 统一托管会话，CLI、TUI、Desktop 不再各跑一个智能体；新增 Mnemoverse、post-cortex、Membase、conxt、Inspeximus 等记忆提供方 | 两个新回调对应 P0-1、P2-3。`/rewind` 和统一 gateway 的影响见“未核实事项” |
| [Hermes Agent v0.22.7](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.7)（11-03） | 补丁版，已并入 v0.23.0 的说明 | 无单独改动需要跟 |
| [Cognee v1.6.7](https://github.com/topoteretes/cognee/releases/tag/v1.6.7)（11-06） | 摄取默认拒收数据库连接串和 HTTP 上的服务器文件路径；限定范围的检索更准；PGVector、Turso、LanceDB 批量打分；遗忘和会话缓存的删除改为批量 | “摄取时默认拒收危险输入”和兰台 v0.68 票 03 方向一致 |
| [MemOS 本地插件 v2.0.21-beta.1](https://github.com/MemTensor/MemOS/releases/tag/memos-local-plugin-v2.0.21-beta.1)（11-04） | 预发布，说明里只有打包信息（10 个提交、59 个文件），没写功能变化 | 仅供了解 |
| Supermemory | 没有新版本；11-02 提交让 MCP 从 v5 命名空间图谱读记忆图（[#1871](https://github.com/supermemoryai/supermemory/pull/1871)） | 仅供了解 |
| mem0 | 没有新版本（最新仍是 9-25 的 v2.2.1）；本周提交都是依赖安全修复 | 仅供了解 |
| Graphiti、Letta、LangMem、A-MEM | 本周没有新版本 | — |

## 评测动态

- **新评测集**：GroupAssistBench（真实群聊，回忆和使用分层）、MemHarmBench（跨会话注入）、StateChangeBench（依赖修订，50 例）、FinLoCoMo（765 题）、MemShift（五种变化）、Conditional Memory Utility Dataset（配对收益）。共同方向：从“找没找到”转向“用没用对”“变了能不能跟上”。
- **新结果**：Reglos 在 LongMemEval-S 上 84.0%（Qwen3.8-27B）和 87.2%（GPT-5.6 Luna）；MEMOIR 在 LoCoMo 上 evidence recall@5 比纯余弦高 11.92 个百分点；LEDGER 在 LoCoMo 上 +2.7。
- **兰台大概在哪**：兰台的公开数字是 LongMemEval-S cleaned 25 题子集上会话级 recall@5 = 0.88（`docs/benchmarks/selfproof-report-2026-10.md`），测的是检索召回，不测回答。Reglos 的 84.0% 是回答准确率，两者不能直接比。按本周几篇论文的方向，兰台最缺的是“用没用上”这一层的评测，P1-2 的观察信号可以作为起点。
- 本周没查到 LongMemEval、LoCoMo、MemoryAgentBench 官方榜单的更新。

## 未核实事项

- 所有论文数字来自 arXiv 摘要，全文没核对；机构没核实。
- P0-1 是用兰台自己的 serve 模拟的，没用真的 Hermes 删过会话；`on_session_delete` 在 CLI 进程里触发时插件能否及时连上 serve，没验证。
- `/rewind` 回退的提问在 Hermes 里标为 `active=0`，但兰台在那一轮结束时已经写过了。hooks 文档里没看到 rewind 对应的插件回调，所以兰台目前不会知道这些轮次被撤回。是否值得处理，等你实际用到 `/rewind` 再看。
- v0.23.0 让 CLI、TUI、Desktop 都走同一个 gateway。如果你同时配了 `cli-config.yaml` 里的 shell hook（`docs/hermes-install-handoff.md` 第 3 步）和 lantai-hook 插件，升级后是否会注入两次，没验证。升级前值得先查。
- P2-4 里你真实库里 `supports`、`causes` 边的数量和质量没看到。
- 本周没看 X 上的讨论和 Hugging Face Papers 热榜。
