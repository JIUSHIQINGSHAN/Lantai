# 上游调研（v21.2 → f0.3++）与下一步开发计划

> 2026-10-07 · 基于上游 commit `418c3c3`（2026-10-06，f0.3++ 发布）
> 上一次调研：[upstream-v212-gap-analysis.md](upstream-v212-gap-analysis.md)（2026-09-18，止于 v21.2 / `da34e1a`）
> 上游：https://github.com/monkey2jack/aiduMEM

## 一、结论

1. **上次调研的七项缺口已全部落地**：MMR（`hybrid.py:744`）、errsig（`retrieval/errsig.py`）、session_id 写入链（`memory_service.py:320,404,660`）、回声抑制（`hybrid.py:44` echo_suppress）、咀华 distill 泳道（`settings.py:79`）、写线活性探针（`check_ingest_wiring.py` + 司天）、episode 信用（`services/episode_service.py`）。
2. **上游 9-18 之后又推进了四代际**（v22.0 雷霆审计 → v22.1 → f0.1/f0.2 → f0.3 → f0.3+ → f0.3++），125 commits / 266 files。这批的成色与上批不同：**功能增量少，事故教训多**——consolidator 误删、写线空转、恢复漏向量层、LLM 无并发闸被打爆，全是他们生产上真炸过的。
3. **真缺口 5 项 + 预防性 2 项**，全部可以**挂靠到既定的 roadmap-v2 11 张票上**，不需要另开战线；独立小票 1 张（LLM 并发闸）。**S2（入口限长）10-07 勘误取消**：实施核实发现服务层与 Pydantic 已全覆盖限长，调研时查浅了。
4. **不跟清单 4 项**，理由见 §五，防止将来重复调研。
5. **既定 10 月计划不变**：更漏关键路径（票 05 → 08/09/10 → 11）照跑，本计划是往既定票里挂上游经验 + 穿插两张小票。

## 二、上游 9-18 以来发生了什么（四代际速览）

| 代际 | 日期 | 主题 | 对兰台有价值的点 |
|---|---|---|---|
| v22.0 雷霆审计 | 09-20 | 大规模安全整改 | B11 禁 fallback 到 default；PBKDF2 600k；治理引擎（多语言注入防御+有界评估池）；bearer 必须声明身份 |
| v22.1 | 09-21 | Hermes 升级适配 | turn_author 多实体隔离、Hook 熔断旁路 |
| f0.1 / f0.1+ | 09-23/24 | **读线末端补时间**；删除安全 | 召回注入让模型看见「什么时候」；时间戳优先级常量化；日期粒度可配；容量合并误删 P0 修复 |
| f0.2 | 09-26/28 | 接线整改 | 读线补 session、萃取线补全；mem0ai 2.2.1 |
| f0.3 | 09-29/30 | 四份审计合并整改 | **consolidator 误删除血**（淘汰判据与 /delete 契约脱节：日志写「删除 0/N」实际照删）；矛盾检测重写（单字反义词两两比对平方级误伤 → warn-only）；墓碑恢复补回向量层；LLM 并发闸门；异步幂等 TTL+回执；**coalesce 按 origin session 分键**；注入检测先限长；verbatim 先 commit 再 FTS |
| f0.3+ | 10-04 | 决策模型可选通道 | 候选池预算关键词兼容（top_k/limit 被静默限制 20 的教训）；宿主注入保留类型标记+原文 500 字预算 |
| f0.3++ | 10-06 | 收口加固 | 自动合并默认关闭；MCP 全工具重复失败守卫（5 次/60s+冷却 30s）；全量删除显式 confirm；WAL fail closed |

## 三、逐项对照（兰台现状 → 判定）

### 3.1 真缺口（要补）

