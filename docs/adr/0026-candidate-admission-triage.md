# ADR-0026: 沙汰——候选入队口径信噪分离

**日期**: 2026-08-28
**状态**: Accepted
**决策者**: 大哥
**来源**: 生产库实测：候选管道净损失（690 候选 = 447 rejected + 165 pending +
75 gated；pending 置信度均值 0.25；memoryitem 10 天零新增；TTL 每日静默毙掉
72–142 条）

## 背景

当前 `ingest_dialogue` 对两类候选一律入待审队列（`pending_review`）：

1. 闲聊（`_is_chitchat` → `status=pending_review`）："好的好的" / "嗯嗯" /
   "再见" 等纯社交结束语，置信度 0.0，provenance=dialogue-chitchat；
2. 低置信度 LLM 提取（`extractor_confidence < 0.55` → `enqueue_rejected`）：
   均值 0.25，最大值 0.30。

实测 165 条 pending 全部置信度 ≤0.3，其中约 40% 为 chitchat（conf=0.0）。
TTL 7 天自动归档后每日净毙 72–142 条。队列是噪音不是信号。

## 决策

| 项 | 决策 |
|----|------|
| 闲聊 | 不再进待审队列——直接落 `status=rejected`（provenance 仍为 dialogue-chitchat，可追溯审计）。闲聊不可能是有效记忆，且用户从未 review 过任何一条 |
| 低置信度 | 新增 `CANDIDATE_MIN_CONFIDENCE` 设置（默认 0.0，即不改变行为），低于该值的候选直接 `status=rejected`（不入待审队列）。默认值 0.0 为保守选择——待真实数据积累后再校准；初始校准建议值 0.15 |
| 置信度天花板 | 低置信度但仍高于地板的候选（0.15–0.55）仍入待审队列（现行行为不变），保留用户裁决权 |
| 命名 | 「沙汰」——取自《世说新语》「沙汰」= 淘洗淘汰，指候选入队前先筛掉明显噪音，信噪分离 |

## 理由

- 闲聊（conf=0.0）从未被 review 过（7 天 TTL 零人工裁决），白白占用队列与
  日报噪音；
- 宁 miss 不脏写：不自动修正低置信度候选，只在入队前加一道信噪门——低于地板
  的仍落库（status=rejected，可审计），只是不再排队等用户裁决；
- 默认值 0.0 保证零行为变化——沙汰是可选门，待真实数据校准后才激活。

## 影响

- `lantai/ingestion/dialogue.py`：闲聊路径从 `status=pending_review` 改为
  `status=rejected`；低置信度路径增加 `CANDIDATE_MIN_CONFIDENCE` 地板检查。
- `lantai/core/settings.py`：`CANDIDATE_MIN_CONFIDENCE: float = 0.0`
- 测试：`test_provenance.py::test_dialogue_chitchat_candidate_carries_provenance`
  断言 `status=rejected`（原 `pending_review`）；新增 min_confidence 地板测试。
- 生产库历史 chitchat 候选（status=pending_review, conf=0.0）不受影响，
  由 TTL 自然归档。

## 相关

- [ADR-0002](0002-zero-hardcoding.md) — 阈值入 settings
- [Ticket 02](../.scratch/field-research/issues/02-conflict-rules.md) — 候选队列
  pending_review 语义
- [CONTEXT.md](../../CONTEXT.md) — 沙汰词汇登记
