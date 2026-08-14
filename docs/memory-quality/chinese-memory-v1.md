# 兰台记忆 中文记忆评测集（chinese-memory-v1 → v2）

## v2 扩编（2026-08-14）：13 → 50 case

- 数据源：`lantai/eval/chinese_memory_cases.py`（纯数据，无副作用），dataset 名 `chinese-memory-v2`
- 规模：**50 case** = typo×15 / fresh×12 / stale×8 / temporal×8 / superseded×7
- 错别字 case 统一「去首字」模式：query 的全部 trigram 均为 content 的 trigram，
  FTS AND 链确定性命中（FTS 兜底路径可复现）
- 陈旧 case 按 lane 半衰期保证归档（chat 90 天 / preference 200 天，decay 必达阈值）
- 门禁（`GATES`）不变：stale=0 / typo=1 / fresh=1 / temporal=1 / superseded=1，
  `scripts/run_forgetting_quality.py --check` 实测 PASS
- **纳入 CI**：`.github/workflows/tests.yml`（push/PR：全量 pytest + 门禁）
- v1 规格与六维指标定义见下（保持不变）

---

> 状态：v1 发布（2026-08-11）
> 定位：面向「中文 / 错别字容错 / 遗忘质量 / 时效」的记忆检索自证基准——英文生态
> 基准（LoCoMo / LongMemEval / BEAM）不覆盖此场景，且厂商分数互相打架、不可复现
> （Mem0 vs Letta 公开争议、LoCoMo 答案键被审计出错误），本项目以「本地可复现命令」
> 作为主张依据。

## 评测集规格

- 数据集：`lantai/eval/chinese_memory_cases.py`（纯数据，无副作用）
- 规模：13 case = typo×4 / fresh×3 / stale×2 / temporal×2 / superseded×2
- 命名空间：`eval_fq`（种子与关系边随评测结束自动清理，不污染真实库）
- 查询设计约束：query 与目标内容共享 ≥1 个 trigram 子串——FTS5 trigram 是整串
  子串匹配（非单字容错），词边界型错字在向量不可用时仍确定性可测

## 六维指标

| 指标 | 含义 | 方向 |
|---|---|---|
| stale_hit_rate | 已归档记忆仍被召回（Ebbinghaus 归档失效） | 越低越好 |
| typo_recall_rate | 中文错别字容错命中（FTS trigram 兜底） | 越高越好 |
| fresh_recall_rate | 对照组召回（管道自检，应≈1） | 越高越好 |
| temporal_order_accuracy | Chronos 双时间轴：未生效过滤 / 过期降权后新值在前 | 越高越好 |
| superseded_order_accuracy | 矛盾取代：supersedes 边降权后新值在前 | 越高越好 |
| superseded_residual_rate | 被取代旧值残留 top-k（降权不删旧值，诚实测量） | 越低越好 |

## 实测结果（FTS 兜底路径 = 最严格基准）

| 指标 | 值 |
|---|---|
| stale_hit_rate | 0.0 |
| typo_recall_rate | 1.0 |
| fresh_recall_rate | 1.0 |
| temporal_order_accuracy | 1.0 |
| superseded_order_accuracy | 1.0 |
| superseded_residual_rate | 1.0（诚实报告：旧值保留在新值之后） |

报告留档：`docs/memory-quality/2026-08-11.md`（含逐条明细）。

## 复现（两条命令）

```bash
# 门禁模式：离线临时库 + 仅外部依赖 mock，断言指标门槛，FAIL 退出码 1
python scripts/run_forgetting_quality.py --check

# 完整报告（真实检索；LLM 意图 + embedding 可用时）
python scripts/run_forgetting_quality.py --intent rule --out docs/memory-quality
```

确定性：同一代码 + 数据集，FTS 兜底路径 3 轮连跑指标一致（superseded 排序
自 0.5 → 1.0 的修复见 `9cda3dd`，`_apply_supersedes_order` 降权实现）。

## 诚实原则与边界

- 指标为诚实测量：无数据返回 0.0，绝不编造；残留类指标如实报告检索层缺口。
- 「宁 miss 不脏写」：修复遵循人工闸门裁决，不自动删旧值 / 不自动改写。
- 已知边界：本报告为 FTS 兜底下界；向量路径启用后复跑，期望
  `superseded_residual_rate` 进一步下降（embedding 可区分新旧值）。
- 扩展路线：case 数扩至 50+（覆盖多轮对话 / 时间区间 / 跨命名空间），纳入 CI。