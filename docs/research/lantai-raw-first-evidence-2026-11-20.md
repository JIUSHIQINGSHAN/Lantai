# 兰台“原文先存、之后提取”证据核查

核查日期：2026-11-20。固定发布基线为 `v0.69.1`、提交 `18892e7ec5b49cbe9cd96bb70352c7432f744ccb`。本笔记仅静态阅读 Git 追踪的源码与测试，没有运行测试、导入应用、读取环境配置或接触生产/用户数据。已核对现有干净发布检出确实位于该 SHA。本地远程跟踪主线 `origin/master` 指向 `eaa5d29a`；从发布到该引用，产品目录无改动，测试增量为进程年龄隔离。这不是实时远端状态声明。

## 1. 结论与建议的准确起点

兰台已有原始文档、逐字记忆、提取候选、演化提案、回滚快照与阶段事件，具备复用基础；**尚不能把现有接口等同于“先可靠提交完整原话，再独立可恢复地提取”。** [模型][model-raw]、[异步服务][async]、[对话写入顺序][dialogue-flow]与[逐字写入顺序][raw-write]支持以下判断。

1. `RawDocument` 是按内容复用的来源文档，不是逐轮、逐消息的事件账。`content_hash` 全表唯一；目前两个主体说同一段文本会命中同一份文档，重复的两次发言也不能只靠此行表示。[模型][model-raw] · [对话复用][dialogue-store]
2. 常规对话先尝试 LLM 提取，再创建原文及候选；`/add` 在部分去重分支可直接返回而不新建来源文档。原话存在不代表它是模型之前的可靠接收点。[对话顺序][dialogue-flow] · [直写与去重][add-direct] · [提取与去重][add-extract]
3. `verbatim` 是 `MemoryItem` 的一类，当前 `/add/raw` 的 embedding 在 SQL 持久化之前，且未在本函数捕获。其“零 LLM”应准确理解成不做文本提取/改写，**不是无模型外呼，也不是 embedding 故障仍必然原文入库**。[逐字构造][raw-build] · [逐字写入][raw-write] · [embedding 客户端][embed-client]
4. `/dialogue/async` 返回的 `task_id` 不代表原文已经持久化。任务注册表与待执行正文位于内存线程池，持久化幂等键和 `IngestEvent` 刻意不存正文。只读代码不能据此宣称重启后恢复原文任务。[异步服务][async] · [幂等契约][idem] · [摄取事件模型][model-ingest]
5. 第一阶段宜增加小型、按消息出现次数记录的原话接收层与持久提取作业，复用后续候选→提案→记忆链；不必迁移掉 Chroma，不宜简单把所有回合变为 `verbatim` 或打开全量逐字召回。

## 2. 当前入口的实际边界

| 入口 | 实际保存与处理 | 不可混称的能力 |
|---|---|---|
| `/dialogue` | 一段 `text`，可携 `session_id/turn`。白名单直构候选；闲聊可建原文及 rejected 候选；普通文本先 `extract_candidate`，再提交 `RawDocument + MemoryCandidate`。来源回合信息落候选和 provenance。[路由][dialogue-route] · [业务链][dialogue-flow] · [落库][dialogue-store] | 不是角色化 user/assistant/tool 消息接口；不是先持久原话再提取；拒绝成为记忆的内容也不一定没存原文。 |
| `/add` | 支持内容、元数据等；fastpath 或模型提取路径复用 `RawDocument`、创建候选；预筛的 merge/update 可先返回，已有文档仍可能新建候选。[直写][add-direct] · [提取][add-extract] | 不是每次调用都产生一份原话事件；文档去重不是提取任务幂等。 |
| `/add/raw` | 直入 `MemoryItem(memory_type='verbatim')`，同时建 Evidence、FTS、Chroma；内容归一化哈希用于幂等，正文保存输入字符串；默认不参加普通混合召回。[构造][raw-build] · [写入][raw-write] · [开关][raw-settings] | 不是纯档案表；会产生嵌入外呼；内容键会合并不同发言出现次数；并不自动安排之后的事实提取。 |
| `/import/jsonl` | [REST 路由][import-route]调用的是[服务层逐字导入][import-store]，逐行建立 `verbatim`，保留传入时间、可在 embedding 失败后继续 SQL/FTS；并有注入/密钥/lane 闸。[导入闸][import-gates] | 不要与另一个[历史对话导入器][session-import]混为一谈：后者只摄取 user、跳过 assistant，解析出的 session 在实际 `ingest_dialogue` 调用中未传递。 |
| `/session/distill` | 从指定 session 的 active `MemoryItem` 取至多 `DISTILL_MAX_SOURCE` 条，做会话摘要；LLM 不可用时选最长两条拼接并标 fallback；`store=true` 再通过 `add_memory` 建候选。[源收集][distill-read] · [摘要与落库][distill-write] | 不读取完整 `RawDocument` 逐轮原话，不是对“原文先存”队列的后台消费者；生成摘要成功不代表摘要已成为可召回记忆。 |

