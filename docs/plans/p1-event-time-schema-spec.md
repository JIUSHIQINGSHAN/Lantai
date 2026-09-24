# 兰台 P1·更漏（Genglou）：事件时间与双时间轴 Schema 规范

> **版本**：P1 首位（调研 D20 升格）
> **定位**：MemoryItem 事件时间（event time）与双时间轴（bi-temporal）数据级 Schema、检索时效视图与迟到更正机制的技术规范；配套决策记录见 [ADR-0048](../adr/0048-genglou-bitemporal-event-time.md)。
> **命名**：本规范域名词「更漏」已按 ADR-0013 R4 登记 `CONTEXT.md` 词汇表（铜壶滴漏计时意象）；新增字段沿用英文技术标识（R6），不再另造中文名。

---

## 0. 依据与范围

### 0.1 调研依据

- 记忆全景调研 2026-09：**Zep/Graphiti 在「时间感知与冲突消解」维度得 5.0，为全部 20 系统中唯一满分**——bi-temporal 是其显式设计（`valid_at`/`invalid_at`），facts 自动失效而非删除，查询 `invalid_at IS NULL` 取当前视图（`docs/research/memory-landscape-2026-09/iterations/iter-11.md:7`）。
- 兰台同维度仅 3.0：**事件时间/有效时间未入库是唯一数据级硬缺口**（iter-11.md:10）；旧文档「Chronos 双时间轴」说法已被代码核验证伪——`MemoryItem` 仅有 `created_at`/`last_used_at` 等，supersedes 仅存在于 `MemoryEdge.relation`（iter-07.md:12）。
- 决策 D20：时间主线升为 **P1 首位**（治理四原语 temporal supersession 中兰台唯一数据级缺失）；D21：**不做**图数据库集群与全量 Graphiti 式抽取（iter-11.md:20-21）。

### 0.2 范围

| 做 | 不做（边界） |
|---|---|
| MemoryItem 增 `event_time` / `event_time_precision`，语义化 `valid_from` / `valid_to` | 图数据库（Neo4j/FalkorDB）、MemoryEdge 图库化（D21） |
| 检索当前态视图（current view，兼容现状）与历史时点视图（as-of query） | 全量 Graphiti 式事件抽取（D21） |
| 时间窗过滤（time_from/time_to）与软/严格双模式 | 事务轴完整 system-time as-of（append-only 版本化，v2，用 `MemoryCheckpoint` 快照近似） |
| 迟到更正：supersedes 边 + 知命态 + 直断冲突账本联动 | 中文相对时间表达式解析器（gate 提取层另票） |

---

## 1. 现状核验（本规范的地基）

以下均为本次核验的一手代码事实：

| 现状 | 位置 | 含义 |
|---|---|---|
| `MemoryItem` 有 `created_at`/`updated_at`，无 `event_time` | `lantai/models/tables.py:143-144` | 事务轴唯一存在，事件轴为零 |
| `valid_from`/`valid_to` 已存在但均为 `datetime \| None`、无索引、写入侧近乎不用 | `lantai/models/tables.py:141-142` | 有列无语义：仅评测种子构造（`lantai/eval/forgetting_quality.py:112-115`） |
| `_chronos_filter`：`valid_from` 未到→剔除；已过 `valid_to`→`decay_score *= 0.3` 保留召回 | `lantai/retrieval/hybrid.py:716-734`（调用点 `hybrid.py:518`、`hybrid.py:871`） | 仅有「当前态」雏形，无 as-of 能力，`now` 硬编码 `utcnow()` |
| supersedes 仅是 `MemoryEdge.relation` 取值 | `lantai/models/tables.py:267`、`lantai/storage/edges.py:60` | 取代关系有边、无事件轴失效时刻 |
| 知命生命周期：`SUPERSEDED` + `superseded_by`/`superseded_at` | `lantai/models/tables.py:150-159` | 被取代态可复用，但 `superseded_at` 是事务轴时刻 |
| 冲突账本 `ConflictEvent`：`kind` 仅 `mutex` / `override`（预留），status open/resolved/dismissed | `lantai/models/tables.py:344-356` | 迟到更正落账的现成挂点 |
| 直断 `ConflictEngine` 六维评分，recency 权重 0.15 按 `created_at` 计算 | `lantai/cognition/conflicts.py:48`、`conflicts.py:91-98` | 迟到更正场景下「最近写入机械取胜」的错判根源 |
| 检索主入口 `POST /search`，无任何 as_of/时间窗参数 | `lantai/api/routes_search.py:13` | API 面缺口 |
| 迁移链：`CURRENT_SCHEMA_VERSION = 20`，`_has_column` 幂等 + DDL 固定字面量，异常不阻断 | `lantai/storage/db.py:19`、`db.py:22-41`、`db.py:386-408` | 本波续接 v20→v21 |

