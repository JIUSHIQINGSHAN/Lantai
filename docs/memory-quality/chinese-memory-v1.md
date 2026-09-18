# 兰台记忆 中文记忆评测集（chinese-memory-v2）

> **规格事实源 = 代码**：case 清单/分布/门禁阈值以 `lantai/eval/chinese_memory_cases.py`
> 与 `lantai/eval/offline.py::GATES` 为准；本文件的数字由
> `tests/test_memory_quality_spec.py` 逐项钉死（任何 case 增删/改名都会使测试失败，
> 直到本文同步更新——防漂移锁定，宁 miss 不脏写）。

## 规格（v4，2026-09-19 起）

- 数据集：`lantai/eval/chinese_memory_cases.py`（纯数据，无副作用），dataset 名 `chinese-memory-v2`
- 规模：**94 case** = typo×23 / typo_mid×6 / paraphrase×8 / fresh×18 / stale×14 / temporal×13 / superseded×12
- 命名空间：`eval_fq`（种子与关系边随评测结束自动清理，不污染真实库）
- 查询设计约束：query 与目标内容共享 ≥1 个 trigram 子串——FTS5 trigram 是整串
  子串匹配（非单字容错），词边界型错字在向量不可用时仍确定性可测；错别字 case 统一
  「去首字」模式（query 的全部 trigram 均为 content 的 trigram，FTS AND 链确定命中）。
  paraphrase（同义改写）与 typo_mid（词中错字）**不满足** AND 链约束——它们是
  报告型类别（v4 起加入，P0 票05），诚实测量 BM25 兜底泛化边界（2026-09-19
  基线：离线 FTS-only 下两者均为 0——整句 AND 匹配不覆盖泛化；改进方向
  FTS OR 兜底见 `.scratch/p0-reliability-host-loop/issues/06-fts-or-fallback.md`）
- 陈旧 case 按 lane 半衰期保证归档（chat 90 天 / preference 200 天，decay 必达阈值）
- 门禁（`GATES` 不变，从 v1 沿用）：stale=0 / typo=1 / fresh=1 / temporal=1 / superseded=1；
  `superseded_residual_rate` / `typo_mid_recall_rate` / `paraphrase_recall_rate`
  为诚实测量，只报告不设门槛
- 回答层（v4 起，LongMemEval 式分层）：case 带 `key_points` 时由 `--judge`
  （rule 确定性 / llm 选配）打要点命中率，`overall_hit_rate` 与召回层分开呈现，
  不做单变量混合分
- **CI**：`.github/workflows/tests.yml`（push/PR：全量 pytest + 门禁）

## 指标

| 指标 | 含义 | 方向 |
|---|---|---|
| stale_hit_rate | 已归档记忆仍被召回（Ebbinghaus 归档失效） | 越低越好 |
| typo_recall_rate | 中文错别字容错命中（FTS trigram 兜底） | 越高越好 |
| typo_mid_recall_rate | 词中错字命中（向量层兜底为主；离线 FTS-only 诚实测量） | 越高越好 |
| paraphrase_recall_rate | 同义改写召回（离线 FTS-only 诚实测量） | 越高越好 |
| fresh_recall_rate | 对照组召回（管道自检，应≈1） | 越高越好 |
| temporal_order_accuracy | Chronos 双时间轴：未生效过滤 / 过期降权后新值在前 | 越高越好 |
| superseded_order_accuracy | 矛盾取代：supersedes 边降权后新值在前 | 越高越好 |
| superseded_residual_rate | 被取代旧值残留 top-k（降权不删旧值，诚实测量） | 越低越好 |

## 复现（两条命令）

```bash
# 门禁模式：离线临时库 + 仅外部依赖 mock，断言指标门槛，FAIL 退出码 1
python scripts/run_forgetting_quality.py --check

# 完整报告（真实检索；LLM 意图 + embedding 可用时）
python scripts/run_forgetting_quality.py --intent rule --out docs/memory-quality
```

确定性：同一代码 + 数据集，FTS 兜底路径连跑指标一致（superseded 排序
自 0.5 → 1.0 的修复见 `9cda3dd`，`_apply_supersedes_order` 降权实现）。

## 诚实原则与边界

- 指标为诚实测量：无数据返回 0.0，绝不编造；残留类指标如实报告检索层缺口。
- 「宁 miss 不脏写」：修复遵循人工闸门裁决，不自动删旧值 / 不自动改写。
- 已知边界：报告为 FTS 兜底下界；向量路径启用后复跑，期望
  `superseded_residual_rate` 进一步下降（embedding 可区分新旧值）。
- 扩展路线：case 数继续扩编（覆盖多轮对话 / 时间区间 / 跨命名空间）。

## 版本沿革

| 版本 | 日期 | 规模 | 说明 |
|---|---|---|---|
| v1 | 2026-08-11 | 13 case = typo×4 / fresh×3 / stale×2 / temporal×2 / superseded×2 | 首发（发布稿） |
| v2 | 2026-08-14 | 50 case = typo×15 / fresh×12 / stale×8 / temporal×8 / superseded×7 | 全维度扩编 + 纳入 CI |
| v3 | 2026-08-15 | 80 case（见上） | 5 类内扩至 80，不动 GATES/runner；规格以本文为准 |
| v4 | 2026-09-19 | 94 case = +paraphrase×8（带 key_points）/ +typo_mid×6 | 召回/回答分层（P0 票05）；两项新类别只报告不设门 |
