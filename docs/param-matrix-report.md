# 调参对比矩阵报告 v2

> 日期：2026-08-08 18:47 | 查询集：dry-run-v2（214 样本，420 事件/248 干净去重）
> 本轮含 FTS5 特殊字符语法修复，矩阵期间 1284 次检索 FTS5 警告 0 次
> 命令：`python scripts/run_param_matrix.py --query-set dry-run-v2 --intent rule --no-rerank --top-k 5`

## 一、矩阵结果

| 标签 | overrides | zero_rate | avg_count | jaccard | 耗时 |
|---|---|---|---|---|---|
| base | `default` | 0.0 | 4.0 | None | 30.4s |
| vec+ | `{'RETRIEVAL_W_VECTOR': 0.75, 'RETRIEVAL_W_BM25': 0.15}` | 0.0 | 4.0 | 1.0 | 27.5s |
| vec++ | `{'RETRIEVAL_W_VECTOR': 0.85, 'RETRIEVAL_W_BM25': 0.05}` | 0.0 | 4.0 | 1.0 | 26.3s |
| bm25+ | `{'RETRIEVAL_W_BM25': 0.45, 'RETRIEVAL_W_VECTOR': 0.4}` | 0.0 | 4.0 | 1.0 | 24.3s |
| decay+ | `{'RETRIEVAL_W_DECAY': 0.25, 'RETRIEVAL_W_VECTOR': 0.45, 'RETRIEVAL_W_BM25': 0.2}` | 0.0 | 4.0 | 1.0 | 29.0s |
| fts+ | `{'RETRIEVAL_W_FTS': 0.15, 'RETRIEVAL_W_VECTOR': 0.5, 'RETRIEVAL_W_BM25': 0.25}` | 0.0 | 4.0 | 1.0 | 24.2s |

## 二、位置敏感对比（vs 基线轮，Jaccard 盲区补充）

| 标签 | top1一致率 | top3集合一致率 | 平均位置漂移 | 分数相关 |
|---|---|---|---|---|
| vec+ | 0.9206 | 0.8972 | 0.1168 | 0.9691 |
| vec++ | 0.8411 | 0.5888 | 0.3879 | 0.8657 |
| bm25+ | 0.7991 | 0.9533 | 0.1285 | 0.969 |
| decay+ | 0.8364 | 0.6075 | 0.3388 | 0.936 |
| fts+ | 0.9626 | 0.9813 | 0.0327 | 0.9983 |

## 三、解读（v2 实证）

1. **集合无区分度是量级效应**：记忆库仅 4 条 active（fact 1 / preference 2 / working 1），
   top_k=5 恒返回全部记忆 → jaccard 恒 1.0。与 v1 结论一致，是数据问题而非权重问题。
2. **位置敏感指标首次出现实质分化**：vec++（W_VECTOR=0.85）与 decay+ 把 top3 集合
   一致性打到 **58.9% / 60.8%**——约四成查询的 top3 集合与基线不同；fts+ 影响最小
   （top1 96.3% / top3set 98.1% / drift 0.033）。实证"集合不变 ≠ 排序不变"。
3. **当前权重组合偏稳健**：214 条查询 zero_result=0%、avg=4.0/5，无召回缺口；
   向量主导排序（vec+ 仅 7.9% 查询改变 top1）。
4. **FTS5 特殊字符修复**：修复前矩阵日志大量 `fts5: syntax error near "@"/"="`，
   FTS 通道在含符号查询上整体降级；修复后 1284 次检索警告 0。因 FTS 权重 0.05
   且库量级小，指标未变——修复价值在通道可用性，待库量级上来后复测。
5. **不建议现在调权**：4 条记忆下任何权重调整都缺乏统计意义；先攒数据
   （目标 >100 条 active），再按此矩阵重跑，届时 jaccard 与位置指标都会分化。

## 附：run_id
- base: `erun_01KZGFKBRHC2B4JA71K8M7QZGY`
- vec+: `erun_01KZGFM9ENGBQDMBQRD6Y4PKYN`
- vec++: `erun_01KZGFN499P347T6SENMSW2ADA`
- bm25+: `erun_01KZGFNXYHA5W4C9B8HVFP0NE4`
- decay+: `erun_01KZGFPNP9HYDAK9W8V5140HQA`
- fts+: `erun_01KZGFQJ0Z8BB5027R3DJP650R`

