# ADR-0066: 起居注（Qijuzhu）时序记忆脉络与每日全息纪事引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥 & Claude  
**来源**: `docs/adr/0013-naming-system.md` 候选意象转正 & `.scratch/v0.45-next-window-2026-11/`

---

## 背景

在兰台 AI Agent 长期记忆系统的演进中，记忆已实现了多维空间编目（目次 · ADR-0065）、长程任务情境锚定（卷宗 · ADR-0063）与高频规则直写（直书 · ADR-0064）。
然而，在**时间轴与记忆演化叙事连贯性（Narrative Continuity）**维度上，既有机制存在明显的结构性缺陷：
1. **既有日结（`digest_worker.py`）过于扁平且静态**：仅在清晨离线跑一次纯指标统计并输出一份静态 Markdown 文件，不提供细粒度事件溯源与结构化时序 API，Agent 无法在对话中按需查询任意历史时段的认知变迁；
2. **多源认知演进缺乏统一时间轴串联**：新摄入事实、确立的程序铁律、推进的卷宗里程碑、解决的参商冲突以及演化提案，彼此孤立保存在各自数据表中，没有统一的时序脉络（Chronicle Stream），导致 Agent 难以一眼纵览「今天/近期兰台记忆发生了哪些关键决策跃迁」；
3. **时序认知上下文断层**：在长对话跨日恢复或需要回顾近期经验时，缺乏轻量级、经过樊篱防注入安全围栏包裹的「日度全息演进纪事」，Agent 容易遗忘晚近发生的重要操作与规则约束。

---

## 决策

### 1. 传统意象命名转正（ADR-0013）
- 正式中文名：**起居注**（Qijuzhu）。
- 出处：《汉书·艺文志》「古之人君，左史记言，右史记事」，汉唐以降历朝均设起居注，逐日详录言行得失、政令变迁，为国史实录之源。
- 英文代号：`QijuzhuEngine` / `Chronicle` / `qijuzhu`。
- 登记入口：`CONTEXT.md` 词汇表与 `docs/adr/0013-naming-system.md`。

### 2. 核心起居注聚合引擎（QijuzhuEngine）
在 `lantai/cognition/qijuzhu.py` 建立 `QijuzhuEngine`，提供低延迟、多源事件融合、行级租户隔离的时序纪事能力：
1. **多源时序认知事件编排（Multi-Source Chronicle Aggregation）**：
   - 摄入事实事件（`memory_ingested`）：高价值新增事实、偏好与经验；
   - 程序铁律事件（`rule_established`）：由直书（Zhishu）确立的操作禁令与配置规则；
   - 卷宗推进事件（`dossier_milestone`）：卷宗创建、阶段里程碑达成与办结；
   - 参商仲裁事件（`conflict_arbitrated`）：矛盾冲突的仲裁裁决与胜出事实；
   - 演化提案事件（`proposal_evolved`）：反思蒸馏自动应用的提案；
2. **全息日度简报与时间轴脉络（Daily Chronicle & Timeline）**：
   - `get_daily_chronicle(date)`：产出指定日期的变迁指标、宏观叙事 summary 与结构化事件清单；
   - `get_timeline(days, limit)`：提供滑动时间窗口内的时序演化流，支撑 Agent 跨日连续思考；
3. **樊篱数据围栏防御（Fanli Protection）**：
   - 所有事件正文、摘要与笔记全部经 `wrap_as_data` 隔离，严防提示注入攻击；
4. **行级租户与属主安全隔离（Tenant/ACL Guard）**：
   - 严格继承 `tenant_id`、`user_id` 与 `allowed_lanes` 过滤，确保多租户安全。

### 3. API、MCP 工具与认知上下文切面
1. **REST 路由**：
   - `GET /qijuzhu/today`：获取今日起居注全息纪事；
   - `GET /qijuzhu/timeline`：获取滑动时间轴事件脉络；
   - `GET /qijuzhu/dates/{date_str}`：获取历史上某一天的起居注回放；
2. **MCP 工具扩展**：
   - 注册 `qijuzhu_chronicle` 工具，MCP 工具池由 78 个扩充至 79 个，广告与分发字典 1:1 双向对齐；
3. **认知上下文装配切面（CognitiveContext）**：
   - 在 `CognitiveContext` 引入 `qijuzhu_digest` 切面，支持在任务开始时自适应注入近期时序脉络。

---

## 影响

- 兰台在时间维度形成完整的时序变迁叙事能力，弥补空间（目次）与任务（卷宗）之外的时间脉络短板；
- 为 Agent 提供连续统一的时序记忆脉络感知。
