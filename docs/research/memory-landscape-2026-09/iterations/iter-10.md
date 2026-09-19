# Iteration 10 — 主题综合 I：记忆分类学与生命周期（2026-09-19）

## 类型
synthesis（主题 1/4）

## 跨系统分类学对照（全部 verified）
| 系统 | 分类法 | 覆盖 CoALA 四类(工作/情景/语义/程序性) |
|---|---|---|
| LangGraph/LangMem | CoALA 三分（semantic profile/collection、episodic few-shot、procedural 自改 prompt） | 缺工作记忆分层（thread 消息历史充当） |
| MIRIX | 六组件：core/episodic/semantic/procedural/resource/knowledge_vault，各配专职 agent | 全覆盖+扩展 |
| agentmemory | 四级整合：working→episodic→semantic→procedural（睡眠整合隐喻） | 全覆盖 |
| MemOS | L1 traces/L2 policies/L3 world model + Multi-Cube | 三层为「轨迹→策略→世界模型」变体 |
| OpenViking | memories/resources/skills/peers 按 viking:// 目录 | 全覆盖（目录即类型） |
| MemU | Markdown 技能文件 Wiki（程序性为纲） | 侧重程序性 |
| **兰台** | lane 分轨（事实/规则/偏好/经验/闲聊）× tier（工作/长期）+ Skill 资产 + episode + 底本 + 札记 | 全覆盖：语义=事实/偏好，程序性=Skill/结晶，情景=episode（**影子未接评分**），工作=tier 临时+札记+底本 |

## 生命周期对照
- 兰台独有：知命状态机（活跃→弱化→被取代→退役）+ checkpoint 回滚 + 沉潜巩固 + 考功价值演化——**四段式生命周期在 20 系统中无完全对应者**。
- 最近对照：agentmemory（Ebbinghaus 衰减+TTL+importance evict，无状态机/无回滚）；Zep（validity 窗口失效，无衰减强化）；Letta（git-backed 备份，无衰减）。

## 结论
- 兰台分类学广度达标（CoALA 全覆盖），短板在**情景记忆深度**（episode 影子）与**类型间转化路径的评测证据**（技能结晶、经验→规则的实际转化率无公开数据）。
- 生命周期是兰台最大差异化面，评分维持 4.0（含 checkpoint/回滚的结构性优势），但需 E2 用例把「衰减/归档/撤回」变成可复现数字。

## 决策
- D18：路线图维持「episode 影子→离线消融」节奏，不提前接评分（与 Mem-α RL 观察项合并为一条）。
- D19：新增差异化主张：「全生命周期可治理的记忆状态机」为兰台对外叙事第一句。