**结论**：`created_at` 是唯一时间事实来源，`valid_from`/`valid_to` 是有列无语义的空壳，事件轴完全缺失——与调研「数据级硬缺口」结论一致。

---

## 2. 双时间轴模型

### 2.1 两条轴的定义

```
事务轴（transaction time，入库轴）        事件轴（event/valid time，现实轴）
─────────────────────────────────       ─────────────────────────────────
「兰台何时知道这件事」                    「现实中这件事何时发生/何时为真」
created_at   首次登记时刻                event_time   事件发生（或状态起始）时刻
updated_at   最近改写时刻                valid_from   主张有效期起点
version      改写版本号                  valid_to     主张有效期终点（NULL=未失效）
MemoryCheckpoint 变更快照               event_time_precision  时刻的释读精度
```

两条轴**互不回写**：迟到更正发生在今天，`superseded_at` 记今天（事务轴如实），被更正事实的 `valid_to` 锚定到新值的现实起点（事件轴如实）。混淆两轴是本规范要消灭的第一类错误。

### 2.2 MemoryItem 扩充字段定义

| 字段 | 类型 | 默认 | 语义 | 写入纪律 |
|---|---|---|---|---|
| `event_time` | `datetime \| None` | `None` | 正文所描述事件的发生时刻（episodic，如「昨天迁移了数据库」）或所陈述状态的起始时刻（stateful，如「2024 年起用 Postgres」）。`None` = 未提取到，**不猜** | gate/fastpath 提取置信度不足 → 保持 `None`，`provenance` 记 `event_time_extract_failed: true`（宁 miss 不脏写，iter-11 缺口 2：「失败进待审而非猜」） |
| `event_time_precision` | `str` | `""` | `event_time` 的释读精度，枚举 `year`/`month`/`day`/`hour`/`minute`/`second`/`fuzzy`。约束：**非空 ⇔ event_time 非空**；读取侧遇未知枚举值一律按 `fuzzy` 处理（最宽解释，绝不因脏值硬排除） | 写入侧校验枚举；`event_time=None` 时必须为 `""` |
| `valid_from` | `datetime`（目标语义必填，物理列维持可空，理由见 §5.3） | `default_factory=utcnow` | 主张有效期起点（现实轴）。gate 显式提取到生效时间则写提取值；提取不到以摄取时刻近似（Zep `valid_at` 缺省同款策略） | 服务层写入路径保证非空；存量 NULL 由迁移回填 `created_at` |
| `valid_to` | `datetime \| None` | `None` | 主张有效期终点；`NULL` = 尚未失效（Zep `invalid_at IS NULL` 即当前有效的对应物） | 仅两种途径写入：gate 提取显式失效时间；迟到更正流程回写（§4） |

模型落点（`lantai/models/tables.py`，紧邻现有 `valid_from`/`valid_to` 两行改写 + 新增两行）：

```python
event_time: datetime | None = Field(default=None, index=True)   # 事件轴：现实发生时刻
event_time_precision: str = Field(default="")                   # year/month/day/hour/minute/second/fuzzy
valid_from: datetime | None = Field(default_factory=utcnow, index=True)  # 主张有效期起点（语义必填）
valid_to: datetime | None = Field(default=None, index=True)     # 主张有效期终点（NULL=未失效）
```

> `datetime` 导入与 `utcnow` 已在该文件顶部可用（`lantai/models/tables.py:33-37`）。

### 2.3 精度枚举与不确定性区间

`event_time` 只是一个锚点，as-of/时间窗匹配时按精度展开为**不确定性区间** E：

