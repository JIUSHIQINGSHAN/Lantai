# ADR-0060: 润物（Runwu）自适应认知中间件与无感上下文装载引擎

## 状态
已接受 (Accepted)

## 背景
1. **现状与痛点**：
   在兰台的记忆与认知体系中，记忆的召回与使用通常依赖 Agent 的显式外呼（如 `search`、`cognitive_context`、`memory_tanyi_probe` 等）。然而，在很多高频关键操作（例如修改系统核心配置、执行敏感写操作、处理冲突事实）中，Agent 常常因未主动发起工具调用而遗漏关键原则，或重蹈历史覆辙。
2. **v0.4 规范落地承接**：
   在 `docs/plans/v0.4-lifecycle-middleware.md` 与 `CONTEXT.md` 词汇表中，兰台早前规划了「润物」（Runwu · 杜甫《春夜喜雨》「随风潜入夜，润物细无声」），意在不阻塞主链路的前提下，为对话/请求静默装载最相关的规则与历史教训。
3. **技术缺口**：
   原有 `lantai/runtime/middleware.py` 仅为雏形代码，存在四项关键缺陷：
   - 无行级租户隔离：未支持 `tenant_id` 与 `user_id`，存在跨租户数据越权隐患；
   - 缺乏精细预算控制：无字符预算（Character Budget）截断保护，极端情况下会挤占上下文；
   - 缺乏主动探针联动：未融合探颐巡检的主动求证探针（`clarification_probes`）；
   - 测试纪律缺口：该模块历史测试覆盖为 0，缺乏核心函数不 mock 冒烟单测。

## 决策
1. **构建独立核心引擎 `RunwuEngine` (`lantai/runtime/runwu.py`)**：
   - 接收 `db` 会话与可选配置参数（`char_budget`, `max_rules`, `max_failures`, `max_probes`）；
   - `extract_summary(task, user_id, tenant_id, ...)`：纯 SQL/内存计算，严禁外部网络/LLM 阻塞外呼；
   - 多维装载：
     - **规则（Rules）**：过滤有效且未退役的规则，按置信度与相关性排序；
     - **教训（Failures）**：提取与 task 相关的已知失败模式与教训（`FailureRecord`），防止重蹈覆辙；
     - **探针（Probes）**：集成探颐（Tanyi），若当前归属下有未决冲突或低置信度事实，顺带带出轻量求证追问；
2. **樊篱（Fanli）数据围栏防护**：
   - 提取的全部规则与教训必须经过 `wrap_as_data` 处理，严防提示词注入（OWASP LLM01）；
3. **严格字符预算与紧凑输出**：
   - 默认预算 350 字符，单条项目字符严格截短，并按优先级动态裁剪，绝不挤占模型上下文；
4. **保持兼容与门面规范（ADR-0001）**：
   - 改造 `lantai/runtime/middleware.py` 的 `build_cognitive_summary`，由其代理给 `RunwuEngine`，保证签名与返回向前兼容。

## 影响
- 为系统提供自适应装载底座，大幅降低 Agent 上下文维护负担；
- 遵循「宁 miss 不脏写」，异常时静默降级（输出空或局部摘要），绝不中断业务请求；
- 补齐不 mock 冒烟测试，消灭未测代码死角。
