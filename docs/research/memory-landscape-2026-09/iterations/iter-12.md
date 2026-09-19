# Iteration 12 — 主题综合 III：治理、安全与人机协同（2026-09-19）

## 类型
synthesis（主题 3/4）

## 治理全景（20 系统横评）
| 治理能力 | 兰台 | 全场最佳对照 |
|---|---|---|
| provenance 血统 | ADR-0015 提取来源（模型/提示词/时间） | Zep（事实溯源 episode）；Governed Shared Memory 原语化 |
| 人工审批 | 锦囊待审+案牍+持节受控自主 | **全场唯一有人工闸门者**；Letta 明言自动不请批准 |
| 回滚 | checkpoint 快照 | Letta git-backed（最接近）；其余未见 |
| 权限隔离 | ACL+辨域三维硬隔离 | Zep 每用户图/Mem0 图视图分级（弱） |
| 提示注入防御 | 樊篱 `<memory_data>` 围栏（OWASP LLM01） | 未见同规模公开实现 |
| 用户可见可编辑 | /ui/vault+悬镜（机器级明细） | 平台级标配：Claude Topics/OpenAI memory summary（页面级摘要） |
| 导入/导出 | 冷启动导入 JSONL+digest 可读导出 | Claude 显式导入导出通道；agentmemory 54 工具跨客户端 |
| 删除语义 | 归档可逆；级联文档删除（ADR-0042） | **纠错/撤回/归档/删除四分法仍待实现**（2026-09-18 主线 B） |

## 结论
- 治理 4.0 维持：兰台是唯一把「人工闸门+回滚+注入围栏」做成完整闭环的系统（TRUSTMEM 的「巩固即持久系统状态失败」为其学理支撑）。
- 但**平台已把「页面级用户控制」做成标配**（D10），兰台的治理优势必须以两条方式兑现：①机器级可审计（provenance+直断+checkpoint 的 API/导出）②四分删除语义落地（撤回传播到 FTS/Chroma/缓存/宿主副本）。
- 持节（受控自主审批）与 Governed Shared Memory 的 policy-governed propagation 同构——是进入多智能体叙事的门票。

## 决策
- D22：治理主线维持 P0，新增交付物「治理能力对照表（四原语对齐）」作为对外文档。
- D23：撤回/删除四分法保持 P0（覆盖 SQLite/FTS/Chroma/摘要/缓存/宿主副本），评测用例 E2 的「撤回后禁用命中=0」为其验收口径。