| precision | E（左闭右开） | 典型来源 |
|---|---|---|
| `year` | [当年-01-01 00:00, 次年-01-01) | 「去年开始用」 |
| `month` | [当月-01 00:00, 次月-01) | 「上个月上线」 |
| `day` | [当日 00:00, 次日 00:00) | 「9 月 15 号迁的库」 |
| `hour` | [整点, 整点+1h) | 「下午三点改的」 |
| `minute` | [整分, 整分+1min) | 提取器带时分 |
| `second` | 点（E = [t, t]，**闭点区间特例**） | 日志/原始时间戳 |
| `fuzzy` | [锚点−1d, 锚点+1d)（定宽 ±1 天，不做更宽泛化） | 「大约去年底」 |

> **点区间特判（必须实现）**：全表约定左闭右开，唯 `second` 是闭点 [t, t]。交叠谓词对 `second` 必须特判为「t 落在窗口内即命中」，**不得**按左闭右开字面解读成 [t, t) 空集——否则秒级记忆永不与任何窗口重叠。落锚测试：`second` 记忆对含 t 的窗口命中、对不含 t 的窗口不命中。

- `event_time IS NULL` 的记忆**不属于任何时间区间**：永不参与事件轴硬过滤，as-of/时间窗激活时按 `temporal_unknown` 软放行（§3.3）并在 explain 标注。
- 时区约定沿用既有纪律：一律 UTC；naive datetime 按 UTC 解释（与 `_chronos_filter`、`ConflictEngine` 现行做法一致，`lantai/retrieval/hybrid.py:724-728`、`lantai/cognition/conflicts.py:93-94`）。

### 2.4 与既有机制的关系（只复用、不另造）

| 既有机制 | 关系 |
|---|---|
| 知命生命周期 `lifecycle_status=SUPERSEDED` + `superseded_by`/`superseded_at` | 迟到更正的**事务轴**痕迹：取代判定发生在今天，`superseded_at=utcnow()` 如实记录，不回写伪造 |
| `MemoryEdge.relation="supersedes"` | 迟到更正的**关系**痕迹：新值→旧值的边照常写（`lantai/storage/edges.py:9`） |
| `valid_to` | 迟到更正的**事件轴**痕迹：被更正旧值由本字段承载现实失效锚（相当于 Zep `invalid_at`），旧行不物理删、不进 `retracted`（它不是「从未为真」，是「至某刻为止为真」） |
| 笔削 retract（ADR-0047 `status="retracted"`） | 「自始错误」型更正走撤回语义（全检索面禁用 0 命中），**不**设 `valid_to`——曾为真与从未为真必须语义分离 |
| `MemoryCheckpoint`（底本快照） | 事务轴历史近似的现成数据源（v2 边界，本波不实现查询） |
| 回声抑制时间窗（ADR-0046） | 会话内回声抑制，独立于本规范的现实轴视图，互不替代 |

### 2.5 不变式（落锚测试对象）

1. **I1** `event_time` 非空 ⇒ `event_time_precision ∈ 枚举`；`event_time` 为空 ⇒ `event_time_precision == ""`。
2. **I2** 新写入记忆 `valid_from` 非空（服务层保证）；存量回填后全表 `valid_from IS NULL` 计数为 0。
3. **I3** `valid_from ≤ valid_to`（两者均非空时）；违反写入拒绝（不静默修正）。**作用域：服务层新建/直改路径**。迟到更正回写路径例外见 §4.2 步骤 c——旧值 `valid_to` 锚点早于其 `valid_from`（回填近似所致）时**钳制至 `valid_from` 并在账本 detail 留痕**，不拒绝、不静默（钳制是如实近似，拒绝会让最常见迟到更正形态无法落库）。
4. **I4** `event_time IS NULL` 的记忆在任何视图下都不因时间条件被硬排除（软放行）。
5. **I5** 全部时间字段 UTC 语义；naive 按 UTC 解释。
6. **I6** 笔削 `retracted` 在当前态与 as-of 视图下均 0 命中（沿用 ADR-0047 验收口径，双视图复验）。

---

## 3. 检索时效过滤与时间窗机制

### 3.1 当前态视图（current view，默认，零回归）

