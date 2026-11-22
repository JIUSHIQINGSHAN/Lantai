# aiduMEI f0.9 与兰台 v0.69.1 公开证据核查

核查日期为 2026-11-20。两侧以 GitHub Release、标签对象解引用后的完整提交、固定提交的公开代码和评测留证为准。查重时找到的是较早的 f0.3++、f0.6 对照，目标文件尚未存在。[f0.3++ 对照][lt-old-f03] · [f0.6 对照][lt-old-f06]

| 项目 | 公开标签与版本 | 固定提交 |
|---|---|---|
| aiduMEI | `f0.9`，包/服务 `0.9.0`，2026-11-19 发布。先前的 `aiduMEM` 仓库 URL 现重定向至 `aiduMEI`。[发布][up-release] · [标签引用][up-ref] · [标签对象][up-tag] · [包元数据][up-pkg] | `003c3aa9c409e15d554c71d7224952eb23e70278` |
| 兰台 | `v0.69.1`，包 `0.69.1`，GitHub 发布于 `2026-11-19T16:00:36Z`。[发布][lt-release] · [标签引用][lt-ref] · [标签对象][lt-tag] · [包元数据][lt-pkg] | `18892e7ec5b49cbe9cd96bb70352c7432f744ccb` |

核查采用静态阅读，并复算公开的 LongMemEval 逐题指标。文中的实机与回归验收结果归属发布方或其公开 CI。本次未运行仓库程序，未访问真实记忆、宿主配置或生产服务。

## 内核与摄取

- **aiduMEI 自 f0.7 起默认使用 native SQLite。** `memory.db` 内有记忆正文、FTS5 trigram、向量 BLOB、历史、墓碑和图边。原生 `add` 在锁外计算可用的嵌入，在同一 SQLite 事务写入一组消息及其本库索引和关联。它接收兼容参数 `infer`，逐轮保留经写入策略处理的文本，会话精华由 `/session/distill` 另行生成。事实层的对外权威数据仍在 `facts.db`，服务还维护独立的 mutation journal。事务范围限于原生内核的本库写入。[f0.7 发布][up-f07] · [原生 DDL][up-schema] · [原生写入][up-add] · [事实层说明][up-facts] · [服务边界][up-limits]
- **原生内核按全文和向量两个排名做 RRF。** 全文用 FTS5 trigram 和 BM25 排序，短词可由 `LIKE` 补齐。向量使用过滤范围内的精确余弦计算，走 `sqlite-vec` 或 NumPy 批量回退。嵌入走 OpenAI 兼容接口，未配置、不可用或在 `local` 档时走词汇召回。`mem0`、`shadow` 仍可显式选择，依赖从可选项安装。未显式指定内核时，`local` 走 native，其他档位发现有旧 mem0 历史、尚无 `memory.db` 时留在 mem0 并提示迁移。[检索][up-search] · [嵌入与降级][up-embed] · [内核选择][up-selector] · [注册表][up-registry] · [可选依赖][up-extras]
- **兰台 v0.69.1 使用 SQLite/FTS5 与分开持久化的内嵌 ChromaDB。** 默认向量库是 `.chromadb` 下的 Chroma `PersistentClient`，主库默认 `remembrance.db`，无需独立向量服务。混合检索的候选来自 HNSW 向量、FTS 子串、BM25 三个结果集。向量和 BM25 做加权 RRF，再加 FTS 命中与时间衰减，施加 lane 等权重。“四路”包含衰减这一排序信号。向量出错或候选为空时可走词汇兜底。[存储设置][lt-settings] · [FTS DDL][lt-fts] · [Chroma 实现][lt-vector] · [候选与兜底][lt-retrieve] · [融合评分][lt-fusion]
- **两侧写入路径各有独立语义。** 兰台的对话摄取复用 `RawDocument → MemoryCandidate → proposal → MemoryItem` 的来源链，低置信或提取失败可进 `pending_review`。`/import/jsonl` 则提供逐字导入，按归一内容哈希去重、保留原时间戳，索引仍可计算嵌入。兰台的笔削将纠错、撤回、归档、删除分开。aiduMEI 的 native 删除在本库处理历史、向量、FTS 和关联，并为恢复保留受 TTL 约束的墓碑快照。[兰台对话摄取][lt-ingest] · [逐字导入][lt-import] · [笔削 ADR][lt-bixiao] · [原生删除][up-delete]

## 宿主与运行边界

