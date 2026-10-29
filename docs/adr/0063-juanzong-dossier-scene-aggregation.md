# ADR-0063: 卷宗（Juanzong）长程复杂任务情境聚合与目标上下文锚定

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥 & Claude  
**来源**: `docs/adr/0013-naming-system.md` 候选意象转正 & `.scratch/v0.42-next-window-2026-11/`

---

## 背景

在经历格物（因果拓扑与反事实推演）与参商（深度矛盾与一致性仲裁）后，兰台系统具备了精密的因果回溯与逻辑判断能力。然而，在面对跨越数天、多会话交互的长程复杂工程任务（如大型架构演进、故障排查战役、深度调研）时，既有系统暴露出两个明显短板：
1. **静态场景聚类（MemoryScene / ADR-0012）的无状态局限**：原 `MemoryScene` 是基于词向量余弦距离构建的无状态事实簇，无法记录任务目标（Goal）、阶段里程碑（Milestones）与演进状态（Active / Completed / Archived）；
2. **长程对话中的注意力涣散与目标漂移**：常规召回只按照单次查询 query 的局部相似度拉取 top_k，导致与当前长程大任务高度相关的事实与历史经验无法优先聚焦，Agent 容易“只见树木、不见森林”，遗忘整体战略意图。

---

## 决策

### 1. 传统意象命名转正（ADR-0013）
- 正式中文名：**卷宗**（Juanzong）。
- 出处：唐代白居易《新乐府》「卷宗堆案墨翻盆」，官府按案归档、统摄全貌之档卷。
- 英文代号：`JuanzongEngine` / `Dossier`。
- 登记入口：`CONTEXT.md` 词汇表与 `docs/adr/0013-naming-system.md`。

### 2. 卷宗核心数据模型
- 引入 `Dossier`（长程任务卷宗实体）：
  - `id`: ULID 唯一标识；
  - `title`: 任务卷宗案名；
  - `goal`: 顶层战略目标与完成标准；
  - `status`: `active`（进行中） / `completed`（已办结） / `archived`（封藏）；
  - `milestones`: 里程碑列表（`id`, `title`, `status: pending|in_progress|done`, `notes`）；
  - `memory_ids`: 挂载在该卷宗下的关联核心事实、规则与教训；
  - `metadata`: 扩展属性（包含关键产物链接、涉案实体等）；
  - `tenant_id` / `user_id`: 行级租户安全收窄。

### 3. 情境记忆聚焦算法（Dossier Context Focusing）
当 Agent 指定或处于某活动卷宗上下文时，执行定向聚焦检索：
- **核心保障层**：卷宗显式绑定的 `memory_ids` 按重要性与时效无条件优先纳入；
- **关联扩展层**：沿绑定的记忆节点向外关联（如因果链、实体共有原子事实、冲突裁决），根据当前 query 融合召回；
- **目标显影层**：向认知上下文 `CognitiveContext` 注入卷宗案名、目标愿景与当前里程碑进度，并全量施加樊篱（`<memory_data>`）防护。

### 4. 门面铁律与向后兼容（ADR-0001）
- `JuanzongEngine` 不废弃或修改既有 `MemoryScene` 表结构，`MemoryScene` 仍用于底层纯文本聚类；
- 卷宗是认知层的高阶情境抽象，二者并行不悖。

---

## 影响

- Agent 具备了统摄跨会话大任务的记忆抓手，彻底解决多轮长程交互中的“目标迷失”问题；
- 认知上下文新增 `active_dossier` 聚焦切面，为大模型提供连贯且受保护的情境信息。
