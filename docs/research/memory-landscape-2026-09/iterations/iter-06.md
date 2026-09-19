# Iteration 06 — 新兴开源批 2：MIRIX / Memori / agentmemory / OpenViking 一手核验（2026-09-19）

## 类型
survey（GitHub README 实抓 + 官方来源搜索）

## 吸收的关键事实
| 系统 | stars | 核验要点 |
|---|---|---|
| MIRIX | 3.4k | 六记忆组件（core/episodic/semantic/procedural/resource/knowledge_vault）各配专职 memory agent；**auto-dream 端点**做去重/合并/过期/冲突清理；多模态（文本/图像/语音/屏幕捕获）；PostgreSQL BM25+向量；致谢 Letta 作记忆基础；论文 arXiv 2507.07957 |
| Memori (MemoriLabs) | 16.8k | 原 GibsonAI/memori 已迁移；entity/process/session 三层追踪 + attributes/events/facts/people/preferences/relationships/rules/skills 增强类别；LLM/DB/框架无关、BYODB；一条命令 MCP 接入 Claude Code/Cursor/Codex/Warp/Antigravity；LoCoMo 87%、~721 tokens/查询（自报） |
| agentmemory | 28.6k | **SQLite+iii-engine 零外部依赖**；自动捕获代理行为→压缩→下次会话注入；BM25+向量+知识图谱三路 RRF(k=60) 融合；Ebbinghaus 衰减+访问强化+auto-evict+矛盾检测+TTL；四级整合 working→episodic→semantic→procedural；**默认 54 个 MCP 工具**+17 技能；实时查看器含会话回放；LongMemEval-S R@5 95.2%（自报） |
| OpenViking (字节/火山) | 38.0k | **viking:// 虚拟文件系统**（语义化目录+.abstract/.overview，L0/L1/L2 三层加载）；resources/user memories/skills/peers 分区；会话提交归档→后台提取记忆→**与已有记忆比较决定新建/合并/跳过**；向量先定位目录再目录内探索（TrieHI，ICDE）；**AGPLv3**（主）；MCP+Hooks 接 Claude/Codex/Cursor/TRAE/OpenCode；Py/Go/TS SDK+桌面应用 Beta |

## 评分（verified）
MIRIX 2.31 / Memori 2.25 / agentmemory 2.95 / OpenViking 2.60。兰台不动。

## 决策
- **agentmemory 是兰台最直接同类**（SQLite 零依赖+衰减遗忘）：54 MCP 工具 vs 兰台 59——integration 差距不在工具数，在客户端矩阵（32+ vs 兰台 Hermes 单点）。
- OpenViking 38k 现象级，其「会话提交→提取→与已有比较→新建/合并/跳过」与兰台闸门流**同构**，佐证兰台写入管线方向；AGPLv3 对比兰台自用无影响。
- Memori 迁移 MemoriLabs 并走企业 BYODB——轻量内嵌与企业托管分化加剧。
- MIRIX auto-dream 与兰台沉潜/反思同构。

## 覆盖
systems=20 papers=32 score_delta_sum=0(兰台) roadmap_churn=0