| 宿主或接口 | 固定版本事实 |
|---|---|
| aiduMEI 既有宿主 | Claude Code 与 Codex CLI 通过宿主 hooks 处理召回、摄取与会话萃取，Hermes Agent 走 `MemoryProvider`。`f0.8++`（`0.8.3`）加入第四个一等宿主 DeepSeek Harness，包清单固定 DSH `0.2.2-rc.3`，带自动首步召回、已提交轮次的持久 outbox、空闲/会话结束萃取与可选 stdio MCP。DSH 自动路径默认空工作区白名单，按真实路径检查。发布方报告了 Windows 端到端与故障注入，DSH 指南将测试矩阵与预览范围单独说明。[接入清单][up-hosts] · [f0.8++ 发布][up-f08pp] · [DSH 清单][up-dsh-pkg] · [DSH 指南][up-dsh-doc] |
| aiduMEI f0.9 新宿主 | **OpenChamber 是第五个一等宿主**。清单要求 OpenChamber `>=2.6.1`，扩展契约 `apiVersion: 1`。OpenCode v2 插件负责召回，宿主 service 接收 `sessionEvents`。发布方标记 OpenChamber 2.7.2 / OpenCode 2.3.0 的 Windows 实机验收，Linux/macOS 属于其兼容性声明。仓内 OpenChamber 指南首段写成 `Fourth`，其后列了四个既有宿主，发布及变更记录均写第五。[f0.9 发布][up-release] · [变更记录][up-host-log] · [OpenChamber 清单][up-och-pkg] · [OpenChamber 指南][up-och-doc] |
| 兰台 v0.69.1 | Hermes Shell Hook 延续，固定标签内有两个独立 DSH 包。显式 MCP 归因适配器默认关闭、可按配置启用，在工具调用 `_meta` 附当前会话和请求 ID。归因是审计标签，服务端认证及读取范围仍由原权限控制。独立的 DSH 自动召回草案默认无操作，`enabled: true` 在副作用前抛错。远程 MCP 桥保留显式调用，转发前验证冲突裁决参数，另有 `list_memory_checkpoints`，旧 `latest_checkpoint` 参数兼容。[兰台发布][lt-release] · [变更记录][lt-log] · [归因实现][lt-attribution] · [适配器指南][lt-attribution-doc] · [自动召回入口][lt-disabled] · [远程桥工具][lt-bridge-conflict] · [检查点][lt-bridge-history] |

OpenChamber 的 `prompt` hook 启动 CoreMemory 与 `/search` 预取，并将用户 prompt 交给本地共享状态。`context` hook 将召回写入 `event.system`，`event.messages` 提供查询素材。服务收到 `turn-finished` 后配对 user/assistant 两段，先写本地待投队列，再以事件派生的幂等键调用 `/add`。服务启动与新合格事件都会触发补投。成功配对的轮次安排默认两分钟的 best-effort `/session/distill`。缺 prompt 或 reply 时直接确认该事件，`turn-failed` 也直接确认，这两条路径均跳过写队列和转发。仓内指南的 “injects … into the messages” 和 “stores the reply alone” 与这些代码分支不一致。指南还声称项目白名单与测试隔离，所核对的 OpenChamber 插件、管理器和服务没有显示项目白名单的执行路径，测试隔离声明未获独立验收。[插件][up-och-plugin] · [配置写入][up-och-manage] · [投递与萃取][up-och-spool] · [事件分支][up-och-events] · [失败事件用例][up-och-failed-test] · [指南][up-och-doc]

aiduMEI 的 DSH 自动召回在 `agent/pre-step` 等待内层 `next()`，检查进入态、首步及工作区条件，从决策消息取查询。兰台 ADR-0090 要求可信的最终准入、新排队输入归属和后端记录范围，并记录了首步混合输入、waterfall 外层可否决以及工作区白名单仅限定发起位置的依据。上游发布所列 Windows 验收场景侧重跨会话召回、写入、stdio 与故障恢复。Windows 文件同步的改动也有明确边界，mutation journal 只跳过目录 `fsync`，跨进程文件锁及完整运维脚本仍以 POSIX 为保证平台。[上游 pre-step 源码][up-dsh-hook] · [上游 DSH 发布][up-f08pp] · [兰台 ADR-0090][lt-adr] · [目录同步][up-win-sync] · [文件锁][up-win-lock] · [平台声明][up-limits]

## 评测与发布验收