- **保持 `_chronos_filter` 现行语义不变**：`valid_from` 未到 → 剔除；已过 `valid_to` → `decay_score *= 0.3` 保留召回（`lantai/retrieval/hybrid.py:716-734`）。回填与默认值策略对当前态无影响（`created_at ≤ now` 恒过、过期仍 0.3 衰减）——**零回归铁律**。
- 可选收紧开关 `TEMPORAL_CURRENT_STRICT`（默认 `false`）：开启后已过 `valid_to` 的记忆退出当前态（仍可被 as-of 命中）。默认关的理由：现状行为依赖方未知，行为收紧必须有独立票验证后再开。
- `event_time` 在当前态**不参与过滤**：「下个月上线 X」的事件时刻在未来，但「计划 X 月上线」这条主张现在为真——事件时刻是描述，主张有效期才是过滤依据（两轴分工的必然推论）。

### 3.2 历史时点视图（as-of query）

请求带 `as_of: datetime` 时触发，回答「现实时点 T 哪些主张为真」。一条记忆命中当且仅当（任一）：

```
命中 = 有效期命中 ∨ 事件区间命中 ∨ temporal_unknown（软放行，I4）
有效期命中：  (valid_from IS NULL OR valid_from <= :as_of)
          AND (valid_to   IS NULL OR valid_to   >  :as_of)
事件区间命中： E(event_time, precision) 与 [as_of − Δ, as_of + Δ] 存在交集
          （Δ 默认 1 天；second 点区间按 §2.3 特判：t ∈ 窗口即命中）
temporal_unknown：valid_from/valid_to/event_time 全缺（理论上回填后不应出现，
                  防御分支）→ 任何模式都放行（I4：event_time IS NULL 永不硬排除），
                  软模式不降权或按 fuzzy 乘子降权，严格模式同样放行仅降权
```

- 全部时点值为**绑定参数**（`:as_of`、`:delta`），禁止拼接/format/f-string 组装 SQL（生成前安全约束；与 `lantai/storage/db.py:22-27` 的防注入纪律一致）。
- **被取代旧值在 as-of 下不降权**：检索期 supersedes 降序（`_apply_supersedes_order`，`lantai/retrieval/hybrid.py:208-282`）是「当下取向」的；as-of 视图下改为按 `valid_to` 判定——`valid_to > as_of` 的旧值在 T 时点本就是真实记录，不因后来被更正而失格。这是双时间轴相对「机械最近写入取胜」的核心增量。
- 事务轴 as-of（`as_of_recorded`，「兰台当时知道什么」）以 `created_at <= :as_of_recorded` 近似；完整语义需 checkpoint 回放，登记为 v2 边界（§0.2）。

### 3.3 时间窗过滤（time_from/time_to）与软/严格模式

| 参数 | 语义 | 默认 |
|---|---|---|
| `time_from` / `time_to` | 事件轴窗口 [time_from, time_to)：记忆命中当且仅当 E(event_time) 与窗口重叠 ∨ 有效期区间与窗口重叠；`temporal_unknown` 按 I4 软放行（窗口模式无「严格剔除」——用户显式给了窗口，未命中窗口本就不命中，fuzzy/unknown 仅降权并标注） | `None`（不激活） |
| `TEMPORAL_ASOF_STRICT` | **作用域仅 as-of 视图（§3.2），不约束时间窗模式**。`true`：as-of 下事件区间与有效期均未命中的条目剔除，但 `fuzzy` 与 `event_time IS NULL`（含 temporal_unknown）仍放行（I4），仅按乘子降权；`false`（默认）：未命中仅降权不剔除 | `false` |
| `TEMPORAL_FUZZY_PENALTY` | as-of/时间窗激活时，`fuzzy` 精度或 `temporal_unknown` 条目的分数乘子（fail-closed 校验 0~1，非正/越界回默认，沿 `_fail_closed` 纪律 `lantai/retrieval/hybrid.py:122-128`） | `0.8` |

读面哲学：**写侧宁 miss 不脏写，读侧宁多见不错删**——时间锚不确定的记忆软放行并如实标注，只有显式严格模式才硬过滤，且即便严格模式也不排除 `fuzzy`/`event_time IS NULL`（I4）。