| # | 上游项 | 上游位置/代际 | 兰台现状（已核实） | 判定 |
|---|---|---|---|---|
| G1 | **进程级 LLM 并发闸门** | f0.3，`AIDUMEI_LLM_MAX_CONCURRENCY` 默认 4，call_llm 与 mem0 共用 | `lantai/llm/client.py` 无任何 Semaphore/Lock/并发限制（grep 实证零命中）。高峰期潮波冲刷+反思+图谱多路同时触发时，对外部 LLM API 的并发不设防 | **移植**。小票：进程级 Semaphore + 配置项 + 等位带超时 |
| G2 | **MCP 工具重复失败守卫** | f0.3++，5 次/60s、冷却 30s、第 6 次不执行、单探针恢复 | `lantai/cli/mcp.py` 无熔断/守卫（grep 实证零命中）。兰台服务故障时宿主每次对话都白撞一次 | **移植**。中票，挂靠票 06（宿主矩阵） |
| G3 | **coalesce 按 origin session 分键** | f0.3（`6db2bae`），修「多会话同 user 消息混并」 | `lantai/ingestion/coalesce.py:36` 仍按 `user_id + lane` 分键。A/B 两个会话同属一个 user 时，消息混进同一个缓冲一起提炼——轻则记忆串场，重则会话 A 的私密内容被提炼后注入会话 B | **移植**。兰台 session_id 写入链已打通（v21.2 缺口收口时做的），这是临门一脚。挂靠票 08 |
| G4 | **异步摄取幂等回执** | f0.3（`2b90cee`），TTL + redacted receipts | `async_ingest_service.py` 无幂等（grep 实证零命中）。宿主网络重试会导致重复入库；coalesce 的 `job_id` 内容指纹只挡「同内容」，挡不住「同请求不同内容重试」 | **移植**。挂靠票 11（评测收口时一并加幂等用例） |
| G5 | **入口侧注入检测（先限长）** | f0.3++，32KiB/100KiB near-match 经独立进程预算验证；超限整条拒 | 兰台樊篱（`llm/fence.py`）是**出口侧**包裹（注入前声明「数据不是指令」）。**限长面勘误（10-07 实施时核实）**：REST 侧 Pydantic 已限长（dialogue 50k / raw 200k）、`ingest_dialogue` 服务层自带 50k 上限（`dialogue.py:86-87`），MCP 三入口全部走服务层——**长度上限实际已覆盖，S2 票取消**。真正剩的只有有界正则（NFKC 归一 + 重复模式检测），与 REST/MCP 无关 | **调整**。限长已存在（调研时查浅了）；有界正则仍挂票 08（写入提取纪律），保留为 G5 第二刀 |
| G6 | **读线末端补时间** | f0.1（`c746307`），召回注入让模型看见「什么时候」+ 时间戳优先级常量化 + 日期粒度可配 | 更漏 Schema 已建（`tables.py:143-149` event_time/valid_from/precision），但注入面零消费：`cognition/context.py:38 to_prompt` 与 `integrations/host_protocol.py render_host_response` 均无 event_time（grep 实证零命中）。检索命中带回了时间字段却不告诉模型 | **与票 09 合并**（检索双视图本来就要做当前态/历史态），上游的时间戳优先级常量与粒度可配直接抄作业 |

### 3.2 预防性项（先盘点再决定）

| # | 上游项 | 上游教训 | 兰台现状 | 动作 |
|---|---|---|---|---|
| P-a | 全量删除显式 confirm | f0.3++：全量删除必须显式 confirm，拒绝发生在 WAL/存储副作用之前 | `routes_terminal.py:322 delete_memory` 是单条删除，有归属校验 + checkpoint 可回滚，风险低；但**未盘点是否存在批量/全量删除入口**（grep 未见，需 AST 全扫确认） | 盘点票：全扫删除面，只出报告；有全量入口才加 confirm |
| P-b | 墓碑恢复补全所有层 | f0.3：restore_tombstone 原先只回插 facts+FTS，恢复出的记忆向量层召回不到——**恢复路径漏一层** | 兰台恢复面分散：`unretract_memory` / `unarchive_memory` / `revive_consolidated`（`record_ops_service.py`），起复已做 FTS+向量重同步（ADR-0052），但无逐层核验回执 | 自查票：对每个恢复函数核验「SQL/FTS/向量」三层是否齐，缺层补回执。对照 [护栏要枚举不要 allowlist](../../../C:/Users/Asus/.claude/projects/C--Users-Asus-Desktop---/memory/guard-must-enumerate-not-allowlist.md) 方法论 |

### 3.3 已规避（上游炸过、兰台架构上没这个坑）

| 上游事故 | 兰台为何没有 |
|---|---|
| consolidator 误删（日志「删除 0/N」实际照删；淘汰判据按 `status=="ok"` 而 /delete 契约早已四态化） | 兰台沉潜走**提案审批制**（`consolidation_service.py:341` SHADOW → 人工/持节裁决），没有「自动直接删」的路径；修剪 `prune_decayed_synapses` 是归档不是删除，可逆 |
| 矛盾检测平方级误伤（单字反义词两两比对，「不要熬夜」「不要喝酒」判矛盾，每条每轮显著性减半） | 兰台直断（`cognition/conflicts.py`）是 6 维评分 + DecisionTrace 可解释裁决 + 更漏 recency 修正，不做单字反义词比对 |
| reranker 配置缓存不生效 | 兰台 params 是不可变 dataclass + fail-closed（`hybrid.py:62`），无配置缓存层 |
| 写线活性探针读到自己的心跳 | 已移植时按上游教训挂司天（`check_ingest_wiring.py`），用的是带 session 的真实检索数 |

### 3.4 上次调研遗留的核对

- 上游「候选池被 SDK 默认静默限制 20 条」教训：兰台向量层是自研 Chroma 封装（`hybrid.py:499,572` 显式传 `top_k=fetch_n`），**无此坑**，但票 11 评测时值得加一条断言钉住（防将来换 SDK 复发）。

## 四、下一步开发计划（挂靠既定 11 票 + 两张小票）