- **aiduMEI 的公开内容包含评测管线和回归材料。** 旧 LoCoMo/LongMemEval 管线定义数据哈希和上游修订、JSONL、配置及 digest，并有 `--formal` 数据闸门。`RESULTS.md` 列 LongMemEval S/M、LoCoMo 及官方 judge 均未正式运行。固定代码把 LongMemEval 输出界定为检索诊断。历史 LoCoMo 试跑只有 2/10 样本、304 题、Sonnet 裁判，缺逐题原始记录、配置快照和当时的代码锚点，上游已撤回名次说法。这次核查未找到 f0.9 可引用的正式 LoCoMo/LongMemEval 成绩。[结果页][up-results] · [协议][up-protocol] · [留证/指标实现][up-run] · [试跑披露][up-posture]
- **日常回归与未完实验的口径。** 上游 README 描述维护者自用的真实使用回归 v3（五类记忆行为与 Claude Code 宿主回放），公开仓用确定性虚构替代集与相应基线。自用回归语料缺席公开树，公开数字适用于替代集。门禁脚本有两条基线命令，其中 native 臂显式钉内核，首臂由进程环境和默认选择决定。跨会话归并实验仍默认关闭，完整 20 题真实 A/B 尚未完成。[回归说明][up-quality] · [公开集生成器][up-synthetic] · [门禁][up-gate] · [实验状态][up-ab]
- **兰台有留档的历史 E1/E2、LongMemEval-S 和 MemProbe 评测。** E1/E2 记录摄取至注入的阶段行为和时间双视图。LongMemEval-S cleaned 采用预注册 25 题子集、会话级命中、全题分母包含 3 道 gate 跳过题，未做答案生成或 judge。MemProbe suite56 使用 `/import/jsonl` 逐字通道与同一 Qwen reader，56 题/224 探针中兰台为 141/224，完整上下文为 146/224。Naive RAG 有 65 道超时，报告以 159 道有效探针为分母。各实验的语料、摄取通道、reader 和指标应随结果引用，两项目缺少同协议横比。[自证协议][lt-selfproof-method] · [LongMemEval 修订记录][lt-lme-rev] · [MemProbe 协议和结果][lt-memprobe] · [MemProbe 留证][lt-memprobe-evidence]
- **兰台 LongMemEval 的 BM25 数字需按留证引用。** 自证报告开头把 BM25-only recall@5 写为 `0.84`，同文详表、修订说明和 `summary-rev1.json` 都为 `0.88`。本次直接聚合基线与 rev1 共六份 JSONL 的 `hit_at_2`、`hit_at_5` 和 `gate_skipped`，两轮的混合、BM25-only、vector-only 全题 recall@2 分别为 `21/25`、`20/25`、`19/25`，recall@5 分别为 `22/25`、`22/25`、`21/25`。rev1 三组覆盖率均为 `25/25`，召回未变。混合与 BM25-only 在该子集的 @5 并列。[摘要原句][lt-selfproof-summary] · [详表][lt-selfproof-table] · [修订汇总][lt-lme-summary] · [逐题原件目录][lt-lme-tree]
- **v0.69.1 的新增评测证据聚焦隔离。** 启动器验证凭据及评测目录与受保护目录不重叠，以 nonce、PID/亲缘和实际绑定端口确认本次服务就绪，仅回收本次启动的进程树。发布方记录 13/13 发版门禁、4281 passed / 6 skipped，公开 tag 工作流成功。另有隔离的 Linux amd64 容器冒烟工作流，arm64 只记录构建与摘要核查。aiduMEI 的 f0.9 Release 和仓内 CHANGELOG 分别报告 4786/4787 项 pytest 通过、11/15 个 Node 用例，固定树的两个 OpenChamber 测试文件合计有 11 个顶层 `test(...)`，本次未重跑。两边的这些发布验收使用各自测试集。[隔离配置][lt-eval-env] · [进程就绪与回收][lt-eval-process] · [兰台发布][lt-release] · [tag 工作流][lt-ci] · [容器冒烟][lt-smoke] · [上游发布][up-release] · [上游仓内计数][up-test-log] · [OpenChamber 测试目录][up-och-tests]