### 3.4 打分与 explain 透明

- as-of/时间窗激活时：区间精确命中（`second`/`minute`/`hour`/`day` 且锚点落窗）加有界 boost（进 `RetrievalParams`，与 errsig bonus 同款有界加分模式）；`fuzzy`/`temporal_unknown` 乘 `TEMPORAL_FUZZY_PENALTY`。
- explain 增 `temporal` 分项（ADR-0008 溯源精神）：

```json
{
  "temporal": {
    "view": "current | as_of | window",
    "event_time": "2026-09-15T00:00:00+00:00",
    "event_time_precision": "day",
    "valid_from": "2026-09-01T00:00:00+00:00",
    "valid_to": null,
    "matched_by": "validity | event_interval | unknown_soft",
    "superseded_at_asof": false
  }
}
```

### 3.5 参数与 API 面

- `RetrievalParams`（frozen dataclass，`lantai/retrieval/hybrid.py:21-62`）增字段：`temporal_asof_strict`、`temporal_fuzzy_penalty`；`from_overrides` 映射表同步登记 settings 大写名。
- settings 增：`TEMPORAL_CURRENT_STRICT=false`、`TEMPORAL_ASOF_STRICT=false`、`TEMPORAL_FUZZY_PENALTY=0.8`。
- `POST /search`（`lantai/api/routes_search.py:13`）请求体增可选字段：`as_of`、`as_of_recorded`、`time_from`、`time_to`；全部缺省 = 现行行为逐字节不变。REST/MCP 透出的具体工单见 §7。

---

## 4. 迟到更正（late-arriving correction）机制

### 4.1 场景定义

迟到更正 = 新证据到达时（事务轴 t₂），其所描述的现实发生在更早时点（事件轴 t₀ < t₁ < t₂，t₁ 为旧值入库时刻）。三型分流：

| 型 | 判定 | 处置 | 与笔削的关系 |
|---|---|---|---|
| **替换型** supersede | 旧值在 [t₀ 前值, t₀) 曾为真，自 t₀ 起被新值取代（如「服务器从 9 月起换机房」） | 旧值 `valid_to = t₀`；新值 `valid_from = t₀`；写 supersedes 边；知命转 `SUPERSEDED` | 不撤回——旧值不是谎言，是过时 |
| **自始错误型** never_true | 旧主张从未为真 | 走笔削 retract（`status="retracted"`，三面 0 命中），**不**设 `valid_to` | ADR-0047 原语义 |
| **失效型** expire | 事实自然到期，无新值（如「签证 9 月 30 日到期」） | 旧值 `valid_to = 提取的到期时刻` | 无新值，无 supersedes 边 |

### 4.2 流程（复用冲突层，不另起炉灶）

```
新记忆 N 过闸 → 校雠/规则层判定与旧值 O 同主体冲突
  1) 落账：ConflictEvent(kind="override", status="open")   ← tables.py:351 预留值启用
     detail = {correction_type, old/new event_time 锚, recency_axis, valid_to_anchor, approximated}
  2) 裁决：ConflictEngine 六维评分（直断理由照常生成并入库）
     · 自动通过仅限既有 auto 通道；否则进锦囊/案牍人工裁决（人工闸门铁律不绕过，ADR-0010）
  3) 胜出执行（一个事务内）：
     a. MemoryEdge(N → O, "supersedes")                      ← edges.py:9 create_edge
     b. O.lifecycle_status=SUPERSEDED, superseded_by=N.id, superseded_at=utcnow()
        （事务轴如实：判定发生在今天）
     c. O.valid_to = 锚(N)，N.valid_from = 锚(N)（替换型）     ← 事件轴如实：现实失效点
        锚(N) = N.event_time ?? N.valid_from；两者皆无时用 N.created_at
        并在 detail 标 approximated=true
        **I3 钳制豁免（§2.5 I3 的迟到更正例外）**：若 锚(N) < O.valid_from
        （典型形态：O 的 valid_from 系回填 created_at，而 N 携带更早的事件时间），
        则 O.valid_to 钳制为 O.valid_from，detail 额外记 {clamped: true,
        anchor_raw: 锚(N)}——拒绝该写入会让最常见的迟到更正形态无法落库（I3 自锁）；
        钳制是如实近似：账本留原始锚点，宁近似不静默。
     d. MemoryCheckpoint 快照照常留底
```

