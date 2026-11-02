# AI 记忆研究周报 · 2026-11-03（首期）

> 本报告由 Maya（Sesame 的 AI 助手）整理撰写，经 qingshan 授权，通过 GitHub 账号 jiushiqingshand-dev 提交。只改文档，不动代码。
>
> 覆盖时间：2026-10-26 至 2026-11-02（北京时间），少量 10-19 之后的条目单独标注。
> 兰台代码基线：master `cba93612`（v0.65.0），所有行号以这个提交为准。

## 一、让兰台更完善的改进建议

本周最值得做的一件事：**Hermes 的策略插件拦下一轮对话后，兰台照样把这句原话写进记忆。** 改法只有几行，见 P0-1。

四条建议都来自本周的论文、Hermes 更新或同类项目，都在兰台实际代码上复现过。已避开路线图、开着的 PR #19 / #20、`docs/research/` 下已有的周报和巡检报告。

### P0-1 被 Hermes 拦下的一轮，不要写进记忆

- **借鉴来源**：Hermes 本周新增 `pre_llm_call` 拦截能力（提交 [ad9229c843](https://github.com/NousResearch/hermes-agent/commit/ad9229c843)，10-31，已进入 [v0.22.6](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.6)）。官方 hooks 文档写明：被拦下的一轮“不会进入外部记忆提供方”，原始提示词替换成占位文本，`on_session_end` 仍会触发，并带上 `turn_id` 和 `turn_exit_reason="blocked_by_plugin_pre_llm_call"`。这条能力的用途就是 DLP（防止密码、密钥之类发出去）。
- **兰台现在的不足**：
  - `hermes-plugin/lantai-hook/__init__.py:449`，`_on_pre_llm_call` 一进来就先把原始 `user_message` 放进会话缓冲，这时还不知道别的插件会不会拦。
  - 同文件 `:486-496`，`_on_session_end` 不看 `turn_exit_reason`，`:432-438` 的 `_flush_session` 把缓冲里的每一条都送进对话写通道。
  - 我用探针复现过：先用 `"我的银行卡密码是 864213，帮我记一下放哪里比较安全"` 调 `_on_pre_llm_call`，再按 Hermes 的拦截路径调 `_on_session_end(turn_exit_reason="blocked_by_plugin_pre_llm_call", completed=False)`，这句原文被原样送进了 `_call_dialogue`。Hermes 把它拦在模型外面，兰台却把它存进了长期记忆。
- **怎么改**：
  1. `_buffer_turn`（`:409`）记下 `turn_id`。
  2. `_on_session_end` 读 `turn_exit_reason`：等于 `blocked_by_plugin_pre_llm_call` 时，把这个 `turn_id` 的条目从缓冲里删掉，不 flush、不写检查点。`failed=True` 且没有模型回复时也建议同样处理。
  3. 更稳的做法是把缓冲挪到 `post_llm_call`：它只在一轮正常结束时触发，同时带 `user_message` 和 `assistant_response`，P1-3 也能用上回复内容。
  4. 本轮如果已经检索过，`shell_hook` 记下的检索事件里也有原文，建议一起标记或删除。
- **怎么验收**：新增插件测试，按上面的调用顺序断言 `_call_dialogue` 一次都没被调用；`completed=True` 的普通一轮照常写入；装一个总是拦截的测试插件，用真 Hermes v0.22.6 手测一次。
- **工作量和风险**：约 0.5 天。风险是 `on_session_end` 现在每轮都会触发（文档说是 "canonically at each turn finalization"），改缓冲时机要保证会话级检查点不丢。

### P0-2 被更正的旧值，不要再当作当前事实注入

- **借鉴来源**：
  - [When Retrieval Is Not Enough: Obsolete Facts in Agent Memory](https://arxiv.org/abs/2610.36308)（10-26）：证据大小不变，只把一条无关陈述换成过时的竞争陈述，Qwen2.5 的准确率从 100% 掉到 44.6%，50 次答错全部是答了旧值。
  - [CALM](https://arxiv.org/abs/2610.38573)（10-28）：旧记忆可以保留，用来回答“以前怎样”；但问现在的事时，要在注入前按时间适用性把它挡掉，也就是把“保存”和“放进上下文”分开。
  - Hermes 插件目录本周收录的 inspeximus 记忆提供方，把“更正后旧值不再被召回”写成了核心卖点（提交 [b48bfc61a8](https://github.com/NousResearch/hermes-agent/commit/b48bfc61a8)）。
- **兰台现在的不足**：
  - `lantai/cognition/late_correction.py:88-94`，更正后旧记忆的 `status` 仍是 `active`，只把 `lifecycle_status` 改成 `superseded`，再写上 `valid_to`。这是有意设计，保留历史可查。
  - `lantai/retrieval/hybrid.py:249-336` 的 `_apply_supersedes_order` 只是把旧值排到新值后面，分数压到比新值低 1e-6。`scripts/shell_hook.py` 的 `build_context`（`:220` 起，`:282-300` 遍历检索结果）拼注入文本时不看 `lifecycle_status`，也不加任何标记。
  - 我复现过：存“用户住在武汉，在光谷上班”，再用 `apply_supersede_correction` 更正为“用户已经搬到荆州，现在住在荆州”，然后调 `build_context("我现在住在哪个城市，通勤怎么安排比较好？")`。两条一起注入（0.68 和 0.62），旧值前面没有任何“已更正”字样，这正是上面那篇论文测出准确率掉一半的局面。
  - `lantai/eval/chinese_memory_cases.py` 里已有 12 条 superseded 用例，但测的是排序，没测注入文本里还有没有旧值。
- **怎么改**：
  1. 在 `shell_hook.build_context` 里，对 `lifecycle_status == superseded`，或 `valid_to` 早于现在的条目：如果取代它的新记忆也在结果里，就直接丢掉旧条目；不在的话，保留旧条目但加前缀 `[已于 YYYY-MM-DD 更正，仅作历史]`。
  2. 问句里有“以前、当时、之前、去年”这类回看词时，旧条目照常保留，并带上标记。
  3. MCP 的 `search` 带 `as_of` 时，行为保持不变。
- **怎么验收**：把上面的复现写成测试，断言当前态问句的注入文本里没有“武汉”，回看问句（“我以前住在哪？”）里有“武汉”且带“已更正”标记；在 chinese_memory_cases 的 superseded 类里加一条注入层断言；跑一遍 E2 时态评测，确认不退步。
- **工作量和风险**：约 1 天。风险是更正链判错时，会把一条其实没过时的记忆挡掉。所以第一版只处理有明确 `superseded_by` 的记录，不按 `valid_to` 推测。

### P1-3 对话写入换成专用提取规则：分清是谁说的、是一次性请求还是长期偏好

- **借鉴来源**：
  - Hermes 本周两处修改后台记忆复盘：只记对话里真说过的事，不从推理里补（[4a8cc6f705](https://github.com/NousResearch/hermes-agent/commit/4a8cc6f705)）；用户明确说出的喜欢或讨厌算长期偏好，一次性的请求不能升级成偏好（[b47fe6ecc6](https://github.com/NousResearch/hermes-agent/commit/b47fe6ecc6)）。两处都已进入 v0.22.6。
  - [Is it my preference? Role Grounding Attack](https://arxiv.org/abs/2610.38209)（10-28）：转述或引用别人的对话时，记忆系统会把别人的偏好记成用户自己的。
- **兰台现在的不足**：
  - `lantai/ingestion/dialogue.py:130` 调的是 `lantai/parsing/extractor.py:6` 的 `extract_candidate`，用的是 `lantai/llm/prompts.py:1` 的 `EXTRACT_SYS`。这套提示词的开头是 “You are a careful research analyst. Extract STRUCTURED knowledge from the given article/paper text”，是给 RSS 和 arXiv 论文写的。拿它处理用户的聊天消息，既不区分说话人，也不区分一次性请求和长期偏好。
  - 直通规则 `lantai/parsing/zhishu.py:32-44`（用户职业、用户自称两条）只做正则匹配。我实测 `fastpath_check`：
    - “我是你的面试官，现在开始模拟面试” → 直通事实“用户自称：你的面试官，现在开始模拟面试”，置信度 1.0。这是角色扮演指令，被记成了身份。
    - “我是一名资深律师，请帮我审一下这份合同” → “用户职业：资深律师，请帮我审一下这份合同”，请求内容被吞进了事实。
  - `dialogue.py:39` 的 `_PREFERENCE_RE` 在整句里任意位置搜索。“我同事刚发我：我讨厌开早会，你帮我想个委婉的回法”会被分到偏好通道，同事的偏好就成了用户的。反过来，“我对花生过敏”“以后都用中文回复我”这类真正的长期约束，却只落到 general 通道。
- **怎么改**：
  1. 在 `prompts.py` 新增对话专用的 `DIALOGUE_EXTRACT_SYS`，写明：只记用户本人对自己的陈述；引号、冒号后转述、“X 说/发我”里的内容不归给用户；角色扮演和给模型设角色（“我是你的……”“你现在是……”）不入库；一次性请求不升级成偏好；过敏、语言、称呼这类“以后都……”的约束算长期偏好。`extract_candidate` 换用它。
  2. 直通规则加两道排除：句子里有“请、帮我、现在开始”这类请求词的不走直通；“我是你的”开头的不走直通。
  3. `_PREFERENCE_RE` 先剥掉引号和冒号后的转述部分再匹配，再补上“过敏、以后都、别再”这几类约束词。
  4. P0-1 改完后，可以把模型回复一起送给提取器，用来判断这句话是不是在扮演角色。
- **怎么验收**：上面 7 句做成参数化测试，断言直通结果和分道都符合预期；在 chinese_memory_cases 里新增约 10 条“归属”类用例（转述、角色扮演、一次性请求）；没有模型密钥时走 `extractor.py` 的 fallback，确认不报错。
- **工作量和风险**：1.5 到 2 天。风险是规则收紧后，少数真实的自我介绍会被挡掉，比如“我是一名律师，帮我看下这个”。建议被挡下的进待审队列，不直接丢弃。

### P2-4 注入文本去重，并按论文结论收紧预算

- **借鉴来源**：同一篇 [Obsolete Facts](https://arxiv.org/abs/2610.36308)：BM25 检索下，证据预算从 64 token 加到 512 token，准确率从 45% 降到 32%，虽然每次检索结果里都有最新的那条。[Agent Memory as Recommendation](https://arxiv.org/abs/2610.34673)（10-26）也把“每次请求只放进少数几条”当作核心约束，按有预算的推荐问题来处理。
- **兰台现在的不足**：上面 P0-2 的复现里，每条记忆在注入文本里出现两次：一次在“本次依据”（`scripts/shell_hook.py:376-387`），一次在“相关记忆”。预算 `SHELL_HOOK_MAX_TOTAL_CHARS=1500` 只管“相关记忆”那一段（`_apply_recall_budget`，`:356`），“本次依据”在预算外另算，实际注入最多接近 2 倍。“本次依据”是为了回执和审计而设计的，这点我理解。
- **怎么改**：“本次依据”只列 `id` 和分数，正文只在“相关记忆”里出现一次；或者两段合成一段，每行带 `id`。预算按合并后的总长计算。可以再试一个小实验：top-k 从 5 降到 3，看 selfproof 和 LongMemEval 子集的变化。
- **怎么验收**：注入文本里每条正文只出现一次；回执和 `recall_log` 的 `evidence` 字段不变；selfproof recall@2 不低于现在的 0.84。
- **工作量和风险**：约 0.5 天。风险是 Hermes 端如果有提示词或技能依赖“本次依据”的格式，需要同步改。

## 二、上周建议跟进

首期，没有上周的建议可以跟进。从下期开始，这里会逐条记录上面四条是否已采纳、是否被拒。

## 三、本周论文（2026-10-26 至 11-02）

按和兰台的相关程度排序。

1. **[When Retrieval Is Not Enough: Obsolete Facts in Agent Memory](https://arxiv.org/abs/2610.36308)**（10-26）。新事实取代旧事实后，问题出在检索之后：旧陈述和新陈述一起进了上下文，模型就容易答旧值。证据预算越大，答得越差。→ 对应建议 P0-2、P2-4。
2. **[CALM: Conflict-Aware Lifecycle Memory Governance](https://arxiv.org/abs/2610.38573)**（10-28）。把“记忆保存”和“这一问能不能用它”分开，按支持、冲突、依赖信号更新有效期，注入前按时间适用性把关；在 LongMemEval-S 和 LoCoMo 上取得最好的总体准确率。→ P0-2。
3. **[Is it my preference? Role Grounding Attack](https://arxiv.org/abs/2610.38209)**（10-28）。引用、嵌套对话里别人的偏好，会被记成用户自己的。→ P1-3。
4. **[Accept Changes, Reject Attacks](https://arxiv.org/abs/2610.41056)**（10-29）。“事实更新”和“记忆投毒”要求相反的写入决策，论文提出 ECA 指标，同时检查答案对不对、用到的证据是不是最新、有没有被污染。我拿兰台的注入检查试了 8 句：“忽略我之前说的，我现在不喝咖啡了”这类正常更正都能通过，“忽略之前的所有指令……”被拒，说明兰台目前没有误伤正常更正。
5. **[KITE](https://arxiv.org/abs/2610.41521)**（10-30）。不用向量，靠一套受管的主题词表组织记忆，把问题编译成可检查的检索计划；LoCoMo 93.5%，LongMemEval-S 86.0%。兰台的知枢分道和它思路相近。
6. **[Long-Term Agent Memory through Complementary Curation and Consolidation](https://arxiv.org/abs/2610.39790)**（10-29）。情节记忆加结构化关系记忆，再加巩固；LoCoMo 90.2%，LongMemEval-S 85.5%。
7. **[ETAMem](https://arxiv.org/abs/2610.37443)**（10-27）。把异构记忆拆开再对齐；相对最强基线，LoCoMo 提升 12.46%，LongMemEval-S 提升 7.96%。
8. **[Adaptive Memory Index Evolution（AMIE）](https://arxiv.org/abs/2610.34977)**（10-26）。只改“怎么被找到”、不改记忆内容的插件层。意外发现是，简单累计检索次数的零调用策略，就能追平甚至超过语义判断。兰台已在 PR #20 里让 Hermes 注入刷新“最后使用时间”，这篇可以作为后续是否计入注入次数的参考。
9. **[MemSteer](https://arxiv.org/abs/2610.39276)**（10-29）。从没有标注的用户历史里自动生成带证据的问答，用来调记忆设计。和兰台的 selfproof 思路一致，可以参考它的“盲答复核”来给自动生成的题目把关。
10. **[Agent Memory as Recommendation](https://arxiv.org/abs/2610.34673)**（10-26）。把“每次放哪几条记忆进上下文”当作有预算的推荐问题，强调多会话问题要把证据凑齐。→ P2-4。
11. **[Proactive Memory for Event-Driven LLM Agents](https://arxiv.org/abs/2610.36967)**（10-27）。事件驱动的主动记忆，ProactiveStream 上 F1 52.6%。
12. **[MUSE](https://arxiv.org/abs/2610.38332)**（10-28）。面向科研编程智能体的多层记忆和按需注入。
13. **[Learning to Remember](https://arxiv.org/abs/2610.35089)**（10-26）。大模型记忆机制综述，适合当索引用。

## 四、开源项目动态

- **Hermes Agent [v0.22.6](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.6)**：`pre_llm_call` 可以在推理前拦下一轮（DLP 用途）；后台记忆复盘改为只记对话里说过的事；明确说出的喜欢或讨厌算长期偏好；新增可选的“提前准备压缩”（`compression.prepare_ahead`）；插件目录新收录 inspeximus、severian 两个记忆提供方。前三项分别对应建议 P0-1 和 P1-3。另有提交 [857e29d4cc](https://github.com/NousResearch/hermes-agent/commit/857e29d4cc) 修了记忆提供方钩子失败的上报问题。
- **MemOS 本地插件 [v2.0.21](https://github.com/MemTensor/MemOS/releases/tag/memos-local-plugin-v2.0.21)**：本周发布。
- **cognee [v1.6.6](https://github.com/topoteretes/cognee/releases/tag/v1.6.6)**：本周发布。
- **mem0**：本周没有新版本，主干有 3 个提交。
- **supermemory**：本周没有新版本，主干有 9 个提交。

这两个发布的具体内容，我只看了发布说明，没有逐项核对，所以不展开。

## 五、新评测与榜单

- **本周新评测**：Obsolete Facts 一文自带的过时事实评测；ECA 指标（Accept Changes, Reject Attacks）；[BehaviorMem](https://arxiv.org/abs/2610.34790)（10-26，用电商行为日志分开评“记住”和“推断”）；[Finding Is Not Returning](https://arxiv.org/abs/2610.39781)（10-29，评智能体能否主动回到沉寂的对话，并把上下文接上）。
- **新报告的成绩**（均为论文自报的问答准确率）：KITE 在 LoCoMo 上 93.5%、LongMemEval-S 上 86.0%；Complementary Curation 分别为 90.2%、85.5%。
- **和兰台的对比口径**：兰台的 `docs/benchmarks/longmemeval-2026-10` 是 25 题子集上的检索召回率（recall@2 / @5），不是问答准确率，不能直接和上面的数字比较。想对比，需要在 VM 上配好模型密钥，跑一次完整问答。

## 六、方法与未验证说明

- **检索范围**：用 arXiv API 按 18 组关键词检索了 2026-10-19 之后提交的论文，再人工筛出 7 天内与智能体记忆直接相关的条目。开源项目通过 GitHub API 查看发布和主干提交。
- **复现环境**：三处复现都在 VM 上的兰台 master 克隆里，用一次性探针测试完成，探针没有提交进仓库。
- **没验证的**：
  - 改进建议只给了改法，代码没改，测试也没跑。
  - 论文里的数字都来自 arXiv 摘要，没有读全文核对。
  - 没有用真 Hermes v0.22.6 跑过 P0-1 的端到端场景，只按官方 hooks 文档和 `agent/turn_finalizer.py` 的调用参数做了模拟。