另外，[会话精华源查询][distill-read]对非 admin 只按 `session_id/status/allowed_lanes` 收窄，未在该函数对 user/tenant/agent 落等值谓词。不能把碰巧相同的 session 字符串当作租户隔离；给新原文层接这个服务之前须复核作用域，并用两主体同 session 的真实 SQLite 样例验证。这里描述查询缺少的谓词，不声称本次复现了现网越权。

特别是逐字导入的归属：该 REST 路由只传 `allowed_lanes`，不传 principal/user/tenant；构造函数默认归属为 NULL，去重 SELECT 也未加主体限制。普通 `/add/raw` 虽有主体传递，去重主要按 user/NULL 兼容口径，不应让这些旧口径成为新对话档案的隔离规范。[导入路由][import-route] · [逐字导入][import-store] · [逐字构造与去重][raw-build]、[raw-write]。这是固定代码的数据流判断，未验证任何现网跨用户读取或推断实际泄漏。

## 3. RawDocument 的身份、来源与重复问题

### 3.1 字段存在不等于每个写点都填写

模型已有 `tenant_id/user_id/agent_id/session_id` 列，但[对话文档构造][dialogue-store]未给这些列赋值，只给 `meta` 存 `user_id/source`；同一段文本命中原行后连这一份 meta 也复用。候选则填写用户、租户、agent、session。`/add` 的两条 RawDocument 构造同样只写来源/正文/metadata，候选四元组另行赋值。[模型][model-raw] · [直写][add-direct] · [提取][add-extract]

这意味着“候选带属主”不能推出“源文档具有独立且可信的属主”。原文归属须从认证主体/明确服务作用域落列，不能从任意 metadata、会话 ID 或内容 hash 反推。

### 3.2 内容相同、消息相同、网络重试不是一回事

[内容键][model-raw]是全局 unique，不含主体、session、role 或 turn。[重复内容测试][repeat-test]明确锁定：相同内容写两次，复用同一文档、建两个不同候选。它证实文档复用与候选独立是现行语义，不证明模型提取已经去重；并且没有覆盖不同主体或同一句话在不同时间出现的语义。

建议新层用服务端稳定 `message_id` 表示“这一条出现”，外部 `source_event_id` 只用于同一已认证范围内的幂等映射。`tenant/user/agent-or-trusted-scope/host_instance/session/turn/message` 分别有职责，不用内容 hash 代替消息身份。消息内容另算 hash 用于完整性和同 key 不同内容冲突检测；如未来节省存储做 blob 复用，逻辑消息、权限与删除仍独立，第一期无需物理去重。

### 3.3 现有来源链可复用，但不足以表达证据片段

候选有 `document_id`、provenance；提案保存 `candidate_id`、`evidence_ids=[document_id]` 并继承来源；应用新记忆时写 `source_ids`、候选归属、checkpoint 和 doc→memory supports 边。[候选模型][model-raw] · [提案生成][propose] · [应用][apply]

当前 provenance 保存 prompt 标识、配置中的模型名与提取时间，不是完整的 prompt 内容哈希、模型实际修订、原文精确版本和跨度；`MemoryItem.role` 是 observation/belief/rule 等认知角色，不能当作发言人角色。[provenance][prov] · [认知角色与消息模型][model-raw]

建议每次提取冻结输入消息 ID、版本/哈希及顺序，记录提取作业 ID、prompt 哈希/版本、模型配置和运行结局；候选到消息的支持关系显式保存 quote/offset，并校验 quote 是冻结原文的真实子串。数量、日期、否定、条件、说话人与时间均要作为质量要点。来源引用不得由 LLM 凭空生成。新表或关系的正式名称需另走项目 ADR/命名流程，本笔记只使用描述性短语。

