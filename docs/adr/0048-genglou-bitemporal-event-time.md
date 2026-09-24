# ADR-0048: 更漏——双时间轴事件时间模型（bi-temporal event time）

**日期**: 2026-09-25
**状态**: Accepted
**决策者**: 大哥
**来源**: 记忆全景调研 2026-09 主题综合（`docs/research/memory-landscape-2026-09/iterations/iter-11.md`，Zep/Graphiti 时间感知 5.0 为全场唯一满分项）+ 内部代码核验（`iter-07.md` Q1）；技术规范见 [docs/plans/p1-event-time-schema-spec.md](../plans/p1-event-time-schema-spec.md)

---

## 背景

调研把「时间感知与冲突消解」单列维度评分：Zep/Graphiti 得 5.0，是全部 20 系统中唯一满分——其 bi-temporal（`valid_at`/`invalid_at`）为显式设计，facts 自动失效而非删除，查询 `invalid_at IS NULL` 取当前视图，是唯一把「事实有效期」做成一等公民的平台（iter-11.md:7）。兰台同维度仅 3.0：**事件时间/有效时间未入库**（iter-11.md:10）。

内部核验把缺口坐实（iter-07.md:12）：`MemoryItem` 只有事务性时间戳（`created_at`/`updated_at`，`lantai/models/tables.py:143-144`）；`valid_from`/`valid_to` 虽已建列但无语义、无索引、写入侧近乎不用（`lantai/models/tables.py:141-142`）；supersedes 仅是 `MemoryEdge.relation` 的一个取值（`tables.py:267`）。旧文档宣称的「Chronos 双时间轴」被证伪。直接后果有三：

1. 「昨天说我用 Postgres」与「今天还在用 Postgres」在库内不可区分——检索无法回答「现实时点 T 哪些为真」；
2. 迟到更正无事件轴锚点：直断 recency 维按 `created_at` 打分（`lantai/cognition/conflicts.py:91-98`），机械奖励最近写入；
3. 治理四原语（Governed Shared Memory）中 temporal supersession 是兰台唯一数据级缺失项（决策 D20：升 P1 首位）。

## 决策

伞名**更漏**（《周礼》铜壶滴漏计时意象，ADR-0013 候选转正，已登记 `CONTEXT.md` 词汇表），确立双时间轴模型：

1. **两条轴、互不回写**：事务轴（`created_at`/`updated_at`/`version`/`MemoryCheckpoint`）回答「兰台何时知道」；事件轴（`event_time` ± `event_time_precision`，`valid_from`/`valid_to`）回答「现实何时发生/何时为真」。迟到更正今天判定，`superseded_at` 如实记今天（事务轴），旧值 `valid_to` 锚定新值的现实起点（事件轴）——两轴各记各轴，不伪造。
2. **Schema 四字段**：`event_time: datetime | None`（NULL=未提取到，宁 miss 不猜）；`event_time_precision: str`（`year/month/day/hour/minute/second/fuzzy`，非空 ⇔ event_time 非空，读取侧未知值按 fuzzy 最宽解释）；`valid_from: datetime`（语义必填，服务层 `default_factory=utcnow` 近似 + 存量回填）；`valid_to: datetime | None`（NULL=未失效，即兰台版 `invalid_at IS NULL` 当前态语义）。
3. **检索双视图**：当前态视图（current view）保持 `_chronos_filter` 现行语义不变（未到剔除、过期 0.3 衰减，零回归）；历史时点视图（as-of query）显式 `as_of` 参数触发，命中 = 有效期命中 ∨ 事件区间命中 ∨ unknown 软放行；时间窗 `time_from/time_to` 走事件轴重叠。读面哲学：**写侧宁 miss 不脏写，读侧宁多见不错删**——`fuzzy` 与 `event_time IS NULL` 永不因时间条件被硬排除。
4. **迟到更正三型分流**：替换型（supersede：旧值 `valid_to=锚点`、新值 `valid_from=锚点`、supersedes 边、知命转 `SUPERSEDED`）走冲突账本 `ConflictEvent(kind="override")` 落账 + 直断裁决；自始错误型走笔削 retract（ADR-0047，不设 `valid_to`——曾为真与从未为真语义分离）；失效型仅回写 `valid_to`。直断 recency 基准改为**优先 event_time、无则回退 created_at**，`recency_axis` 入 DecisionTrace——「不按最近写入机械取胜」（iter-11 缺口 3 原话）。
5. **as-of 下被取代旧值不降权**：检索期 supersedes 压序是「当下取向」；as-of 视图按 `valid_to` 重新判定——`valid_to > as_of` 的旧值在 T 时点本就是真实记录。这是双时间轴相对现状的核心增量。

## Schema 变更

`lantai/models/tables.py` MemoryItem：

```python
event_time: datetime | None = Field(default=None, index=True)
event_time_precision: str = Field(default="")            # year/month/day/hour/minute/second/fuzzy
valid_from: datetime | None = Field(default_factory=utcnow, index=True)
valid_to: datetime | None = Field(default=None, index=True)
```

