# 兰台记忆系统：全景调研与方向综合简报（2026-09-19）

> 本简报是 15 轮迭代优化的最终产出。证据边界：20 个记忆系统（官方 README/docs/论文一手核验）、32 篇论文与基准（arXiv 摘要级核验）、9 个基准横评；兰台自评经本地代码核验（R7）。所有检索时点为 2026-09-19；stars 会漂移；厂商自报基准分一律记为 claim。不改产品代码、不提交、不发布（继承调研纪律）。

## 一、结论摘要（TL;DR）

1. **格局**：2026-09 的记忆系统分成四个阵营——头部开源平台（Mem0 65.6k★ / Supermemory 30.2k★）、时序图与重基础设施（Zep/Graphiti 31k★ / Cognee 30.8k★ / MemOS 11.5k★）、轻量内嵌同类（agentmemory 28.6k★ / MemU 14.4k★ / Memobase 2.9k★）、平台原生（ChatGPT Dreaming V3 / Claude Memory / Cursor Memories）。2026 现象级 newcomer 是字节 OpenViking（38k★，viking:// 文件系统，AGPLv3）。
2. **兰台位置**（加权 3.56/5，10 维评分冻结于 R13）：治理与人机协同是**全场独占**（人工闸门+checkpoint 回滚+注入围栏，唯一闭环）；生命周期结构最完整（衰减→归档→巩固→退役四段式）。硬缺口按优先级：**事件时间缺失（数据级）**、**宿主矩阵单点**、**评估自证不足（2.0）**。
3. **两条旧结论被本轮更正**（R7 本地代码核验）：「Chronos 双时间轴」与当前 schema 不符（仅 created_at，无事件时间）；「MCP 仅 4 工具」严重过时（实为 59 个）。
4. **方向**（路线图 v2）：P0=可靠性收口+撤回/删除四分法+阶段化评测+注入回执链；P1=事件时间与时效视图（首位）+宿主矩阵+巩固可信度；P2=影子学习与操作级自证发布。定位声明：**全生命周期可治理的本地长期记忆层，用操作级自证替代榜单叙事**。
5. **评测转向**：LoCoMo 信任危机后，前沿已转向操作级/阶段级归因（HaluMem）与能力分层（MemoryAgentBench）；兰台应自证而非刷榜。

## 二、格局总览（详见 03_quadrant.png / dashboard §③⑥）

| 阵营 | 代表 | 一手核验要点 |
|---|---|---|
| 头部开源平台 | Mem0、Supermemory | Mem0 新算法=单遍 ADD-only、图按共现连边无类型边、治理仅 history.db；Supermemory 多模态摄取+本地模式+自建 MemoryBench 自证 |
| 时序图/重基建 | Zep/Graphiti、Cognee、MemOS、OpenViking | Zep bi-temporal（valid_at/invalid_at+自动失效）为时间维度唯一 5 分；OpenViking 会话提交→提取→新建/合并/跳过与兰台闸门流同构；重基建 ops_cost 均为负资产 |
| 轻量内嵌同类 | agentmemory、MemU、Memobase、MIRIX、Memori | agentmemory（SQLite 零依赖、54 MCP 工具、32+ 客户端、Ebbinghaus 衰减）是兰台最直接对照；MemU 程序性记忆最强（技能文件+代理自修补） |
| 平台原生 | ChatGPT Memory+Dreaming V3、Claude Memory、Cursor Memories、OpenAI Sessions | 平台已把「用户可见可编辑+导入导出+敏感主题默认排除」做成标配；**Cursor Memories 已于 2.1.17 移除**——客户端内建记忆脆弱性的直接实证 |

## 三、论文与基准综合（详见 iter-08/09）

- **三集群**：①RL 记忆管理（Mem-α、Memory-R1、Fine-Mem）——存什么/怎么更新可学，但奖励稀疏、开发场景自动判分信号弱；②巩固与遗忘（LightMem ICLR 2026、Oblivion、SCM-Sleep、TRUSTMEM）——遗忘=可及性衰减（与兰台同构）、巩固产物本身可错（须过审）；③治理化共享记忆（Governed Shared Memory）——四大失败模式+四原语（scoped retrieval / temporal supersession / provenance / policy propagation）。
- **基准**：LoCoMo 答案键 6.4% 错误引发信任危机；LongMemEval（500 问五能力）、MemoryAgentBench（四能力）、HaluMem（操作阶段幻觉归因）、MemBench、ImplicitMemBench（内隐/程序性）、EvoMemBench（自进化）构成新评测矩阵。「写精度/遗忘质量/删除边界」仍是基准荒地——兰台差异化自证空间。

