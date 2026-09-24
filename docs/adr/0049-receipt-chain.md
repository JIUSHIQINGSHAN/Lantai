# ADR-0049: 回执链——注入回执一等化（receipt chain）

**日期**: 2026-09-25
**状态**: Accepted
**决策者**: 大哥
**来源**: roadmap-v2 P0-4（`docs/research/memory-landscape-2026-09/roadmap-v2.md:17`）+ iter-13（synthesis §五.3：注入回执为「全场无人区」）；实施票据 `.scratch/roadmap-v2-execution/issues/04-p0-receipt-chain.md`

---

## 背景

兰台治理四原语（Governed Shared Memory）中的 policy propagation 缺最后一环：检索事件（`RetrievalEvent`，票 02 雏形）记录了「召回了什么」，`used_ids` 弱标注（`backfill_used_ids`）记录了「宿主说用了什么」，但：

1. **回执失败纯静默**：插件 `_call_backfill` 失败只写本地日志，服务端无法区分「宿主没回执」与「回执丢了」；
2. **回执缺失不可观测**：无回执状态字段，事件永远停在「无 used_ids」——不可统计、不可审计；
3. **request 无承载**：一次注入调用（一次 context 请求）无整体标识，无法把「一次注入的多条 evidence」对账为整体；
4. **无可追溯率出口**：治理叙事「每条被注入的记忆都能回溯到哪个检索事件」没有数字。

## 决策

伞名**回执链**（契约图：检索事件 → 注入 evidence → 宿主回执 → 可追溯出口）：

### 1. 链上五个标识（并列不替代）

| 标识 | 粒度 | 生成方 | 承载 |
|---|---|---|---|
| `request_id` | 一次注入调用 | 注入侧（shell_hook `build_context`，`new_id("req")`） | `RetrievalEvent.request_id` + 注入响应体 |
| `event_id` | 一次检索事件 | 服务端（`new_id("rev")`） | `RetrievalEvent.id` + 响应体 |
| `session_id` | 会话 | 宿主透传 | 既有列（v022 票 05） |
| `turn` | 会话内轮次 | 宿主透传 | 写线 `provenance.origin_turn`（既有） |
| `memory_id` | 被注入记忆 | 服务端 | `result_ids`（召回面）/ `used_ids`（采用面） |

### 2. 回执状态机（`RetrievalEvent.receipt_status`）

```
pending（事件落库默认） ──backfill──▶ acked（receipt_at=回执时刻）
        └──── 超龄未回执（mark_missed_receipts，默认 300s）──▶ missed（receipt_at=判定时刻）
```

- **missed 是事实不是错误**（宁 miss 不脏写）：置位操作幂等、失败只记日志不入主链路；不承诺宿主必回执。
- 判定入口：`mark_missed_receipts(timeout_seconds)` 惰性批量（worker/统计前调用均可），不引入常驻线程。
- 状态迁移不可逆：acked 不回退 pending（回执是事实）；missed 后迟到的 backfill 仍按整体覆盖语义写入 used_ids 并置 acked（迟到回执是更好的事实）。

### 3. Schema 变更（`RetrievalEvent`）

```python
request_id: str | None      # 索引；一次注入调用标识
receipt_status: str         # "pending"/"acked"/"missed"，默认 "pending"，索引
receipt_at: datetime | None # 回执状态落定时刻
```

迁移 `db.py` 链 **v21 → v22**（表存在守卫 + `_has_column` 幂等 + 两枚索引，纪律同 v21）。

### 4. 协议变更（shell_hook NDJSON，向后兼容）

- `context` 响应增 `request_id` 字段（与既有 `event_id`/`evidence` 并列）；
- `backfill` 请求增可选 `request_id` 字段：服务端与事件列核对，**不一致时记日志但不拒绝**（回执归属以 `event_id` 为准，宁 miss 不脏写）；
- 老宿主不传 `request_id` 行为不变（旧字段全兼容）。

### 5. 可追溯率出口

`receipt_traceability_report()`：`{pending, acked, acked_with_used_ids, missed, traceable, traceability_rate}`。口径：**acked 且 used_ids 非空的事件中，每个 used_id 均回溯到现存 MemoryItem 行的比例**；无 acked 样本时 rate 返回 `None`（不编造）。`scripts/receipt_report.py` CLI 输出（供票 06 宿主冒烟断言 100% 与自证复用）。

## 边界（如实声明，不夸口）

- **回执是弱事实**：宿主声称用了不等于真用了——链证明「宿主确认收到并采用」，不证明因果贡献（贡献归因属 P2-1 影子学习）。
- **不按回执调权**：回执/可追溯率不参与检索打分与晋升（roadmap 不做清单）。
- **missed 判定是惰性批量**：不实时；统计前未跑 `mark_missed_receipts` 时超龄事件仍显示 pending（如实）。
- **多宿主泛化不在本票**（票 06）：本票只定契约 + Hermes/shell_hook 受控宿主冒烟。
- **宿主内已注入副本不可追溯清除**（ADR-0047 边界延续）。

## 后果

- 「来源可追溯率 100%」从叙事变成可测数字（受控宿主冒烟断言），补齐治理四原语的 policy propagation 本地闭环。
- 回执缺失从不可见变成 missed 桶计数——治理审计可回答「多少注入没有回执」。
- 成本：RetrievalEvent 三列两索引（毫秒级）；注入响应多一个字段；无新常驻进程。

## 相关

- [ADR-0047](0047-bixiao-fourway-record-lifecycle.md) — 笔削（撤回传播的姊妹原语）
- [ADR-0048](0048-genglou-bitemporal-event-time.md) — 更漏（本 ADR 迁移链续接 v21→v22）
- `lantai/observability/retrieval_log.py` — 回执服务实现（acked/missed/可追溯率）
- `scripts/shell_hook.py` — NDJSON 协议（request_id 贯通）
