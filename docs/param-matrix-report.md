# 调参对比矩阵报告 v0 — 权重敏感度实证分析

> 日期：2026-08-08 | 数据：生产库 `remembrance-data` 已有 5 组 eval_run（dry-run-v1，179 样本）
> 方法：位置敏感指标分析（Jaccard 集合指标的盲区补充）
> 说明：本报告基于**已跑过的历史 run** 做静态分析，不新增 API 调用；扩展矩阵见 `scripts/run_param_matrix.py`

## 一、为什么补位置敏感指标

dry-run 报告 v1 用 `jaccard_vs_baseline=1.0` 得出"权重不敏感，无区分度"结论。

**该结论不精确**：`jaccard_overlap()` 用 `set()` 比较，**忽略排序变化**。同一组记忆只要集合不变，即使顺序全换，jaccard 仍是 1.0。调参改变的是排序优先级，恰恰在 jaccard 的盲区里。

实证：默认权重 vs `W_VECTOR=0.75/W_BM25=0.15` 两组 run（同为 179 样本，no-rerank）：

| 指标 | 数值 |
|---|---|
| per_query 结果集合不同的条数 | **31 / 179**（17.3%） |
| top1（首位）不同 | **14 / 179**（7.8%） |
| top3 集合不同 | 17 / 179（9.5%） |
| 位置变化总数 | 73 处 |
| 平均位置漂移（共同元素） | 0.11 位 |
| top_scores 相关系数 | 0.9699 |
| **jaccard_vs_baseline（旧指标）** | **1.0（盲区）** |

权重改变后，17% 的查询召回集合就不同了，且 top1 换了 7.8%。**敏感度真实存在**，只是被 jaccard 掩盖。

## 二、敏感度的量级判断

当前敏感度温和，原因不是"权重无效"，而是：

1. **记忆库极小**：生产库仅 4 条 memoryitem。所有查询都在 4 条里选，top-k=5 几乎必然全召回。
2. 权重变化只影响**排序**，集合层面变化有限（31/179 是因为 FTS 追加召回 + 候选截断）。
3. 库量级涨上来后（>100 条记忆），候选集变大，权重对 top-k 集合的塑造力会显著增强。

**结论**：交接文档"当前库量级小，权重不敏感"应修正为"**库量级小导致集合指标不敏感，但排序指标已现分化；调参矩阵必须用位置敏感指标评估**"。

## 三、可用命令（Windows 本机，.venv-audit）

扩展矩阵（6 组参数 × 179 样本）：
```bash
python scripts/run_param_matrix.py --query-set dry-run-v1 --intent rule --no-rerank
```

快速试跑（前 30 条）：
```bash
python scripts/run_param_matrix.py --limit 30
```

指定基线 run：
```bash
python scripts/run_param_matrix.py --baseline erun_01KZFJNKA0BVHGND9...
```

脚本产出：每组参数落一个 eval_run + 终端矩阵表 + 写 `docs/param-matrix-report.md`（覆盖本文件）。

## 四、参数空间建议

当前 settings 默认：`W_VECTOR=0.6 W_BM25=0.25 W_FTS=0.05 W_DECAY=0.1`。

矩阵覆盖方向：

| 标签 | 变化 | 验证点 |
|---|---|---|
| base | 默认 | 基线 |
| vec+ | W_VECTOR 0.75 / W_BM25 0.15 | 向量主导（已实证敏感） |
| vec++ | W_VECTOR 0.85 / W_BM25 0.05 | 极端向量化 |
| bm25+ | W_BM25 0.45 / W_VECTOR 0.40 | 关键词主导 |
| decay+ | W_DECAY 0.25（其余摊薄） | 时效主导 |
| fts+ | W_FTS 0.15（其余摊薄） | FTS 命中主导 |

## 五、下一步

1. 本机跑 `run_param_matrix.py` 出完整矩阵（6 组 × 179 = ~3 分钟，rule 模式无 LLM 调用）
2. 记忆库量级是敏感度上限的决定因素——持续 ingest，库 >100 条后重跑矩阵
3. used_ids 回填后 weak_hit_rate 可用，可进一步按"实际被使用的记忆"评估排序质量

## 附：分析基于的 run

- 基线（默认）`erun_01KZFJNKA0BVHGND9...`（2026-08-08 02:19）
- 对比（W_VECTOR=0.75）`erun_01KZE6WKGBA5JKRH7...`（2026-08-07 13:34）
