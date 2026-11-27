# 兰台记忆研究周报 2026-11-21

> 覆盖 2026-11-14 至 2026-11-21 的 arXiv 新论文、开源项目更新和评测动态。兰台对照基线：master `eaa5d29a`（2026-11-20 04:53 +0800，最新标签 v0.69.1）。上一份周报：`docs/research/papers/memory-weekly-2026-11-14.md`（PR #52，已合并）。本报告由 Maya（Sesame 的 AI 助手）生成。

## 一句话结论

这周最值得兰台做的一件事：**对话在写进兰台之前先脱敏**。我在真 Hermes v0.23.1（假模型）上跑了一场带密钥的三轮会话：硅基流动 key、带密码的数据库连接串、GitHub token 三样原样进了兰台的 `rawdocument`（2 行）、`memorycandidate`（2 行）和 `retrieval_event`（1 行），也原样发给了兰台自己的提取模型。兰台的 `secret_guard` 只认得出其中的 `sk-` key，而且默认是 `shadow` 模式，只记日志不拦。Hermes v0.23.2 起已经给插件提供了现成的 `ctx.redact_text()`，cognee 的 Hermes 插件也默认脱敏。

## 让兰台更完善的改进建议

### P0 对话写入兰台前先脱敏（借鉴 Hermes `redact_text`、cognee Hermes 插件、HEAMS）

- **做什么**：
  1. Hermes 插件 `hermes-plugin/lantai-hook/__init__.py` 把这一轮放进 `_session_buffers` 之前，先过一遍脱敏：有 `ctx.redact_text` 就调 `ctx.redact_text(text, force=True)`；没有（Hermes v0.23.1 及更早）就退回兰台自己的规则。
  2. 服务端 `lantai/security/secret_guard.py` 的规则补齐到至少覆盖：GitHub `github_pat_`/`gho_`/`ghp_`、Slack `xox?-`、JWT、URL 里的 `user:password@`、`*_PASSWORD=`/`*_KEY=` 赋值。对话来源（`source_type=dialogue`）在入库前替换成占位符，而不是 `shadow` 只记日志。
  3. CLI 模式 `scripts/shell_hook.py` 走同一个脱敏函数。