## 4. 角色与会话粒度

[对话请求][dialogue-route]只有一个文本字段，不内建结构化消息角色；[历史对话导入][session-import]只消费 user 行；[Hermes 缓冲][hermes-buffer]也收 user_message，冲刷提交的还是这一段用户文本。这些代码不能证明当前保存了完整 user+assistant+tool 逐轮记录。

建议第一期只处理**明确获准保存的 user 文本和 assistant 最终可见回复**，按独立消息保存；不要等双方齐全才保存用户输入。缺失回复、取消、有部分已提交文本的轮次按宿主给出的真实状态标注，不能编造成已完成双向回合。工具调用、工具输出、系统提示、开发提示、模型隐藏推理、注入的历史记忆及子代理内部消息均不自动纳入第一期原话范围。

`session_id` 用于会话聚合，`turn_id` 用于用户轮次，`message_id` 用于发言身份；还需区分 host conversation 与压缩/恢复后的运行 session，宿主无相应标识时保持未知，不能靠时间猜。assistant 的断言只证明“助手说过”，不能直接升级为用户事实；用户对方案的“好/按这个来”需要关联相邻问题与方案，不能脱离上下文提取。工具证据以后按工具类型、执行成功状态、来源版本另行进入，而非把所有输出当权威。

原文采集应发生在可靠的已提交/最终准入宿主边界；DSH 的独立自动召回目前[代码拒绝启用][dsh-boundary]，不能因本提案而宣称已具备自动采集授权或解除该边界。此次用户表达的是方案偏好与文档需求，不是授权访问历史私人对话。

## 5. 异步、索引与重试

### 5.1 当前状态

`submit_async_dialogue` 先抢幂等键、创建无正文事件，再把正文放入 `_EXECUTOR.submit`；`_TASKS` 只有进程内字典。claim/done 回执可持久化标量，但无法据此重建丢失的全文任务。幂等层 DB 异常目前有 fail-open 分支。自动键只保护 600 秒租约，显式 done 回执保留配置的 TTL。[异步实现][async] · [幂等契约与白名单][idem]

常规 `RawDocument` 不进入 `memory_fts`；FTS 是 `MemoryItem` 的派生索引，`sync_fts` 用调用方 SQL 事务。Chroma 是独立 `PersistentClient`；`/add/raw` 在 SQL commit 前写向量，删除链在 SQL commit 后删向量，不能称为跨 SQLite/Chroma 原子事务。[FTS][fts] · [逐字写入][raw-write] · [硬删除][hard-delete]

### 5.2 推荐接收契约

```text
认证 + 来源范围 + 文本/大小/隐私准入
  → 同一个 SQLite 短事务：消息出现记录 + 提取作业
  → 提交成功才回 raw_saved(message_id, extraction_state=pending)
  → worker 认领冻结输入，释放数据库事务后调用模型
  → 短事务重验 source 状态/版本与作业 token
  → 候选 + 显式来源关系 + 作业结局
  → 沿用闸门/提案/应用
```

新接收层不等待 embedding 或 LLM，不让远端模型故障变成原文丢失；本地数据库写失败时如实拒收，不返回“已保存”，不能复用幂等 fail-open 作为可靠接收承诺。一个单进程下的 SQLite 持久作业即可先落地，无需 Kafka、Redis 或多 worker 扩容。作业须有有限重试、租约/随机所有权 token、错误类别、下次重试时间、最大积压与重启扫描。输入/作业在同事务，避免“原文有但没任务”与“任务有但原文没提交”。

提取作业可以按同范围相邻回合组成有界窗口；每个窗口显式冻结消息集合、序号、水位及提取版本。它不是整场会话无限追加后的单一布尔 `extracted`。后来的补充、改口、纠错产生新窗口；模型配置升级也不得自动重审所有历史被拒/撤回内容。

可先为原文建受范围过滤的 SQLite FTS，embedding 作为可重试派生任务延后。索引任务携带源版本；每次 upsert/delete 和输出重读都重验权威 SQL 状态，旧版本重试不能把删除或撤回的数据重新放回可见索引。停服、崩溃、提交后断线和恢复都需实测，不能从“有任务表”推导 exactly-once 或断电零损失。