- 类型注解保留 `datetime | None`（列物理可空），「`valid_from` 语义必填」以服务层默认值 + 不变式落锚测试固化——SQLite 无法对既有列补 `NOT NULL`，且评测种子显式传 `None`（`lantai/eval/forgetting_quality.py:112-115`），重建表的破坏面远大于收益。
- 时区纪律：全部 UTC，naive 按 UTC 解释（沿 `_chronos_filter`/`ConflictEngine` 现行做法）。
- 精度 → 不确定性区间映射（year=[当年,次年)、…、second=闭点特判、fuzzy=±1d 定宽）与 explain `temporal` 分项定义见规范 §2.3/§3.4。
- 检索参数：`RetrievalParams` 增 `temporal_asof_strict`/`temporal_fuzzy_penalty`；settings 增 `TEMPORAL_CURRENT_STRICT=false`、`TEMPORAL_ASOF_STRICT=false`、`TEMPORAL_FUZZY_PENALTY=0.8`；`POST /search` 增可选 `as_of`/`as_of_recorded`/`time_from`/`time_to`，缺省时行为逐字节不变。

## 数据迁移

`lantai/storage/db.py` 版本化迁移链续接 **v20 → v21**（现行链尾 v20，`db.py:386-408`）：

1. `ALTER TABLE memoryitem ADD COLUMN event_time DATETIME` / `ADD COLUMN event_time_precision TEXT DEFAULT ''`（`_has_column` 幂等守卫 + DDL 固定字面量——SQLite ALTER TABLE 不支持绑定参数，防注入纪律不变）。
2. 回填 `UPDATE memoryitem SET valid_from = created_at WHERE valid_from IS NULL`（DML 常量字面量，无外部输入无注入面）。安全性论证：当前态下 `created_at ≤ now` 恒真，行为与 NULL 等价，零回归；as-of 的「any-of + unknown 软放行」判定使回填无法制造新的误排除——无 event_time 的存量行落入 `temporal_unknown` 软放行而非被 `valid_from > as_of` 冤杀。
3. `CREATE INDEX IF NOT EXISTS` 三枚：`ix_memoryitem_event_time` / `ix_memoryitem_valid_from` / `ix_memoryitem_valid_to`。
4. `PRAGMA user_version = 21`；沿用「异常只记日志不阻断启动」的链纪律；实施时若链尾已前移则版本号顺延。

## 边界（如实声明，不夸口）

- **不做图数据库**：MemoryEdge 不迁移 Neo4j/FalkorDB，不引入图库运维成本（D21，ops_cost 是 Zep 的已知负资产）。
- **不做全量 Graphiti 式事件抽取**：event_time 提取挂在既有 gate/fastpath，低置信不猜（宁 miss 不脏写），中文相对时间表达式解析器另票。
- **事务轴 as-of 是近似**：`as_of_recorded` 用 `created_at` 过滤近似「兰台当时知道什么」；完整 system-time as-of 需 append-only 版本化，本波以 `MemoryCheckpoint` 快照留底、查询回放留 v2。
- **`fuzzy`/NULL 不参与硬过滤**是刻意取舍：时间锚不确定的记忆在 as-of/窗口下软放行降权并 explain 标注，宁可多见不错删；严格模式（`TEMPORAL_ASOF_STRICT=true`）也不排除这两类（不变式 I4）。
- **当前态默认不收紧**：已过 `valid_to` 仍 0.3 衰减保留召回（现状行为），`TEMPORAL_CURRENT_STRICT` 默认关，行为收紧须独立票验证后再开。
- **笔削面不重做**：retracted 全检索面 0 命中口径（ADR-0047）在双视图下复验而非重实现。
- **派生物**：场景摘要/星图边不携带事件时间语义，记入已知限制。

## 后果

- 兰台补齐 temporal supersession 的数据级地基：`event_time`/`valid_from`/`valid_to` 使「what changed when」可查询、可验证，对齐调研唯一的 5 分项能力而不引入其运维负担。
- 迟到更正从「最近写入机械取胜」升级为「事件轴锚定 + 直断留痕」：账本 detail 带三型分流与 `recency_axis`，裁决可解释可审计（ADR-0010 精神延续）。
- 评测新增确定性口径：as-of 当前/历史证据选择正确率 ≥ 90%（E2，阈值沿用 2026-09-18 调研§五），迟到更正、双轴不混淆、回填等价三组不 mock 冒烟用例落锚。
- 成本：MemoryItem 增两列三索引——`event_time`/`event_time_precision` 为新增列，`valid_from`/`valid_to` 为既有列改写（加 `default_factory` 与索引，SQLite ADD COLUMN 毫秒级）；检索主路径 as-of/窗口缺省不激活，现行行为零漂移；写入侧 gate 增一次时间提取尝试（失败不阻断）。
- 实施票据 A–E 拆分见规范 §7；命名登记（CONTEXT.md「更漏」条目 + ADR-0013 候选转正）已随本 ADR 完成。

## 相关

- [docs/plans/p1-event-time-schema-spec.md](../plans/p1-event-time-schema-spec.md) — 技术规范（字段/视图/更正/迁移全量定义）
- [ADR-0013](0013-naming-system.md) — 更漏命名出处（铜壶滴漏）与登记规则 R4
- [ADR-0010](0010-conflict-resolution-layer.md) — 冲突账本与直断（迟到更正的承重结构）
- [ADR-0047](0047-bixiao-fourway-record-lifecycle.md) — 笔削四分法（自始错误型的出口）
- [ADR-0046](0046-echo-suppression-time-window.md) — 既有会话回声时间窗（与事件轴正交，不互替）
- 调研：`docs/research/memory-landscape-2026-09/iterations/iter-07.md`、`iter-11.md`