### 4.3 直断 recency 维的时间轴修正

现状缺陷：`ConflictEngine` 的 recency（权重 0.15）按 `created_at` 计算（`lantai/cognition/conflicts.py:91-98`、`conflicts.py:133-141`）——迟到更正里新值 `created_at` 必然更新，等于**奖励迟到本身**；反之若旧值才是「当下仍为真的稳态事实」，会因入库早被冤杀。

修正：recency 基准改为**优先 `event_time`，无则回退 `created_at`**；所用时间轴记入 `DecisionTrace.score_components_*["recency_axis"] = "event" | "created"`（`lantai/cognition/conflicts.py:22-33` 的 trace 结构直接承载）。这正是 iter-11 缺口 3 的要求：「迟到更正由冲突层裁决（保留直断理由），不按最近写入机械取胜」。

### 4.4 一致性验收用例（确定性，不 mock）

| 用例 | 步骤 | 断言 |
|---|---|---|
| U1 as-of 历史 | O（9-01 入库，无 event_time）被 N（event_time=9-15）替换更正 | `as_of=9-10`：O 命中（valid_to=9-15 > 9-10）且不被 supersedes 降权；`as_of=9-20`：仅 N 命中 |
| U2 双轴不混淆 | 「昨天说我用 Postgres」今入库为 N（event_time=昨天） | 当前态：N 正常召回；事务轴 `created_at` 仍为今天——不因 event_time 改写入库时间 |
| U3 不猜 | gate 提取时间失败 | `event_time IS NULL`、precision=""、provenance 标注；当前态召回不受影响（I4） |
| U4 自始错误 | never_true 判定胜出 | retract 后当前态与 as-of 双视图 0 命中（I6） |
| U5 回填等价 | 迁移后抽查存量行 | `valid_from == created_at`；当前态检索结果与迁移前逐条一致 |

评测阈值沿用调研口径：**当前/历史证据选择正确率 ≥ 90%**（E2 时间用例，iter-11.md:17），用例落 `lantai/eval/chinese_memory_cases.py` 既有种子风格（该文件已含 `valid_from_days` 系列用例，`chinese_memory_cases.py:309+`）。

---

## 5. Schema 变更与数据迁移

### 5.1 模型变更汇总（`lantai/models/tables.py` MemoryItem）

- 新增：`event_time`（索引）、`event_time_precision`（§2.2 代码块）。
- 改写：`valid_from` 加 `default_factory=utcnow` 与索引；`valid_to` 加索引。
- 类型注解决策：四字段注解**均保留 `datetime | None`**（列物理可空）——理由见 §5.3；「`valid_from` 语义必填」以不变式 I2 + 落锚测试固化，不靠列约束。

### 5.2 迁移链 v20 → v21（`lantai/storage/db.py` 续接）

```python
# v20 -> v21（ADR-0048 更漏）：event_time 双时间轴 + valid_from 回填 + 时效索引
if user_version < 21:
    if not _has_column(conn, "memoryitem", "event_time"):
        conn.execute("ALTER TABLE memoryitem ADD COLUMN event_time DATETIME")
    if not _has_column(conn, "memoryitem", "event_time_precision"):
        conn.execute(
            "ALTER TABLE memoryitem ADD COLUMN event_time_precision TEXT DEFAULT ''"
        )
    # DML 常量字面量回填（无外部输入，无注入面；UPDATE 语义见 §5.3）
    conn.execute(
        "UPDATE memoryitem SET valid_from = created_at WHERE valid_from IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_memoryitem_event_time "
        "ON memoryitem (event_time)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_memoryitem_valid_from "
        "ON memoryitem (valid_from)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_memoryitem_valid_to ON memoryitem (valid_to)"
    )
    conn.execute("PRAGMA user_version = 21")
    conn.commit()
    logger.info("数据库增量迁移 v21 完成（更漏双时间轴）")
```