## 6. 原文与提取结果怎样一起检索

[当前主检索][recall]在各通道并集后重读 active `MemoryItem` 并做归属过滤，再按 memory_type/lanes/domain 等过滤；[默认设置][raw-settings]将 `verbatim` 排除，专用[原文检索路由][verbatim-route]可显式查询。原始 `RawDocument` 并非这个混合搜索的数据源。

建议保持三个读法，先不要把全部原文平铺塞进普通召回：

- **日常回答**：先查已生效知识；需要核对时按来源关系补一小段原话和邻接上下文。
- **明确回忆原话/提取尚未完成**：在同一授权范围内查原文，结果带 role、时间、提取状态和“尚未验证/历史陈述”标签；有总字符/片段数预算。可以做到原文一提交即有关键词兜底，不必等 embedding。
- **历史追溯**：按消息顺序/真实事件时间回看；当前事实已更正，旧原话仍可能是历史证据，但不能作为当前值并列默认展示。

对原文及其衍生知识做证据分组，通常返回一条结论加引用，不重复注入同一事实的 raw、summary、MemoryItem 三份；不要对不同人的同文原话做跨范围融合。读原文权限与读衍生结论权限分别重验；单独的“可保存”“可向模型发送”“可检索”“可提取”策略应可区分。检索命中只说明原文存在，不证明其陈述为真。

## 7. 删除、撤回和保留期：新增原文层前必须补齐

现有 [文档删除][document-delete]从文档出边找目标记忆，先校验目标归属，再删除文档、分块、候选和源出边并提交；之后逐个调用记忆删除。判断记忆是否还有其他文档来源用 `source_memory_id.startswith('doc_')`。这覆盖特定边结构，但不等于基于全部版本和多跳衍生来源的事务化净除；中途失败可能返回已删文档/部分记忆的结果。

[记忆硬删][hard-delete]和[终端硬删][terminal-delete]清理行、FTS、原子事实/实体及两端关系，之后删 Chroma；这些删除清单没有清除 `RawDocument`、`MemoryCandidate/Proposal`、Evidence 正文和所有 checkpoint 的 before/after。相反[模型][model-history]与[应用][apply]显示这些位置可持有内容副本。不能据一次删除就宣称全库、索引、备份、宿主日志和远端模型副本彻底擦除；本次未测试各副本读取面的实际泄漏。

[撤回][record-ops]会保留 MemoryItem 正文，将状态设 retracted、尝试移除索引并撤回关联原子事实；[归档][record-ops]保留索引，靠 SQL status 排除；[遗忘][forgetting]只是衰减与归档 MemoryItem，不是清理原话字节。已见[观测 TTL][retention]是 IngestEvent/RetrievalEvent/LLMCallEvent 等表的保留政策，[默认 30 天][retention-settings]不能当作 RawDocument 或 verbatim 的原文保留期。

建议从首版就明确：

| 操作 | 推荐语义（计划，不是现有保证） |
|---|---|
| 纠错 | 原消息正文按接收版本不可变；追加更正/新版本及 supersedes 关系，更新当前知识，历史查询仍可解释。不可变不妨碍合法删除/清除内容。 |
| 来源撤回 | 先停止该源的检索、提取、待审与迟到提交；依赖该源的结论进入失效或重新核验，不能自动复活。多来源不能仅凭“还剩一条边”保留，必须确认剩余来源独立支持该结论。 |
| 忘记/删除 | 根据作用域形成可审计删除任务，覆盖原消息、分块/索引、待执行/待审产物、受影响知识及正文副本；元数据回执只含最小标量。大批跨存储清理明确 pending/partial/complete 与重试，不假造单事务成功。 |
| 容量性原话到期 | 与事实撤回区分；按显式保留政策处理仍被引用原文和知识去留。不能自动因“已经提取”就删唯一证据，也不能把模型摘要当原文等价物。 |
| 备份恢复 | 保留期限、删除传播和恢复后抹除规则另有约定；恢复旧备份不可把已删除消息重新排队提取。第一期可保留有界删除标记/恢复对账方案，不宣称跨备份即时擦除。 |

