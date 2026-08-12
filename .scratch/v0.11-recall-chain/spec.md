# v0.11 记忆广播链（烽燧）spec

借鉴源：aiduMEI v18.3 `ducky/pipeline/memory_broadcast.py`（POST /recall_chain）——
从一条 seed 记忆出发，以它为 query 再检索引擎，结果继续作 seed 形成传播链（深度 3 / 分支 3 / 最低分 0.3 / 总量 20）。

## 设计

- `lantai/ops/recall_chain.py::build_recall_chain(seed_text, max_depth=3, branch=3, min_score=0.3, total_max=20)`
  - 每层 BFS：当前 seed 集逐条调 `hybrid_search(top_k=branch*3, use_rerank=False)`，链内按分数降序取前 branch 条
    （hybrid 非重排路径返回顺序依赖 DB 行序，必须自排序）；
  - 入选：score ≥ min_score、非自匹配（文本归一化相等 或 jieba 词集合余弦 ≥ 0.9）、id 跨层去重、总量未达 total_max；
  - 下一层 seed = 入选记忆的 content；自匹配条目 id 记 seen（锚点整链排除——链只见关联不见起点）；
  - 提前终止：total_max 达标 / 某层零入选 / 达到 max_depth；单条搜索失败只缺层不阻断（宁 miss 不脏写）。
- `validate_chain_params`：max_depth∈[1,5]、branch∈[1,10]、min_score∈[0.0,1.0]、total_max∈[1,50]；非法抛 ValueError 不静默修正。
- REST `GET /recall/chain?q=&max_depth=&branch=&min_score=&total_max=`（受保护；校验失败 422）。
- MCP `recall_chain`（工具 39 → 40，只读）。

## 测试要求

- `validate_chain_params` 纯函数校验（不 mock）。
- 空库 / 空 seed → 空链 / ValueError。
- 多跳传播：seed → 直接相关 → 经内容接力到更远关联；无关记忆不入链。
  真实 SQLite+FTS + 本地 ngram 嵌入 + 假向量库（仅替换外部网络 embedding/向量存储），
  BFS 展开 / 去重 / 自匹配 / 封顶 全部真实执行。
- 自匹配排除：seed 内容即某条记忆 → 该条整链不出现，关联记忆仍入选。
- 总量封顶 truncated；min_score 过滤（衰减地板分不入链）。

## 明确不吸收

- workspace 冷记忆自动清理（access_count + 超时）：兰台 archived/tier 语义已有。
- J-lens 整包审计报告：search_trace / recall_report / reflect 审计已覆盖。
- Ignition 双路径检索标记：兰台 trace 体系已覆盖。
