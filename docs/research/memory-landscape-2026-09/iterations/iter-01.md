# Iteration 01 — 框架 v1 与数据骨架（2026-09-19）

## 类型
setup

## 本轮做了什么
1. 建立工作区与迭代协议（README.md）：15 轮，每轮须有可检验增量。
2. 评测框架 v1：10 个能力维度 + 权重 + 0–5 评分锚点（framework.json）。
   - 维度：写入提取 / 召回检索 / 时间冲突 / 遗忘生命周期 / 治理安全 / 人机协同 / 多宿主接入 / 评估自证 / 程序性记忆 / 性能运维。
   - 权重设计理由：时间冲突、治理、召回是 2026 年行业公认主战场（来源：2026-08-11 内部调研§四，及 2026-09-18 调研主线 A/B/C），各 0.12/0.12；评估自证作为差异化竞争点给 0.10。
3. 数据骨架：landscape.json 12 系统（含兰台自评）、papers.json 17 篇（含 7 篇本地全文）。
4. 基线评分：兰台 10 维继承分（依据 CONTEXT.md 词汇表 + ADR 目录，全部标 inherited），以及 Mem0/Zep/Letta/LangMem 四个对照系统的继承分。

## 基线结果（加权总分）
- 兰台：**3.40**（ingest 4.0, retrieval 4.0, temporal 3.5, forgetting 4.0, governance 4.0, oversight 4.0, integration 2.5, evaluation 2.0, procedural 3.5, ops 3.5）
- Mem0 3.10 / Zep 3.05 / Letta 2.45 / LangMem 2.30
- 注：兰台基线偏高反映"继承自内部文档的机制广度"，integration 与 evaluation 两项明显短板已可见。

## 决策
- D1：迭代对象=数据集+评分+路线图，不改产品代码（继承调研纪律）。
- D2：继承分与验证分严格分离标注，防止"文档自述"冒充核验事实。
- D3：外部系统评分只依据其公开可证实机制，不实测、不横比厂商基准分。

## 开放问题（转入后续轮次）
- Q1 兰台 Chronos 双时间轴实际覆盖度；Q2 MCP 工具面现状；Q3 LightMem/Mem-alpha/HaluMem/MemBench 元信息；Q4 平台原生记忆（OpenAI Dreaming/Claude memory/Cursor）机制细节。

## 覆盖
systems=12 papers=17 primary_sources≈30（多为 inherited）
score_delta_sum=0（基线）roadmap_churn=0