原话存储比提炼结果包含更多个人信息和密钥。现有密钥守卫默认 shadow；enforce 在候选阶段转待审，往往已经过 LLM 提取，不能当作模型外呼前的隐私门。[顺序][dialogue-flow] · [密钥守卫][secret]。新的 raw-first 接收要先区分准入与后续外发资格；不以“原样保存”为由默认长期保存全部系统消息、工具输出和凭据，保存受控原文时与普通检索隔离，不改写原文来假称仍是逐字。

## 8. 最小实施阶段（供建议白皮书采用）

| 阶段 | 范围 | 退出条件 |
|---|---|---|
| Phase 0：立契约与基线 | 固定消息身份、作用域、可保存消息类型、三类时间（发生/收到/提取）、准入/外发/保留/删除语义；选一个已有宿主或显式 API，合成数据试验。 | 构造同文不同轮/主体、缺回复/取消、改口和“不要记住”的完整样例；确定源 ID 不伪装权限。暂不读取历史用户对话。 |
| Phase 1：可靠原话接收 | user+assistant 最终可见文本；独立出现记录、同事务作业、恢复扫描、查询状态；不依赖模型；保留原 `RawDocument`/verbatim 对外契约，新数据不沿用 NULL 兼容。 | 提交后停止进程/重启可恢复；响应丢失后同 key 重试不丢/不重复；同 key 不同文拒绝；同文不同消息均保留；跨范围不可读写。 |
| Phase 2：延迟提取 | 有界消息窗口、版本化提取、真实引文和来源关联；复用原候选→提案→应用，低置信度待审；删除竞态防护从本阶段一开始存在。 | 新旧 extractor 不覆盖原文；失败可重试；来源撤回或修改时迟到结果不能落库；角色混淆与日期/否定/数量的合成样例通过；重跑不复活 rejected。 |
| Phase 3：受控原话检索与运营 | 先 SQL/FTS 兜底，再可选向量；知识与原文证据分组；显示 backlog、原话保存/提取/应用/索引/召回等独立阶段。 | 原文保存后能独立查到；源与衍生不重复塞上下文；删除/恢复对账、保留与积压上限工作。做同模型同语料的有无原文补充 A/B，再决定是否调整默认召回。 |

推荐最小持久抽象是“消息出现记录 + 提取作业/尝试 + 候选/知识对消息的证据关系”。实际可用现有表做投影和适配，但别让 `RawDocument.content_hash`、可过期 `IngestEvent` 或内存 `_TASKS` 同时扮演消息身份、存储真源和可靠队列。无需增加正式中文功能名、换数据库、永久保存所有工具日志或一次实现全事件溯源。

## 9. 测试能证明什么、还应补什么

本次只阅读测试源码，**没有声称下列用例在本轮通过**。

| 现有证据 | 可说明 | 不能说明 |
|---|---|---|
| [对话测试][dialogue-tests] | 真实 SQLite 下候选、原文与来源字段的路径，闲聊可保留 RawDocument；外部 chat_json 可替身。 | 所有角色/完整回合、原文先于模型提交、跨用户文档隔离和崩溃恢复。 |
| [相同文本复用测试][repeat-test] | 当前重复内容复用文档但创建新候选的语义。 | 用户重试去重与合法重复发言都被正确区分。 |
| [原文直存测试][raw-tests] | SQLite/FTS 与内容去重真实执行，提取器不执行；用 mock 替换 embedding/向量。 | embedding 故障照样可入库、无网络、真实 Chroma 的一致性或重启持久性。 |
| [异步服务测试][async-tests] | thread/task 状态和 REST/MCP 包装；其内部 `ingest_dialogue` 被整体 mock。 | 端到端真实提取/入库和进程重启恢复；不得用它单独满足新核心链冒烟。 |
| [会话精华测试][distill-tests] | active MemoryItem 集合提炼、fallback 与 `store` 建候选等路径。 | 完整原始对话的分角色萃取、输入消息水位、每轮精华去重。 |
| [文档级联测试][cascade-tests]、[越权测试][cascade-owner-tests]、[两端边清理测试][edge-tests] | 独占来源删目标、多来源保留、越权前置中止、特定 SQL 回滚/边清理。 | 全部原话衍生多跳重算、checkpoint/Evidence 净除、Chroma 与 SQL 同事务、恢复旧备份不复活。 |
| [回填测试][reextract-tests] | 真 file-backed SQLite；已有任何状态候选不再创建，阻断标记保留；dry-run 不提取。 | 一个持久作业队列，按提取版本重跑，或对话消息级提取与跨范围授权。 |

