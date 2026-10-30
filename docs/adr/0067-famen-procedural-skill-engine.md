# ADR-0067: 法门（Famen）——程序性技能资产与执行流水线引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥  
**来源**: 针对程序性记忆（Procedural Memory）缺乏动态匹配与结构化 SOP 执行流水线的长跑演化需求。

---

## 背景

在 Agent 长程任务与复杂工程落地中，不仅需要陈述性事实（Facts）与情境目标（Juanzong），更需要明确的**程序性技能（Procedural Skills / SOP）**指导执行步骤。
目前兰台在程序性记忆领域存在如下断层：
1. `SkillCrystal`（技能结晶）主要记录了从对话或高频聚类中提炼的文字流程，但缺乏标准步骤对象契约；
2. `MemoryItem` 虽支持 `structure={"name", "description", "steps"}`，但检索链路多退化为非结构化文本；
3. Agent 面对复杂任务时，无法通过一次轻量调用快速完成「任务意图 $\to$ 法门触发规则匹配 $\to$ 结构化 SOP 步骤注入」的闭环；
4. 步骤正文若不加安全数据围栏，容易受到提示注入攻击，诱导 Agent 偏离既定规程。

---

## 决策

确立「法门」（Famen）程序性技能资产与执行流水线体系：

| 维度 | 决策 | 规范说明 |
|---|---|---|
| **命名出处** | 法门（Famen） | 出自《法华经》「深入法门，通达诸法」，喻指可循之法、可行之门与标准作业程序；见 [ADR-0013](0013-naming-system.md) 候选池转正，登记于 `CONTEXT.md`。 |
| **多源资产归一** | `SkillAsset` 规范数据契约 | 统摄 `SkillCrystal(status in ('approved', 'promoted'))` 与 `MemoryItem(decay_class='procedural' or lane='rule')`，统一萃取为 `id, name, trigger_rule, steps, source_type, metadata`。 |
| **动态触发匹配** | `FamenEngine.match_skills` | 纯内存/轻量 SQL 结合任务关键词词袋重叠度与触发规则匹配，按相关度倒序截断返回。 |
| **樊篱安全包裹** | 全链路 `wrap_as_data` | 技能描述、前置检查与具体 SOP 步骤在进入 Agent 提示词前，强制以 `<memory_data id="...">` 包裹，彻底杜绝指令注入。 |
| **行级租户隔离** | `_check_access` 纪律 | 严格校验 `tenant_id` 与 `user_id`，公共结晶（`shared_scope='swarm_shared'`）安全共享，私有资产隔离，宁 miss 不脏写。 |
| **API 与 MCP 扩展** | REST + MCP 80 工具池 | 落地 `/famen/match`、`/famen/skills/{id}`、`/famen/list`，并注册 `famen_skill` MCP 工具，达成 80 工具里程碑。 |
| **认知上下文切面** | `CognitiveContext.famen_digest` | 上下文生成时可挂载匹配到的 SOP 列表，输出为清晰的结构化步骤块。 |

---

## 影响

- 提升 Agent 面对多步骤工程任务的可靠性与执行确定性；
- 形成完整的「摄入/结晶 $\to$ 审核 $\to$ 法门匹配 $\to$ SOP 上下文注入」闭环；
- 系统 MCP 工具总数扩充至 80 个。