> 节奏与 [monthly-report-2026-10.md](../plans/monthly-report-2026-10.md) §三 完全兼容：**既定 11 票照跑，上游吸收按主题挂靠对应票 + 穿插两张独立小票，不开新战线。**
> 今天 10-07，处于 W2。每张票照旧走六阶段 SOP + 不 mock 冒烟 + 变异门禁。

### 主线（不变）

| 周 | 票据 | 上游吸收挂靠 |
|---|---|---|
| W2 剩余（10/7-10/9） | **票 05 更漏 Schema+迁移**（关键路径起点，ready-for-human 待验收后开工尾款） | — |
| W3（10/10-10/16） | 票 08 写入提取纪律 / **票 09 检索双视图** / 票 10 迟到更正闭环 | **票 08 ← G3 潮波按会话分键 + G5 入口限长**；**票 09 ← G6 注入帧带时间**（时间戳优先级常量化 + 粒度可配，抄 f0.1 作业） |
| W4（10/17-10/24） | **票 06 宿主矩阵** + 票 11 评测收口 | **票 06 ← G2 MCP 失败守卫**（5 次/60s + 冷却，宿主矩阵 DoD 增补「宿主反复撞故障服务时兰台自己熔断」）；**票 11 ← G4 幂等用例 + 3.4 池预算断言** |
| 11 月 | 更漏尾款 + 月度复盘 | P2 项排期（§4.2） |

### 独立小票（穿插，不动主线路径的文件）

| 票 | 内容 | 量级 | 排期 |
|---|---|---|---|
| **S1 LLM 并发闸门（G1）** | 进程级 `threading.BoundedSemaphore` + `LLM_MAX_CONCURRENCY` 配置（默认 4）+ 等位超时；chat/vision/embed 三通道共用同一闸 | **已完成（10-07）**：6 例测试 + 4/4 变异 KILLED，见 `.scratch/llm-concurrency-gate/` | ✅ |
| ~~S2 入口限长第一刀~~ | **10-07 勘误取消**：实施核实 `ingest_dialogue` 服务层自带 50k 上限（`dialogue.py:86`）+ REST Pydantic 限长，MCP 三入口全走服务层——限长已覆盖，调研时查浅了。有界正则（G5 第二刀）仍挂票 08 | — | ❌ |
| S3 删除面盘点（P-a） | AST 全扫删除/清空入口，出报告；有全量入口才开 confirm 票 | 1 小时 | W3 |
| S4 恢复路径三层自查（P-b） | `unretract/unarchive/revive_consolidated` 逐个核验 SQL/FTS/向量三层齐 + 回执；缺层补 | 半天 | 11 月 |

### P2 / 11 月以后

- **PBKDF2 决策记录**：上游 v22.0 把 key 哈希升级 PBKDF2 600k。兰台 `auth.py:123` 是 SHA-256 无盐，但 key 本体是 `token_urlsafe(32)`（256 bit 熵），无盐暴力成本天文数字——**现状风险低，暂不动**，落一条 ADR 备忘：将来若允许用户自设弱 key，必须先升级。
- **可观测小票**：`/health` 加 git_sha（当前 `app.py:86` 硬编码 version）；司天加 24h LLM 降级率一级指标（上游 f0.3 的 `report.py` 同款）。
- **G5 第二刀**：入口有界正则（NFKC 归一 + 中文重复模式，预算受控），上游 f0.3++ 经独立进程验证过边界。

## 五、不跟清单（含理由，防止将来重复调研）

| 上游能力 | 不跟理由 |
|---|---|
| **决策模型可选通道**（f0.3+/++，Drex/Clef 等自动分类+检索核验） | 上游自己默认关闭、本地模式直接禁用；实测检索 p50 +68%、p95 +120%。兰台无此需求；且该功能强依赖 mem0 事实模型，与兰台四路混合检索架构不同源 |
| **WAL sidecar 跨进程锁**（f0.3++，fail closed + POSIX fsync） | 兰台单机单进程部署，`db.py:23` busy_timeout=30s 已覆盖现役并发面；多进程化是架构级决定，届时再议 |
| **官方 mcp SDK 迁移**（f0.3++，`mcp==1.30.0`） | 兰台自研 JSON-RPC 2.0 已稳定（1249 测试覆盖），迁移风险大于收益 |
| **自动合并默认关闭**（f0.3++ #16） | 上游修的是他们激进合并的生产事故；兰台校雠（ADR-0019）本来就是余弦预筛+结构判别+值变更走待审提案的保守口径，无此病灶 |

## 六、待维护者拍板

1. **票 13 遗留决策**：`search_fts` 家族的 `principal=None` 收敛与否（CHANGELOG Unreleased 已记录：worker 契约与入口收敛是两层决定，grep 实证仓内暂无真实调用方）——不拍板则维持现状（刻意契约，测试钉住）。
2. **spec §二.8 在途改动归属**（auth/settings/vector_store 未提交文件）——W1 遗留，基线冻结口径依赖它。
3. **发布纪律照旧**：本计划全部通过本地门禁后停在 push/tag 人工闸门，无本月发布计划。