- DDL 固定字面量（SQLite ALTER TABLE 不支持绑定参数）、`_has_column` 幂等、异常只记日志不阻断启动——全部沿现行迁移链纪律（`lantai/storage/db.py:22-41`、`db.py:44-45`）。若实施时链尾版本已前移（当前链尾 v20，`db.py:386-408`），版本号顺延、结构不变。
- 索引三枚皆为 `CREATE INDEX IF NOT EXISTS`，幂等可重放。

### 5.3 回填语义论证与「物理可空」取舍

- **为何回填 `valid_from = created_at` 是安全的**：当前态视图下 `created_at ≤ now` 恒真，行为与 NULL（无约束）完全等价，零回归；as-of 视图下，「任何匹配 = 有效期命中 ∨ 事件区间命中 ∨ unknown 软放行」（§3.2），回填只影响「有效期」这一分支，无法制造新的误排除——无 event_time 的存量行落入 `temporal_unknown` 软放行而非被 `valid_from > as_of` 冤杀。回填的收益是满足不变式 I2 与索引选择性。
- **为何列维持可空、注解保留 `| None`**：① SQLite 无法对既有列补 `NOT NULL`，重建表风险远大于收益（宁 miss 不脏写）；② 评测种子显式传 `valid_from=None`（`lantai/eval/forgetting_quality.py:112-115`），列约束会打灭既有评测；③ 「语义必填」由服务层默认值 + I2 落锚测试保证，约束强度够用。实施票 A 须在 PR 描述里如实记录此取舍。

---

## 6. 测试纪律（不 mock 冒烟）

按 AGENTS.md 测试纪律，以下核心函数新增/修改必须配不 mock 冒烟测试（真实构造最小输入直调）：

| 函数 | 冒烟点 |
|---|---|
| 迁移 `apply_migrations` v21 分支 | 旧库 fixture（含 NULL valid_from 行）→ 迁移 → 断言列/索引/回填（U5） |
| as-of/窗口过滤（hybrid 内新纯函数） | 真实 MemoryItem 构造 + 逐精度区间断言 + I4 |
| `_chronos_filter` 回归 | 改动后现有语义逐条复验（未到剔除/过期 0.3）零漂移 |
| 迟到更正流程（§4.2 step 3） | 真实建 O、N、边、账本行，断言三轴字段各归其位（U1） |
| `ConflictEngine.resolve` recency 修正 | 同一冲突对，event_time 有/无两分支，断言 `recency_axis` |
| I1/I3 写入校验 | 违例构造被拒（不静默修正） |

评测（可 LLM 参与，不属冒烟）：E2 时间用例 ≥90%（§4.4）。

---

## 7. 实施票据拆分（建议顺序）

| 票 | 内容 | 依赖 |
|---|---|---|
| A | Schema + 迁移 v21（tables.py + db.py + 回填 + 索引 + I1/I2/I5 落锚） | 无 |
| B | 写入侧提取纪律（gate/fastpath event_time/valid_from 低置信不猜 + provenance 标注 + U3） | A |
| C | 检索双视图（hybrid as_of/time_from/time_to + RetrievalParams + settings + explain temporal + `/search` 增参 + U1/U2 + `_chronos_filter` 回归） | A |
| D | 迟到更正闭环（ConflictEvent kind=override detail 契约 + supersedes 边 + 知命转移 + valid_to 锚定 + 直断 recency_axis + U4） | A，可与 C 并行 |
| E | 评测 E2 时间用例 ≥90% + MCP 工具面透出（`search` 增 as_of 参数）+ 文档收口 | C、D |

---

## 8. 相关

- [ADR-0048](../adr/0048-genglou-bitemporal-event-time.md) — 本规范的决策记录
- [ADR-0013](../adr/0013-naming-system.md) — 更漏命名出处与登记规则
- [ADR-0010](../adr/0010-conflict-resolution-layer.md) — 冲突账本与直断（本规范 §4 的承重结构）
- [ADR-0046](../adr/0046-echo-suppression-time-window.md) — 既有时间窗（会话回声域，与事件轴正交）
- [ADR-0047](../adr/0047-bixiao-fourway-record-lifecycle.md) — 笔削四分法（自始错误型的出口）
- 调研：`docs/research/memory-landscape-2026-09/iterations/iter-07.md`（Q1 核验）、`iter-11.md`（5 分项分解、D20/D21）
