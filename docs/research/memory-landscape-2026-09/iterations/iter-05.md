# Iteration 05 — 新兴开源批 1：MemOS / Memobase / Cognee / MemU 一手核验（2026-09-19）

## 类型
survey（GitHub README 实抓，2026-09-19 当日 stars）

## 吸收的关键事实
| 系统 | stars | 核验要点 |
|---|---|---|
| MemOS | 11.5k | 自托管 Neo4j+Qdrant（docker compose）/本地 SQLite 插件（100% 设备端）/云插件；MemScheduler 异步摄取；自然语言反馈纠正记忆；混合检索（FTS5+向量）+智能去重；技能进化层级 **L1 traces / L2 policies / L3 world model**；Multi-Cube 知识库（隔离/受控共享/create_cube）；LoCoMo 88.83、LongMemEval 89.20（OmniMemEval 自报）、省 35.24% token（自报） |
| Memobase | 2.9k | **profile-based**：不存碎片条目，学出 topic/sub_topic/content 结构化画像；用户事件时间线答时间问题；buffer flush（1024 tokens 或闲置 1h 自动冲刷）；0.0.40 起固定 3 次 LLM 调用（token 降 40-50%）；FastAPI+Postgres+Redis；<100ms 在线延迟（自报）；LoCoMo SOTA 自报 |
| Cognee | 30.8k | 四操作 **remember/recall/improve/forget**；文本→实体/关系/分块、代码→符号+依赖图、会话蒸馏→固化；检索按问题类型自动路由（图/向量/代码）；Postgres 单实例 1.0（图为 demo 特性，生产图库=许可产品）；cognee-mcp + Claude Code/Codex/OpenClaw 插件 |
| MemU | 14.4k | **记忆=Markdown 技能文件 Wiki**；后台桥接任务把会话日志切片为作业文件；代理自读技能库决定不操作/修补/新建；MemoryService 只 store/embed/retrieve 不做 LLM 调用；每宿主专用二进制（Codex/Claude Code/Cursor）record/inject；核心仅 500 行 |

## 评分（verified 替换 inherited）
MemOS 2.58 / Memobase 2.29 / Cognee 2.27 / MemU 2.48。兰台不动。

## 决策
- MemU 是**程序性记忆最强样本**（4.5）：技能文件+代理自修补+宿主注入，与兰台 Skill 资产/结晶直接对标。
- MemOS 的 L1/L2/L3 分层与兰台 episode/规则/器识分层同构，佐证分层方向。
- Cognee 已 30.8k★ 且 forget 成为一等 API 操作——**遗忘进入主流接口面**。
- Memobase 印证兰台 coalesce 缓冲冲刷设计（1024 tokens/1h 与潮波同构）。

## 覆盖
systems=16(landscape 扩至 20 于 R6) papers=32 score_delta_sum=0(兰台) roadmap_churn=0