需要补的“不 mock 核心逻辑”smoke 至少包括：

1. 真临时文件 SQLite，调用真实接收层，断开模型路径仍保存两条相同内容但不同消息 ID 的发言；同消息重复重放单行，冲突载荷拒收。
2. 真关闭/重开数据库或独立受控子进程，验证已提交原文/作业保留、接管 token 不被旧 worker 结算、提交后回执丢失不双提取。
3. 用确定性外部模型服务替身、内部提取/校验/候选/提案/SQL 全真运行，引用必须来自输入片段；user/assistant 不互相冒充来源。
4. 两个 scope 共用相同文本/外部 session ID，接收、搜索、提取、删除各自独立；缺主体新数据不得进入旧 NULL 共享路径。
5. 在模型执行窗口撤回/删除源，迟到候选或已待审提案无法 apply；多来源保留需逐条有效支持，不能仅数边。
6. 真临时 Chroma + SQLite 故障边界模拟，验证索引失败回执、删除重试、旧索引任务不能复活已删原文，普通与历史读视图不泄漏撤回内容。
7. 源文件含超过 6000 字符且关键否定/数字在尾部：分窗提取覆盖与明确截断均可解释；不把[现有 `content[:6000]`][extractor]当作全文处理。
8. 清理/恢复检查正文复制位置、日志中无正文、快照历史不残留可见秘密；验证保留截止前后和已删任务恢复。备份与真实宿主验收另列，不由单测数字代替。

## 来源索引（固定发布提交）

[model-raw]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/models/tables.py#L1-L205
[model-history]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/models/tables.py#L425-L476
[model-ingest]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/models/tables.py#L691-L715
[dialogue-route]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_dialogue.py#L16-L107
[dialogue-flow]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/ingestion/dialogue.py#L127-L249
[dialogue-store]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/ingestion/dialogue.py#L274-L364
[add-direct]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/memory_service.py#L460-L546
[add-extract]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/memory_service.py#L549-L648
[raw-build]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/memory_service.py#L865-L914
[raw-write]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/memory_service.py#L950-L1088
[raw-settings]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/settings.py#L124-L129
[embed-client]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/llm/client.py#L128-L255
[import-route]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_import.py#L12-L18
[import-store]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/import_service.py#L106-L151
[import-gates]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/import_service.py#L154-L223
[session-import]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/ingestion/import_service.py#L57-L164
[distill-read]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/distill_service.py#L84-L115
[distill-write]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/distill_service.py#L129-L204
[async]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/async_ingest_service.py#L23-L205
[idem]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/idempotency.py#L1-L120
[propose]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/evolution/proposer.py#L202-L235
[apply]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/evolution/promoter.py#L632-L763
[prov]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/provenance.py#L1-L35
[hermes-buffer]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/hermes-plugin/lantai-hook/__init__.py#L574-L669
[dsh-boundary]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai-recall/README.md#L1-L17
[fts]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/storage/fts.py#L126-L175
[recall]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L637-L710
[verbatim-route]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_obsidian.py#L50-L70
[document-delete]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/source_service.py#L140-L234
[hard-delete]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/evolution/promoter.py#L948-L1004
[terminal-delete]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_terminal.py#L339-L414
[record-ops]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/record_ops_service.py#L199-L445
[forgetting]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/memory/forgetting.py#L99-L199
[retention]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/observability/retention.py#L40-L190
[retention-settings]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/settings.py#L367-L381
[secret]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/security/secret_guard.py#L1-L61
[extractor]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/parsing/extractor.py#L8-L36
[dialogue-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_dialogue_ingest.py#L23-L240
[repeat-test]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_fastpath_dup_content_hash.py#L77-L126
[raw-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_raw_memory.py#L20-L150
[async-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_async_ingest.py#L23-L105
[distill-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_distill.py#L90-L184
[cascade-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_document_cascade.py#L47-L167
[cascade-owner-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_document_cascade_ownership.py#L161-L294
[edge-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_edge_cascade_hard_delete.py#L106-L230
[reextract-tests]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/tests/test_reextract_write_gate.py#L100-L221
