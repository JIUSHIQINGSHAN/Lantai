# Iteration 03 — 头部 5 系统一手核验（2026-09-19）

## 类型
survey（子代理实地抓取 GitHub/docs/博客，2026-09-19 当日 stars）

## 吸收的关键事实
| 系统 | stars(09-19) | 核验要点 |
|---|---|---|
| Mem0 | 65.6k | OSS 新算法=单遍 ADD-only（无 UPDATE/DELETE）；平台图按**共现**连边、无类型边；检索=向量+BM25+图加分；治理仅 history.db；MCP 11 工具。LoCoMo 92.5 等分均为**平台自报**且有 Zep(2025-05)/Letta(2025-08) 公开质疑 |
| Zep/Graphiti | 31.0k | 三层模型（episode 节点→entity→边 facts）；显式 bi-temporal `valid_at/invalid_at`+自动失效；混合检索+重排；事实可溯源 episode；需 Neo4j/FalkorDB |
| Letta | 24.8k | MemFS git-backed 记忆文件系统；Dreaming(sleep-time compute) 后台复盘巩固；**审核自动、明言不请求人工批准**；文件系统-only agent LoCoMo 74%（自报）并称"基准意义有限" |
| LangMem | 1.7k | CoALA 三分（semantic/episodic/procedural）；热路径 vs 后台双轨；collection 模式把删除/更新复杂度留给开发者 |
| Supermemory | 30.2k | MIT 协议；多模态摄取（OCR/转写/AST 分块）；hybrid 检索；本地模式（bge 本地嵌入）；自建 MemoryBench 自证榜首（激励注意） |

## 评分变化（verified 替换 inherited）
- Mem0 3.10→**2.76**（治理/监督降分：审批回滚未确认；ADD-only 简化合并）
- Zep 3.05→**3.00**（temporal_conflict 升至 5.0，ops_cost 2.0 图库负担）
- Letta 2.45→**2.58**（governance 3.0：git-backed 历史可审计）
- LangMem 2.30→**2.25**
- Supermemory 新增 **2.59**
- 兰台 **3.40→3.52**：非评分变化，是 iter-01 手算错误修正（程序化加权计算）

## 决策
- D6：Zep 的 bi-temporal（5.0）实证兰台 temporal_conflict=3.5 的差距为**真差距**，非文档夸大。
- D7：Mem0/Supermemory integration=4.5 反衬兰台 2.5 的接入短板；MCP 已是默认分发通道（5 家中 4 家官方 server）。
- D8：人工审批在头部产品中**缺位**（Letta 明言自动）——兰台的锦囊/案牍是稀缺能力，但需转化为可复现证据。
- D9：厂商基准分一律记为 claim+争议，不入评分证据（框架 evidence_rule 落实）。

## 开放问题
- Q1（Chronos 覆盖度）、Q2（MCP 工具面）转入 R7 本地代码核验；Q4（平台原生）转入 R4。

## 覆盖
systems=12(5 verified) papers=32 new_primary_sources=14 lantai_score_delta_sum=0(修正算术) roadmap_churn=0
