# Iteration 13 — 主题综合 IV：多宿主接入与评测自证（2026-09-19）

## 类型
synthesis（主题 4/4）

## 接入格局
- MCP 已是默认分发通道（20 系统中 12+ 官方提供）；工具面量级：agentmemory 54、**兰台 59（实核）**、Mem0 11、Supermemory 3 工具。
- 宿主矩阵是真正差距：agentmemory 32+ 客户端（插件+钩子+MCP 三形态）、OpenViking（Claude/Codex/Cursor/TRAE+SDK+桌面应用）、MemU（每宿主专用二进制 record/inject）vs 兰台 Hermes 单点+shell hook。
- **注入回执**：头部均未提供「记忆确实被宿主注入」的证据链（2026-09-18 调研主线 A 指出 HTTP 200≠宿主拿到）——无人区，兰台可做第一个。
- MemU/OpenViking 的「宿主侧钩子挖掘会话日志」模式提示：兰台 shell hook 可泛化为 Claude Code/Codex/Cursor 的钩子适配（低侵入）。

## 评测自证
- 见 iter-09。兰台 evaluation 2.0 的 3.5→4.0 路径=E1 阶段化指标+E2 确定性用例+E3 外部锚点（LongMemEval-S 子集+MemoryAgentBench，标注 judge 协议）。
- 对外叙事纪律：所有厂商自报分数记 claim 不记 fact；兰台自证同样公开 judge 协议与样本。

## 结论与评分终值（收敛声明）
| 维度 | 兰台终值 | 依据 |
|---|---|---|
| ingest_write 4.0 | 闸门/去重/精炼/异步/直存（继承，结构证据充分） |
| retrieval 4.0 | 混合+贯珠+烽燧+拾遗（继承） |
| temporal_conflict 3.0 | R7 核验降分后不再动，待 D20 落地 |
| forgetting_lifecycle 4.0 | 状态机+回滚+巩固（继承） |
| governance 4.0 | 全场唯一闭环（iter-12） |
| human_oversight 4.0 | 全场唯一人工闸门（iter-12） |
| integration 3.5 | R7 上调（59 工具），宿主矩阵缺口明确 |
| evaluation 2.0 | E1-E3 未做，如实 |
| procedural 3.5 | Skill/结晶有，episode 影子 |
| ops_cost 3.5 | SQLite 内嵌+异步+司天 |
| **加权总分** | **3.56** | R13 起冻结为 v-final |

## 决策
- D24：integration 路线改为「宿主矩阵+注入回执」，替代旧『MCP 工具扩容』表述（工具面已足量）。
- D25：评分冻结；后续轮次（R14/R15）不再改分，只做路线图收口与可视化。

## 覆盖
systems=20 papers=32 lantai=3.56(frozen) score_delta_sum=0 roadmap_churn 见 R14
