# 上游对比：aiduMEI v21.2 vs 兰台 0.21.0

> 2026-09-18 · 基于上游 commit `45ae2cf`（2026-09-17，v21.2.0 Memmy 融改 + 写线收口）
> 上游：https://github.com/monkey2jack/aiduMEM

## 结论

兰台在 8-31（0.21.0 悬镜）之后上游又推进了一整个大版。上游 v21.2 的六项新能力兰台**全部没有对应物**，其中四项是纯增量、实现量小、值得直接移植；一项（写线活性探针）兰台有基建（司天）但缺那条规则；一项（轨迹信用）依赖 LLM 反馈回环，按上游自己的建议先装不启用。

另有一条**上游踩坑教训**比功能本身更值钱：写入路径不透传 session，导致回声抑制、轨迹登记、会话萃取三个功能上线即空转而所有探针全绿。兰台现状与上游踩坑前**一模一样**——`MemoryItem.session_id` 字段在表上存在（`lantai/models/tables.py:45`），但 gate/candidate 写入路径从未赋值（全链 grep 零命中）。

## 逐项缺口

| 上游能力 | 上游位置 | 兰台现状 | 借鉴判定 |
|---|---|---|---|
| M2 回声抑制（本会话自写的候选不回捞） | `ducky/epistemic.py` + `scoring.py` | 无；写入不落 session 来源 | **移植**（先补来源链） |
| M4 MMR 多样性（λ·相关 −(1−λ)·冗余） | `ducky/scoring.py:805 mmr_select` | 无；纯按分截断 | **移植**（纯增量，~80 行） |
| M6 错误签名通道（errsig） | `ducky/pattern_extract.py:177` + 检索 bonus | 无 | **移植**（纯增量，正则+bonus） |
| 会话精华萃取（distill 泳道） | `ducky/session_distill.py`（203 行） | 底本（ADR-0021）是交接快照，不入记忆库 | **移植**（与底本互补不重叠） |
| 写线活性探针（有读零写即降级） | `/health ingest_liveness_ok` + `scripts/check_ingest_wiring.py` | 司天 13 条告警有零召回、无写活性 | **移植**（挂进司天） |
| M1 轨迹级奖励信用（episode credit） | `evolve_episodes` 表 + feedback 端点 | 无 | **低优**（上游默认权重 0，等数据再开） |
| M7 episode rollup / M8 跨殿借阅 | `recall_funnel` + event_ledger | 烽燧已有多跳扩散；跨殿借阅与兰台 ACL 车道隔离路线冲突 | **不移植** |
| 双引擎向量（Cloud/Local/Auto 三模式） | `ducky/vector_backend.py` | 单模式外部 API + FTS 降级（拾遗已兜底） | **暂不**（拾遗已覆盖故障面） |

## 关键整合点（兰台侧）

- **MMR 插入点**：`lantai/retrieval/hybrid.py` 两处截断——`candidates = scored_items[:fetch_n]`（hybrid.py:464 附近）与精排路径 `candidates[:top_k]`。冗余度量可直接用 jieba 分词 token 重叠（上游用词表重叠，兰台现成 jieba 更顺）。兰台没有「点火条」，上游 `protect_ignited` 概念对应兰台的 `_apply_supersedes_order`：被取代置顶的条目应豁免冗余惩罚。
- **回声抑制前置**：先打通 `ingest → candidate → MemoryItem.session_id`（写入路径现在从不赋值），再在 `hybrid.py` 打分前滤 `m.session_id == principal.session_id` 的**本会话新写**（按 `updated_at` 时间窗，防误杀跨会话检索）。上游教训：来源必须在写入侧**显式传递**，不能靠 contextvar 隐式通道（上游审计 🔴-1 实证生产全空）。
- **errsig 单一真源**：正则只写一份（`_ERRSIG_RE`：CamelCase + Error/Exception/Warning 收尾），写入侧（gate 抽取）与检索侧（`hybrid.py` 打分 bonus）共用 import，杜绝两侧漂移（上游自查轮专门收口过这个）。
- **distill 泳道**：新增 lane `distill`（慢衰减 0.3），`POST /session/distill` 只提炼不落库、`/add` 走完整闸门管线；LLM 不可用时确定性降级（取该会话最长的两条原文拼接），metadata 标 `distill_mode=fallback` 不冒充。
- **写线活性挂司天**：`build_monitor_snapshot` 的 quality 域加 `ingest_conv_reads_24h`（带 session 的真实检索数）与 24h 写入数，`evaluate_alerts` 加一条：有检索零写入即 critical。上游教训：判据不能用后台巡检量——探针读到的是自己的心跳。

## 上游方法论可借鉴处（不写码）

1. **「开关一开就不对」自查法**：默认关/旁路的功能从未在冒烟现形，上游施工期翻出 9 处空转。兰台测试纪律已有「不 mock 冒烟」条款，可补一条：**带开关的新特性，必须有一条开+关对照的冒烟用例**。
2. **健康脚本必须读字段，不能只看 200**：`health_check.py` 曾把整份降级清单拿到手一个字段不读。兰台 `scripts/` 定时巡检值得对同款问题审一遍。
3. **降级必须留痕且可复现**：distill fallback 取「最长两条」而非「随机/最新」，理由是长度是可复现代理指标。兰台各降级路径（拾遗等）可对照自查是否满足「如实声明 + 可复现」。