## 四、兰台位置（详见 05_score_matrix.png / 04_dimension_comparison.png）

**独占面（4.0，全场唯一）**：human_oversight（锦囊+案牍+探颐+持节）、governance（provenance+ACL+辨域+樊篱+checkpoint）。
**强项（4.0）**：ingest_write（闸门/校雠/披沙/潜移）、retrieval（混合+贯珠+烽燧+拾遗）、forgetting_lifecycle（知命状态机+沉潜）。
**短板**：temporal_conflict 3.0（无事件时间，Zep 5.0 对比）；integration 3.5（59 工具已足量，宿主矩阵与注入回执是缺口，对照 agentmemory 4.5）；evaluation 2.0（dry-run 179 样本未阶段化、无公开锚点）；procedural 3.5（MemU 4.5/OpenViking 4.0 之上，episode 仍影子）。

## 五、方向判断（路线图 v2 全文见 roadmap-v2.md）

1. **不追检索性能榜**：Mem0/Supermemory 的检索分靠平台与 token 投入，兰台赢面在「写对、记得住时效、错可纠、删得净」。
2. **时间是第一缺口**：治理四原语中兰台唯一数据级缺失是 temporal supersession。P1-1 事件时间（可空+精度）+时间窗过滤+双视图，不全图化。
3. **接入从工具面转向宿主矩阵**：MCP 工具 59 个已与 agentmemory 同量级，差距在 Claude Code/Codex/Cursor 钩子适配（MemU/OpenViking 模式）与**注入回执**（全场无人区）。
4. **治理优势必须「可证明」**：平台已把页面级用户控制做成标配；兰台需交付机器级审计（直断/provenance/checkpoint 导出）与四分删除语义的确定性用例（撤回后禁用命中=0）。
5. **自证替代榜单**：E1 阶段化指标+E2 确定性用例+E3 外部锚点（LongMemEval-S/MemoryAgentBench 子集，公开 judge 协议）。
6. **RL 与多智能体只影子观察**：证据不足以支持生产化承诺（D16）。

## 六、15 轮迭代过程（详见 iterations/iter-01..15.md + 01_iteration_process.png）

- R1 框架 v1（10 维+权重+锚点）→ R2 论文 +15 → R3 头部 5 系统核验 → R4 平台原生 → R5/R6 新兴开源 8 个 → R7 兰台内部核验（唯一分数修正轮：temporal −0.5、integration +1.0）→ R8/R9 论文与基准综合 → R10-R13 四主题（评分零变动=收敛）→ R14 路线图收口（churn：改 2 增 3）→ R15 可视化冻结。
- 收敛证据：score_delta_sum 自 R8 起为 0；覆盖封盘 systems=20 / papers=32；iter-01 手算误差（3.40→3.52）于 R3 程序化修正并留痕。

## 七、工件索引

| 文件 | 内容 |
|---|---|
| `README.md` | 迭代协议与评分记法 |
| `framework.json`（v6） | 10 维评测框架 + 18 系统评分 + 加权总分 |
| `landscape.json` | 20 系统台账（机制/来源/核验状态） |
| `papers.json` | 32 论文与基准台账 |
| `iterations.jsonl` + `iterations/` | 15 轮迭代流水与记录 |
| `roadmap-v2.md` | 路线图 v2（P0×4/P1×3/P2×5+不做清单） |
| `charts/01..05*.png`, `charts/dashboard_full.png` | 迭代过程与结果图表、dashboard 渲染图 |
| `dashboard.html` | 单文件零依赖可视化框架（浏览器直接打开） |
| `gen_charts.py` / `gen_dashboard.py` | 可复现生成脚本 |

## 八、局限与后续

- stars/功能为 2026-09-19 快照；基准分数均厂商自报，未独立复现。
- 兰台 3.56 为「机制存在性+结构证据」评分，非效果实测；升级到 4.0 需 E1/E2 证据。
- Further work：事件时间 schema 设计票、宿主钩子适配票、四分删除语义票（建议走 `.scratch/` 票据化流程）；平台级「记忆摘要页」交互对齐（悬镜增强观察项）。
