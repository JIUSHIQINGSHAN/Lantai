# aiduMEI 与兰台记忆技术白皮书

**2026-11-20 · 上游项目现状、两条架构路线与兰台演进建议**  
**核查基线：**上游 aiduMEI `f0.9`（`003c3aa9`）；兰台 `v0.69.1`（`18892e7e`）；另核查兰台公开主线 `eaa5d29a`。日期与时间除另注外均为北京时间。

## 摘要

兰台早期借鉴了 aiduMEM 的相关性闸门、批量提取、衰减与时间感知思路，随后各自演进。**上游现名 aiduMEI，已经不再以 mem0/Qdrant 为默认底座。** 它把原话及其检索索引收进原生 SQLite 记忆内核，优先打通宿主的召回、写入、萃取三条线；`f0.9` 新增 OpenChamber 接入。**兰台则围绕「一条信息何时可以成为可信的长期记忆」建设管线**：候选准入、提案变更、历史查询、撤回归档和阶段监控是其重要组成部分。[兰台移植记录](<aidumem-port-results.md#L9-L51>) · [上游 f0.7 Release](https://github.com/monkey2jack/aiduMEI/releases/tag/f0.7) · [上游 f0.9 Release](https://github.com/monkey2jack/aiduMEI/releases/tag/f0.9) · [兰台 v0.69.1 Release](https://github.com/JIUSHIQINGSHAN/Lantai/releases/tag/v0.69.1)

两边的取舍不同。原话优先能降低模型提取失真和漏记风险，但检索与读答环节要辨别旧值、说话人和噪声；提炼与审议能控制进入知识库的内容，但必须测清每个阶段的漏损。**建议兰台先借鉴「新会话真能召回、写入失败可见、重启后可对账」的接入验收方法，不急着更换存储，也不为追齐宿主数量而开启尚未验收的自动外发。**

## 1. 版本与证据边界

| 对象 | 查到的状态 | 不能由此推出的结论 |
|---|---|---|
| 上游 aiduMEI | 原地址 aiduMEM 已重定向至新仓名。GitHub 最新公开 Release 为 [`f0.9`](https://api.github.com/repos/monkey2jack/aiduMEI/releases/latest)，代码包版本 `0.9.0`，发布于 11 月 19 日 15:26:40；[标签解析](https://api.github.com/repos/monkey2jack/aiduMEI/git/tags/8d39a00543316dbec63ce4cab113d4147dd101b0)指向 `003c3aa9`。该 Release 无额外上传资产，可用 GitHub 源码归档；源码提供容器部署说明。 | 不代表所有适配都在任一宿主版本上验过，也不代表真实部署状态。 |
| 上游公开主线 | 核查时 [`main` 为 `f12eb12c`](https://github.com/monkey2jack/aiduMEI/commit/f12eb12c8fe77c4523b749edd05e5963b09e8124)，较 `f0.9` 多一个覆盖 OpenChamber 接入说明的文档提交。 | 本文仍用发布提交的实现，而非用更新的说明代替源码。 |
| 兰台公开发布 | 最新公开 Release 为 [`v0.69.1`](https://api.github.com/repos/JIUSHIQINGSHAN/Lantai/releases/latest)，[标签解析](https://api.github.com/repos/JIUSHIQINGSHAN/Lantai/git/tags/5d78b0a8706402ac7e743440da2f51e8f3077140)指向 `18892e7e`。GitHub 记录发布于 11 月 20 日 00:00:36（UTC 11 月 19 日，发布说明和变更日志按 11 月 19 日记载）。Release 有 wheel、校验和文件，并记录 GHCR 镜像。 | 产物已发布不等于已经部署、迁移或启用了某项能力。 |
| 兰台公开主线 | 核查时 [`master` 为 `eaa5d29a`](https://github.com/JIUSHIQINGSHAN/Lantai/commit/eaa5d29ac5b7bcdedce7ee46eea9c2ebeaa47498)，在发版提交之上增加研究文档与测试隔离，当前 [Tests 流水线](https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44613264115)成功；不是另一个发布版。 | 合入研究建议不等于功能已经实现。 |
| 兰台当前本地工作区 | 初查为 detached HEAD `ff0166b9`，含既有未提交改动，根目录的 [包元数据](<../pyproject.toml#L1-L4>)仍写 `0.68.0`。本次另用已存在的干净发布检出与公开标签核对，不切换工作区。 | 不能用旧工作区 README、依赖范围或本地候选验证记录代替公开版口径。 |

**证据分三类：**「源码／发布可核对」指已看到固定提交的实现或 GitHub 记录；「项目报告」指维护者给出的数字和环境，本次未重跑；「建议／未核实」不计作功能与业绩。文中技术引用以固定提交为主；旧版 [兰台白皮书](<WHITEPAPER.md#L1-L7>)是 `v0.15.2` 历史快照，不覆盖、不复用其中的测试规模或性能承诺。本文未访问生产服务、真实记忆、凭据或宿主配置。

## 2. 上游现状：aiduMEI f0.9

### 2.1 写入、检索与存储

```text
宿主逐轮事件 → 写入策略 → 原话 → 原生记忆库（正文 / FTS5 / 向量 / 历史 / 图边）
会话结束或空闲 → 萃取 → 单独保存精华，不改写原话
查询 → 全文 + 可用的向量 → 排名融合 → 有界召回 → 宿主注入
删除 → 清理正文与关联数据、保留有期限的墓碑；备份另按整个数据目录管理
```

- **默认采用原生 SQLite 内核。** 自 `f0.7` 起，mem0 成为旧库兼容、迁移与回滚用的可选组件；未迁移的旧部署可能仍用 mem0。原生内核在 `memory.db` 中维护记忆正文、FTS5 trigram、向量、历史和图边等，相关内核写入共享 SQLite 事务。**「单库」描述的是记忆内核，不是整套服务只用一个文件**：例如事实层仍另有 `facts.db`，运维日志与其他辅助数据也有各自边界。[f0.7 发布记录](https://github.com/monkey2jack/aiduMEI/releases/tag/f0.7) · [原生内核源码](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py)
- **逐轮原话与会话萃取分开。** 原生 `add` 不逐轮调用 LLM 改写消息；原话经写入策略处理后保存，会话精华另存，不替换原话。向量嵌入与 LLM 改写是两件事：配了模型端点时仍可能调用 embedding。检索内核融合 FTS5/BM25 与向量余弦的排名（RRF），缺向量时保留全文路径；本地档不等价于内置一套离线语义模型。[原生内核实现](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py) · [上游部署与挡位说明](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md)
- **删除正文与可恢复快照有不同边界。** 源码对正文、索引与关联记录做清理，删除前可留受期限控制的墓碑；文档默认 30 天保留快照正文。备份、墓碑和宿主日志是独立的数据面，不因主库删除而自动擦除。上游将部署定位为单主自托管，按身份收窄不等于互不信任的多租户隔离。[上游原生内核](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/memory_kernel/impl_native.py) · [备份与安全说明](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md)

### 2.2 宿主接入：从有接口到三条链路能工作

| 宿主 | f0.9 源码与官方资料可证范围 | 验收边界 |
|---|---|---|
| Claude Code、Codex CLI、Hermes | 有接入安装器和相应适配；上游文档给出安装后写入合成暗号、新会话召回、清理的验证路径。 | 安装器识别或健康接口返回成功，不能代替带宿主版本的全链复验。[安装器](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/scripts/connect_host.py) |
| DeepSeek Harness（DSH） | 上游提供适配 `0.2.2-rc.3` 的预览包：自动召回、已提交轮次摄取、空闲萃取及可选 stdio MCP；本地队列与幂等回执支持重试和重启恢复。 | 工作区须配置；队列满、永久失败、未重开的会话和断电持久化均有文档限制。真宿主隔离测试可用固定模型，不能证明线上模型表现或其他 DSH 版本兼容。[DSH 预览与验证说明](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/dsh/README.md) |
| OpenChamber | [`f0.9` 发布说明](https://github.com/monkey2jack/aiduMEI/releases/tag/f0.9)列为第五个原生适配宿主。插件在 prompt 阶段预取、context 阶段注入；事件服务只在用户提示和最终回复齐全时将 `turn-finished` 入队，随后转发并安排空闲萃取。 | 缺任一侧文本和 `turn-failed` 都会确认收件但不写入；这一点以[服务源码](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/service/main.js#L217-L233)为准，不采用指南称可只写回复的描述。指南列出的实测组合为 OpenChamber 2.7.2 + OpenCode 2.3.0。[插件实现](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/plugin/index.js#L192-L253) · [版本说明](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/README.md) |

真正可借鉴的不是宿主数量，而是把**服务存活、宿主配置正确、本轮已落库、下一会话实际收到**分开验证。上游 `host_wiring` 检查最近写入，适合发现「读线有、写线断」；兰台可借鉴验收方法，但不能拿后台任务写入当作用户会话写入的证明。[上游写线状态实现](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/ducky/host_wiring.py) · [兰台摄取阶段契约](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/adr/0088-ingest-attribution-and-stage-monitor.md#L11-L27)

## 3. 兰台现状：从「存下来」到「可治理地使用」

```text
REST / MCP / 宿主 Hook
  写：来源归因 → 白名单提取 / 模型提取 / 可选批量缓冲 → 候选 → 闸门 → 提案与记忆
  读：相关性预检 → 向量 / BM25 / FTS5 → 融合、时效与归属过滤 → 结果、证据、宿主回执
                          ↕
  SQLite 正文与 FTS + 内嵌 Chroma 向量；后台衰减归档、变更纠错、阶段与运行监控
```

**写入与溯源。** 兰台不是「每次 `/add` 都立即产生一条生效记忆」：规则覆盖的句式可绕过 LLM 构造候选，其他输入可用模型提取；潮波批量缓冲是可选项，默认关闭。不能直接放行的部分候选进入待审，不把收到输入、形成候选、批准提案与正式落库记成同一个成功。`v0.69` 引入零正文的摄取事件，分开保存历史来源、当前调用归因与读取范围；归因字段不是身份凭据，无从证明自然人就是消息作者。[写入源码](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/services/memory_service.py#L311-L456) · [默认设置](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/settings.py#L239-L254) · [阶段监控契约](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/adr/0088-ingest-attribution-and-stage-monitor.md#L11-L27)

**检索与时间。** 发布版默认主路径把向量和 BM25 排名做加权 RRF，再叠加 FTS 命中与衰减等信号，精排是可选的；向量为空或失败可走关键词兜底。时效是排序和过滤信号，而不是单独一个索引。`/search` 支持显式历史时间参数并可返回 trace；普通查询与历史视图不能按同一规则排除旧值。README 的简化加权公式不能替代现行实现。[候选与降级](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L525-L629) · [融合和精排](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L823-L925) · [搜索接口](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_search.py#L14-L108)

**遗忘与变更。** 兰台的指数衰减受分轨（lane）、重要度、使用信号和豁免影响；自动归档不物理删。另有显式纠错、撤回、归档与删除四种语义，适用范围不同；不能从「已从普通召回移除」推导「已从备份和宿主所有副本擦除」。正文/FTS 与 Chroma 不在同一事务，例如删除流程先提交 SQL 再删向量，迁移和恢复要检查两侧一致性。[遗忘源码](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/memory/forgetting.py#L23-L108) · [生命周期 ADR](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/adr/0047-bixiao-fourway-record-lifecycle.md) · [删除实现](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/evolution/promoter.py#L978-L1004)

### 3.1 兰台的接入与部署边界

| 能力 | `v0.69.1` 状态与建议 |
|---|---|
| Hermes、Claude Code、Codex CLI | 有仓库适配。Hermes 有插件及 Hook 协议；Claude Code、Codex CLI 在矩阵中主要是命令 Hook 的读取与注入，不能据此说都自动写入和萃取。Cursor 在该版文档只列静态规则降级，不计作运行时召回。[宿主协议](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/host-hook-protocol.md#L121-L157) |
| `lantai-mcp`、`lantai-mcp-bridge` | 本地 stdio 服务默认暴露 6 个核心工具，显式 `full` 才扩面；远程 stdio 客户端有独立白名单，通过 HTTPS 接 REST（只有显式的字面环回开发例外）。两者工具集和认证承载不同。[本地工具表](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/cli/mcp.py#L4127-L4179) · [远程桥契约](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/mcp-bridge.md#L1-L27) |
| DSH 逐调用归因 | 独立可选适配器，默认禁用。它为显式 MCP 调用附会话/请求标签，需匹配桥与后端；不自动采集每轮对话，不扩大读取权限。确认链路依赖时，不应把这套兼容桥与上面的打包 HTTPS 桥当成同一个实现。[归因适配器](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai/README.md#L18-L54) |
| DSH 自动召回 | **暂停启用。** 独立研究包缺省无操作，显式启用也会在发请求前拒绝。等待可信的宿主最终准入、输入来源与服务端数据范围；停用回归不算自动召回通过。[研究包状态](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/integrations/dsh-lantai-recall/README.md#L1-L17) |
| 控制台、存储与安全 | 宿主外还可用悬镜控制台、司天监控和 Prometheus 出口。按单进程自托管理解；应用默认回环绑定，非回环须配置 API Key，远程优先最小权限与 TLS。嵌入式数据库本地部署不等同模型请求不出网，也不是企业多租户或高可用承诺。容器锁定运行依赖，内嵌 Chroma 在 `v0.69` 更新至 1.5.9，旧库升级应按冷副本导出、独立新目录导入验证，不原地打开旧目录。[认证源码](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/core/auth.py#L45-L80) · [监控路由](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/api/routes_monitor.py#L31-L78) · [容器构建](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/Dockerfile#L1-L30) · [迁移指南](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/chroma-migration.md#L1-L34) |

**部署取舍。** 若主要需求是几个编码宿主共享原话、断线后能补写，上游的接入与队列更值得先试；若主要需求是跟踪「哪条规则何时生效、由谁裁决、如何撤回」，兰台的候选和历史读视图更贴合。两者都应实测自己的数据边界：上游 OpenChamber 的本地任务和回执文件可能留有原话，服务诊断还会输出提示前缀；兰台的摄取事件虽无正文，检索事件在相应路径可以保存查询文本。**分别检查主库、队列、回执、日志和备份，不把其中一条路径的承诺套到其他路径。**[上游队列与日志实现](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/integrations/openchamber/service/main.js) · [兰台检索日志](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/observability/retrieval_log.py#L56-L112)

## 4. 公开质量证据：能下什么结论

| 证据 | 结论 | 不能证明 |
|---|---|---|
| 上游 `f0.9` 发行方记录 | Release 报告 OpenChamber 适配 `11` 个 Node 用例、pytest `4786 passed`、守卫 `26/26`；同标签[变更记录](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/CHANGELOG.md#L3-L5)给出不同计数（`15` 和 `4787`），未证实是否同一运行。其真实使用回归集不随仓分发，公开仓有同结构合成集。 | 本次未重跑，不将来源不同的计数合并，也不拿两项目的用例数排名。[发布说明](https://github.com/monkey2jack/aiduMEI/releases/tag/f0.9) · [测试口径](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/docs/TESTING.md) |
| 兰台 `v0.69.1` 产物与测试 | Release 记载完整发版门禁 `13/13`、`4281 passed / 6 skipped`，含覆盖率运行、宿主数据目录绊线与遗忘质量门禁。公开核查了同一发布提交的 [主线 CI](https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44552467152) 和 [标签测试与构建](https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44556831032)，也核查了独立验证分支对固定镜像的 [冒烟运行](https://github.com/JIUSHIQINGSHAN/Lantai/actions/runs/44560287903)，三者均成功。 | 通过数为发版记录，未在本次复跑。镜像报告的运行冒烟是 Linux amd64、禁网和合成数据；arm64 有构建及摘要核验，不宣称运行验收。[Release 证据](https://github.com/JIUSHIQINGSHAN/Lantai/releases/tag/v0.69.1) |
| 兰台 LongMemEval-S，2026-10 历史结果 | 25 题会话级召回，混合 recall@2 为 `21/25`，纯向量 `19/25`、纯 BM25 `20/25`；recall@5 分别是 `22/25`、`21/25`、`22/25`。混合@5 与纯 BM25 **持平**；闸门通过子集混合@5 为 `22/22`，不是全题满分。 | 小样本、历史版本、只测召回，不测答案；不套用历史报告摘要中与表格不一致的“纯 BM25 0.84”。[修订结果](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/longmemeval-2026-10/summary-rev1.json#L1-L106) · [方法与边界](<benchmarks/selfproof-report-2026-10.md#L17-L42>) |
| 兰台 MemProbe suite56，2026-10 历史结果 | 用同一 Qwen3-8B 读答模型，逐字直存通道 `141/224`，完整上下文对照 `146/224`；兰台来源识别 `8/34`，时间关系 `9/22`。 | 不是普通 LLM 提取管线，也不是完整上下文的理论上限。简易 RAG 有 65 个执行超时，不能把不同分母的数字直接排名。[结果数据](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/benchmarks/memprobe-2026-10/summary.json) · [实验协议与分母](<benchmarks/memprobe-report-2026-10.md#L23-L52>) |

兰台的 [中文遗忘质量门禁](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/scripts/run_forgetting_quality.py#L108-L145) 适合做同口径的版本回归，关注陈旧残留、错字召回、时效与新旧事实排序；其 `--check` 使用隔离临时库与确定性离线路径，不证明真实模型回答可靠。上游[对历史公开基准的说明](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/docs/BENCHMARKING-POSTURE.md)也明确撤下早期 LoCoMo 试跑排名：只有 2 个样本，缺逐题记录、配置和代码锚点。两边暂无本白皮书可引用的同语料、同模型、同预算 A/B，历史基准、CI 成绩与宿主冒烟不能拼成一个「质量分」。

**历史性能样本，不是生产 SLA。** 上游项目报告的 `f0.7` 同机实验使用固定向量模型替身，连续写 2,000 条、再查 300 次；相对旧 mem0/Qdrant 路径，内存从约 207 MB 降至 112–113 MB，检索 p95 则从 24–26 ms 升至 38–42 ms。兰台旧栈（Chroma 0.6.3）的 5,000 条合成数据实验中，本地混合检索 p50/p95 为 61.3/63.3 ms，不含真实模型时延。硬件、数据、存储和测量路径都不同，这两个实验只能在各自口径内使用，不能作为 `f0.9` 与 `v0.69.1` 的性能对比。[上游同机实验](https://github.com/monkey2jack/aiduMEI/blob/003c3aa9c409e15d554c71d7224952eb23e70278/README.md#资源占用f07-对比-f06) · [兰台基线与环境](<research/兰台性能基线-2026-11-07.md#L25-L75>)

## 5. 可借鉴项、取舍与验收

| 优先级 | 建议（非已实现功能） | 最小可验收结果 |
|---|---|---|
| P0 | **先把一条宿主链路验透。** 选已支持的 Hermes 做接入样板，借鉴上游的合成暗号验收，复用兰台现有摄取事件与注入回执，不另造一套指标。 | 实际宿主写入一条合成事实，新会话召回并收到回执，逐项对账 `接收→候选→应用→召回→注入`；分别模拟只读正常、写入断开、队列重试。区分「收到但待审」与「已经可召回」，不把后台写入算成用户写入。 |
| P0 | **验证旧向量库的距离语义和迁移后的业务效果。** 上游单库给兰台的直接启示是核对一致性，不是立刻把 Chroma 换掉。 | 先用合成库验证旧 L2 与新建 cosine 差异，再用获授权且隔离的冷副本固定语料与查询，报告实际度量、召回、去重和属主过滤的差异；新旧存储可分别回滚。[迁移记录](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/docs/chroma-migration.md#L7-L30)确认迁移保留旧度量，而[检索阈值](https://github.com/JIUSHIQINGSHAN/Lantai/blob/18892e7ec5b49cbe9cd96bb70352c7432f744ccb/lantai/retrieval/hybrid.py#L543-L567)仍需连同实际集合解释。 |
| P1 | **做可复算的同场对照，先决定哪些原话值得保留。** 以兰台逐字与提炼两条路径为内部对照，再考虑是否纳入 aiduMEI。 | 固定提交、语料哈希、同一模型及上下文预算；至少分当前态、历史态、拒答、来源与删除五组。分开报候选接受率、Recall@K、回答正确率、撤回泄漏率、超时、延迟与调用量，保留逐题分母与失败。对规则、日期、数字和否定条件的折叠保真先做离线或影子验证。 |
| 暂缓 | **不照搬自动召回，也不做无验证的存储重写。** 上游在特定版本的 DSH 接入可作为测试素材，不能当作兰台的授权证明。 | 只有真实宿主能证明输入归属和最终准入、后端能执行明确的数据范围与留存策略，并且隔离演练通过否决、切换目录、取消、重启和跨作用域拒绝检查，才考虑恢复自动路径。此前保持显式 MCP。 |

**结论：**aiduMEI 值得最先借鉴的是宿主接入的验收纪律；兰台值得保留并验证的是记忆的准入、变更、历史与回执。下一阶段用一条可复算的宿主闭环和一套同口径对照证据回答「有没有记住、有没有找对、有没有越界」，比增加功能名称或工具数量更有价值。

附：[复用的白皮书研究与撰写提示词](<research/lantai-upstream-whitepaper-prompt.md>) · [一手资料核查底稿与逐题复算出处](<research/whitepaper-upstream-evidence-2026-11-20.md>)。
