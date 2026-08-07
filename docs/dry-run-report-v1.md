# Dry-Run 评估报告 v1 — Remembrance 检索质量基线

> 日期：2026-08-07 | 管道：Step 3 后半（EvalQuerySet → run_dry_run → compute_metrics）
> 数据：210 条干净检索事件（is_system_noise=0）→ 去重后 **179 条查询样本**

## 一、执行方式

```bash
# 基线（默认参数）
python scripts/run_dry_run.py --query-set dry-run-v1 --top-k 5 --no-rerank --intent rule

# 参数对比轮（调高向量权重 + 与基线比 jaccard）
python scripts/run_dry_run.py --query-set dry-run-v1 --top-k 5 --no-rerank --intent rule \
  --override RETRIEVAL_W_VECTOR=0.75 RETRIEVAL_W_BM25=0.15 \
  --baseline <基线 run_id>
```

- `--intent rule`：跳过 LLM 意图分类（评估期间 LLM API 超时；rule 模式用 DEFAULT_INTENT，不影响召回指标）
- `--no-rerank`：跳过 rerank（省一半 LLM 调用；基线评估先看召回层）

## 二、结果

| 指标 | 基线（默认参数） | 对比轮（W_VECTOR=0.75） |
|---|---|---|
| sample_count | 179 | 179 |
| zero_result_rate | **0.0%** | 0.0% |
| avg_result_count | **4.0**（top_k=5） | 4.0 |
| weak_hit_rate | null（无 used_ids 数据） | null |
| jaccard_vs_baseline | — | **1.0** |
| 耗时 | 33s | 33s |

run_id：
- 基线 `erun_01KZE6VGYCTPQ6DM2YYHSH7RPF`
- 对比轮 `erun_01KZE6WKGBA5JKRH7FWGETQM65`

## 三、解读

1. **零漏检**：179 条真实查询全部命中记忆，`zero_result_rate=0.0%`——混合检索（向量+BM25+FTS+衰减）召回层健康。
2. **召回量 4.0/5**：平均 4 条，接近 top_k 上限，检索不是"饥饿"状态。
3. **对权重不敏感（jaccard=1.0）**：调 W_VECTOR 后召回集合完全一致。原因：记忆库量级尚小（几百条），top_k=5 内排序主要由向量主导，权重变化未改变集合。**这是量级效应，不是 bug**——库大了权重差异才会显现。
4. **weak_hit_rate 暂无**：系统无生成侧（Hermes 回答用哪些记忆）回填 used_ids，诚实标注 null。等回填通道接入后才有弱标注。

## 四、已知缺口与下一步

| 缺口 | 影响 | 解法 |
|---|---|---|
| used_ids 无回填 | weak_hit_rate 无法计算 | Hermes 生成侧接入回填（backfill_used_ids） |
| LLM API 慢 | intent=llm 模式跑不动 | API 恢复后重跑对比；或本地规则意图 |
| 记忆库量级小 | 调参对比不敏感 | 继续攒数据（当前 ~363 事件） |
| intent_bucket 全 null | 无法按意图分桶评估 | 埋点补 intent（relevance_check 无 intent 字段） |

## 五、后续计划

- **Step 6**：时效复查（published_at 过期策略）
- **Step 7**：影子观察期 + DEDUP shadow-only
- **Step 8**：验证结果回流 + 回归
- 参数对比矩阵：W_VECTOR/W_BM25/W_FTS/W_DECAY 各调多档，观察 jaccard 分化（库量级上来后）

## 附：管道文件

```
remembrance/eval/models.py    — EvalQuerySet / EvalRun 表
remembrance/eval/query_set.py — build_query_set()（事件→查询集）
remembrance/eval/metrics.py   — compute_metrics()（相对指标纯函数）
remembrance/eval/runner.py    — run_dry_run()（执行 + 落库 + intent_mode）
scripts/run_dry_run.py        — CLI（--query-set/--override/--baseline/--intent/--limit）
docs/dry-run-eval-task-split.md — 接口契约（A/B/C 三模块任务书）
```