[lt-old-f03]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/research/upstream-f03-gap-and-plan-2026-10.md#L1-L8
[lt-old-f06]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/research/aidumei-f06-gap-analysis-2026-10.md#L1-L10
[up-release]: https://github.com/monkey2jack/aiduMEI/releases/tag/f0.9
[up-ref]: https://api.github.com/repos/monkey2jack/aiduMEI/git/ref/tags/f0.9
[up-tag]: https://api.github.com/repos/monkey2jack/aiduMEI/git/tags/8d39a00543316dbec63ce4cab113d4147dd101b0
[up-pkg]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/pyproject.toml#L5-L9
[lt-release]: https://github.com/JIUSHIQINGSHAN/Lantai/releases/tag/v0.69.1
[lt-ref]: https://api.github.com/repos/JIUSHIQINGSHAN/Lantai/git/ref/tags/v0.69.1
[lt-tag]: https://api.github.com/repos/JIUSHIQINGSHAN/Lantai/git/tags/5d78b0a8706402ac7e743440da2f51e8f3077140
[lt-pkg]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/pyproject.toml#L1-L29
[up-f07]: https://github.com/monkey2jack/aiduMEI/releases/tag/f0.7
[up-schema]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L53-L93
[up-add]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L486-L550
[up-facts]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L926-L945
[up-limits]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md#L187-L197
[up-search]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L633-L718
[up-embed]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L204-L233
[up-selector]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/__init__.py#L44-L99
[up-registry]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/registry.py#L28-L50
[up-extras]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/pyproject.toml#L31-L50
[lt-settings]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/settings.py#L565-L584
[lt-fts]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/storage/fts.py#L123-L131
[lt-vector]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/storage/vector_store.py#L28-L49
[lt-retrieve]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L525-L640
[lt-fusion]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L821-L844
[lt-ingest]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/ingestion/dialogue.py#L127-L154
[lt-import]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/import_service.py#L106-L147
[lt-bixiao]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/adr/0047-bixiao-fourway-record-lifecycle.md#L21-L41
[up-delete]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py#L405-L442
[up-hosts]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md#L151-L164
[up-f08pp]: https://github.com/monkey2jack/aiduMEI/releases/tag/f0.8%2B%2B
[up-dsh-pkg]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/dsh/package.json#L1-L24
[up-dsh-doc]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/dsh/README.md
[up-host-log]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/CHANGELOG.md#L7-L25
[up-och-pkg]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/package.json
[up-och-doc]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/README.md
[lt-log]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/CHANGELOG.md#L10-L35
[lt-attribution]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai/bridge.js#L7-L17
[lt-attribution-doc]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai/README.md#L1-L22
[lt-disabled]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai-recall/index.js#L1-L6
[lt-bridge-conflict]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/cli/mcp_bridge.py#L164-L174
[lt-bridge-history]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/cli/mcp_bridge.py#L239-L257
[up-och-plugin]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/plugin/index.js#L166-L258
[up-och-manage]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/manage.mjs#L71-L141
[up-och-spool]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/service/main.js#L102-L189
[up-och-events]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/service/main.js#L217-L233
[up-och-failed-test]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/test/service.test.mjs#L105-L115
[up-dsh-hook]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/dsh/index.mjs#L786-L803
[lt-adr]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/adr/0090-mainline-workspace-safety-contracts.md#L10-L31
[up-win-sync]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/mutation_journal.py#L142-L154
[up-win-lock]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/mutation_journal.py#L47-L83
[up-results]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/benchmarks/RESULTS.md#L1-L27
[up-protocol]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/benchmarks/PROTOCOL.md#L270-L315
[up-run]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/benchmarks/run.py#L722-L777
[up-posture]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/docs/BENCHMARKING-POSTURE.md#L8-L26
[up-quality]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md#L199-L201
[up-synthetic]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/benchmarks/build_public_regression.py#L1-L10
[up-gate]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/scripts/push_gate.sh#L106-L113
[up-ab]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md#L11-L17
[lt-selfproof-method]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/selfproof-report-2026-10.md#L17-L25
[lt-selfproof-summary]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/selfproof-report-2026-10.md#L7-L13
[lt-selfproof-table]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/selfproof-report-2026-10.md#L31-L44
[lt-lme-rev]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/longmemeval-2026-10/REVISION-2026-10-27.md#L30-L55
[lt-lme-summary]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/longmemeval-2026-10/summary-rev1.json
[lt-lme-tree]: https://github.com/JIUSHIQINGSHAN/Lantai/tree/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/longmemeval-2026-10
[lt-memprobe]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/memprobe-report-2026-10.md#L19-L52
[lt-memprobe-evidence]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/memprobe-report-2026-10.md#L112-L126
[lt-eval-env]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/scripts/eval_runtime.py#L9-L56
[lt-eval-process]: https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/scripts/memprobe_server_main.py#L30-L177
[lt-ci]: https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44556831032
[lt-smoke]: https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44560287903
[up-test-log]: https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/CHANGELOG.md#L3-L5
[up-och-tests]: https://github.com/monkey2jack/aiduMEI/tree/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/test