- **为什么**：
  - 我的实测（见下）说明密钥现在会进三张表，还会发给外部提取模型（生产上是硅基流动）。
  - [HEAMS](https://arxiv.org/abs/2611.20144)（11-15）把记忆系统定义为“用户长期信息的保管者”，评测维度里专门有“在不合适的场合被披露”和“难以更正或删除”两项，不只看任务分数。
  - cognee v1.6.9（11-18）的 Hermes 集成 `capture_policy.py` 写明 `COGNEE_CAPTURE_REDACT` 默认开启，会清掉私钥、连接串、token、凭据赋值和厂商 key；规则解析失败时“什么都不存”（fail closed）。
- **实测 1：真 Hermes 全链路**（巡检机上的 `hermes-e2e` 目录，Hermes v0.23.1 + 兰台插件 + 假模型，兰台用本周 master）：
  - 会话 1 三轮：①“记一下：兰台的硅基流动 key 是 sk-abc…456，数据库连 postgres://lantai:Hunter2pass@10.0.0.5:5432/lantai”；②“顺便记住 GitHub token github_pat_11ABC…”；③“好的谢谢，我去吃饭了”。会话 2：“我上次做到哪了？”
  - 结果：兰台库里含这几样密钥的行：`rawdocument` 2、`memorycandidate` 2、`retrieval_event` 1。兰台调提取模型时，①②两句原文出现在发给模型的消息里。
  - 会话 2 发给 Hermes 模型的最后一次请求里没有出现这些密钥（这次没被注入回去），但它们已经躺在库里，以后命中相关问题就可能被检索出来。
- **实测 2：两套规则对比**（9 种密钥写法 + 1 句普通对照）：

| 样本 | 兰台 `find_secret_shapes` | Hermes `redact_sensitive_text` |
|---|---|---|
| `sk-` 风格 key | 命中 | 改写 |
| AWS `AKIA…` | 命中 | 改写 |
| GitHub 细粒度 PAT `github_pat_` | 未命中 | 改写 |
| GitHub OAuth `gho_` | 未命中 | 改写 |
| Slack `xoxb-` | 未命中 | 改写 |
| URL 里的密码 | 未命中 | 改写 |
| `DB_PASSWORD=` 赋值 | 未命中 | 改写 |
| JWT | 未命中 | 改写 |
| 中文口述“我的银行卡密码是 864213” | 未命中 | 未改写 |
| 对照：“我对花生过敏，下周三去协和复查” | 未命中 | 未改写 |

  兰台 2/9，Hermes 8/9，两边都没误伤普通句子。中文口述密码两边都抓不到，这一类仍要靠 #26 那种“Hermes 拦下就不入库”的路子，或者另加中文规则。
- **验收**：
  - 用上面会话 1 重跑真 Hermes，三张表里搜不到 `sk-abcdefghij`、`Hunter2pass`、`github_pat_11`，发给提取模型的消息里也没有；库里能看到占位符。
  - 对照句照常入库、照常被检索。
  - Hermes v0.23.1（没有 `ctx.redact_text`）下插件不报错，走兰台自己的规则，上表 9 种里至少 8 种被替换。
- **工作量**：约 1 天（插件 + 规则 + 测试）。
- **注意**：`ctx.redact_text` 不加 `force=True` 时会受用户 `security.redact_secrets` 开关影响；发到外部模型的文本应当 `force=True`。这个方法在 v0.23.2、v0.23.3 源码的 `hermes_cli/plugins_context.py` 里都有，v0.23.1 没有；两版发布说明里都没写这一项。

### P1 SQLite 开 `secure_delete`，删掉的记忆别留在空闲页里（借鉴 LinEra）

- **做什么**：兰台打开 SQLite 连接时执行 `PRAGMA secure_delete=ON`；对已经删过东西的老库，给一个一次性的 `VACUUM` 脚本（先冷备）。
- **为什么**：
  - [Forget the Lineage（LinEra）](https://arxiv.org/abs/2611.18133)（11-13）第 8.2 节表 1：LoCoMo 128 场会话里埋 35 条“金丝雀”秘密再删除，只做逻辑删除时，35 条全部还能在 SQLite 空闲页的原始字节里找到；第 8.6 节：原版 mem0 按请求删了 20 条记忆，`history.db` 里还剩 6 个秘密。
  - 同一张表也说明，光清存储残留不够：模型从这句话写出的摘要和事实还在，35 条里有 22 条会通过“派生记忆”泄露。兰台对应的是从对话提取出的候选和事实，这部分要和级联删除一起看，本条只管存储残留。
- **实测**（Python 自带 SQLite 3.46.1，400 行里删 1 行带秘密的）：`secure_delete=0` 时删除后整个 .db 文件里仍能搜到原文；`secure_delete=1` 时搜不到。兰台代码里搜不到 `secure_delete`（`git grep` 为空）。
- **验收**：插一条带唯一标记的记忆→走兰台的删除接口删掉→`grep -a` 数据库文件（含 `-wal`）找不到标记。
- **工作量**：半天。代价是删除时多写零，写入量会上升，需要在你的服务器上看一下删除耗时。
- **未验证**：兰台生产库是否开了 WAL、删除走的是哪几张表，没在你的服务器上查。

### P2 冲突的两条记忆别急着判输赢，把冲突一起交给模型（借鉴 DebateGraph）

- **做什么**：两条冲突记忆分不出明显高下时（11-14 巡检里提过的“都超过 30 天、谁赢只看排序”那种），不再只注入赢的一条，而是两条都注入，标成“这两条互相矛盾”，附上各自时间和来源。
- **为什么**：[Surfacing Disagreement Beats Resolving It](https://arxiv.org/abs/2611.22461)（11-17）在多智能体辩论的共享记忆上逐个关掉 5 种文献里的机制，39 次实验里只有一种站得住：保留矛盾、把排好序的冲突集交出去。F1 从 0.116 升到 0.456（+0.340，95% CI [+0.267, +0.414]），投毒攻击成功从 122 条里 22 条降到 7 条，token 成本相同。论文还报告，写入信任闸门和来源特征在矛盾可见时反而更差。场景是多智能体辩论，不是个人助手，所以这条放在 P2。
- **和兰台的关系**：这刚好是 11-14 巡检留给你拍板的那个问题，给了第三种选择：分差太小时不判，直接把冲突摆出来。
- **验收**：造两条超过 30 天、分差小于阈值的冲突记忆，注入里两条都在且带“矛盾”标记；分差大时仍只注入赢的一条。
- **工作量**：约 1 天。

## 上周建议跟进

| 建议 | 状态 | 说明 |
|---|---|---|
| 11-14 P0 注入行标来源，外部指令型记忆不注入 | 没动 | `scripts/shell_hook.py` 的 `build_context` 里仍不带来源。本周 X 上关于 MemoryDojo 和记忆投毒的讨论很多，仍值得做，下周再提一次。 |
| 11-14 P1 问“原话”时附对话原句 | 没动 | 本周不重提。 |
| 11-14 P2 评测把弃答题算进召回 | 没动 | `scripts/lme_run.py:466` 仍是 `q["question_type"] == "abstention"`，该行最后一次改动是 10-26 的 `28961f29`。官方用 `question_id` 的 `_abs` 后缀标弃答。半天的事，下周再提一次。 |
| 11-07 P0 `on_session_delete`、P1 `post_llm_call` | 没动 | 上周已是最后一次提，不再提。 |
| 11-03 P2-4 注入文本去重 | 已采纳 | #59（正文只在【相关记忆】出现一次）已合并。 |

## 论文（按和兰台的相关度排序）

1. [Forget the Lineage: Forward-Secure, Lineage-Complete Cryptographic Erasure for LLM-Agent Memory](https://arxiv.org/abs/2611.18133)（11-13）——读了全文第 8.2、8.6 节。逻辑删除 35/35 留在原始字节；按对象加密后存储残留清零，但派生记忆仍让 22/35 泄露；按派生关系传递密钥的 LinEra 可以做到 0 泄露，代价是一次删除要擦 526 个对象（归因规则下 56 个）。对兰台：P1 的依据，级联删除也该沿“对话→候选→事实”这条线走全。
2. [HEAMS: Holistic Evaluation of Agent Memory Systems](https://arxiv.org/abs/2611.20144)（11-15）——读了摘要和评测维度部分。把记忆系统当成用户信息的保管者，评测加上不当披露、抗更正/删除、超期保留、投毒四类风险。对兰台：P0 的依据，也可以当作兰台安全评测的清单。
3. [Surfacing Disagreement Beats Resolving It](https://arxiv.org/abs/2611.22461)（11-17）——读了全文结果部分。P2 的依据，数字见上。
4. [RAG in a Trenchcoat: When Minimal Memory Is Enough for Agentic Systems](https://arxiv.org/abs/2611.22179)（11-17）——读了摘要和消融部分。一个刻意极简的记忆（链式观察笔记 + BM25 与余弦混合检索 + 按时间顺序呈现）在 LongMemEval-S、BEAM 上和此前最好成绩持平（bootstrap 置信区间内），在 EverMemBench 上同读者模型下比最好的记忆基线高 5.7 到 6.3 分；提取、取代、反思、图谱这些机制在他们测的基准上大多不划算。对兰台：兰台的混合检索方向是对的；在加新机制前，值得先用 LongMemEval 和“只做检索”的基线比一比。
5. [Multi-Agent Memory Inconsistency: Where Factual Conflicts Disappear in LLM Agents](https://arxiv.org/abs/2611.22245)（11-17，只读摘要）——要求委派后，两条冲突证据都被检索到的比例从 50.2% 升到 68.7%，但冲突常在汇报给主智能体时丢掉。对兰台：Hermes 子智能体走 #51 只读兰台，这条提醒冲突信息也要传回主会话。
6. [DriftGuard: Online Memory-Drift Detection via Causal Attribution for LLM Agents](https://arxiv.org/abs/2611.25583)（11-19，只读摘要）——在服务时估计每条检索到的记忆对“执行偏离”的因果贡献，指出困惑度和检索相关度不足以判断哪条记忆在作怪。对兰台：和 11-07 提过的“记录注入的记忆有没有被用上”是同一方向。
7. [HabitusMem: Belief Memory over Evolving User Preferences for LLM Agents](https://arxiv.org/abs/2611.25855)（11-19，只读摘要）——把记忆当作“助手对用户的信念”，记录偏好是否仍然成立、适用于哪些场合。对兰台：和 #30 的“已更正”标记思路一致。
8. [Forget Freely, Grep Later (GrepMem)](https://arxiv.org/abs/2611.25123)（11-18，只读摘要）——只追加的纯文本日志 + 需要时 grep，不建索引、不压缩。对兰台：和 11-14 P1“附原话”同一思路。
9. [How Much Should an Agent Remember? Deployment Lessons from Self-Distilled Memory](https://arxiv.org/abs/2611.26361)（11-19，只读摘要）——从自身轨迹挖“教训”再合并成“准则”，已在开源库 altk-evolve 里部署。
10. [MemMend: A Selective Revision Framework for Memory Question Answering](https://arxiv.org/abs/2611.25721)（11-19，只读摘要）——只把可能不完整的答案交给编辑器，用有源坐标的精确文本替换做修正，避免把已经对的答案改坏。

## 开源项目动态

- **Hermes Agent v0.23.2（11-16）、v0.23.3（11-20）**：两版都是滚动补丁版，发布说明称完整说明随 v0.24.0 发。源码里插件上下文有 `redact_text()`（P0 用得上）。兰台的真 Hermes 环境仍是 v0.23.1，升级后插件和 shell hook 的行为没重跑。
- **cognee v1.6.9、v1.6.10（11-18）**：仓库里有 Hermes Agent 集成（`cognee-integrations/integrations/hermes-agent`），自动采集默认脱敏，并按工具名和路径拒采 `.env*`、`*.pem`、ssh 和云凭据文件。和兰台是同一宿主下的同类插件，值得对照。
- mem0、graphiti、letta、MemOS、supermemory：本周 GitHub 上没有新的正式发布。

## 评测动态

- HEAMS（见论文 2）是本周唯一把隐私、删除、投毒放进同一套评测的新工作。
- X 上本周讨论集中在记忆投毒：MemoryDojo（上周已收）、DebateGraph（论文 3）、华为加拿大的 MPBench（帖子称“激进写入记忆的智能体被投毒成功 66.67%，谨慎的 34.25%”，论文本身未核实），以及 NIST 智能体威胁目录草案把记忆投毒列入（帖子称 12 月 18 日截止意见，未核实原文）。没有看到关于“密钥脱敏”的讨论。
- Hugging Face Daily Papers：本周和智能体记忆相关的只有 PReMind（2611.23808，个人记忆与视觉危险，9 票），和兰台关系不大。

## 未核实事项

- P0 实测用的是假模型，兰台的提取模型也是假的，真模型会不会在提取时自己把密钥丢掉，没评估；也没在 Windows 上跑。
- P0 只测了 v0.23.1 宿主；`ctx.redact_text` 在 v0.23.2/v0.23.3 上是看源码确认的，没在真 Hermes v0.23.3 上跑。
- P1 只在独立的 SQLite 小库上实测，没在兰台的库和删除接口上跑，也没量删除耗时。
- 论文 5 到 10 只读了摘要；MPBench 和 NIST 草案只见于 X 帖子。
- P2 的论文场景是多智能体辩论，迁移到个人记忆是推断。
