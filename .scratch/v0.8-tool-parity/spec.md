# v0.8 — 工具面第三波（作者 MCP 37 工具反查收尾）

来源：aiduMEI v18.3.0 `mcp_server.py` 37 工具 vs 兰台 34——剩余缺口逐个判定。

## 本波吸收（4 个，全部「已有服务无暴露」）

- reflect_run（作者 mem_reflect）：`reflector.run_reflect_once` 已有（调度器在跑）
  但无任何 REST/MCP 入口；暴露 MCP 让 Agent 可主动触发（产出 pending 提案或
  高置信 auto-apply，宁 miss 不脏写）。
- mem_usage（作者 mem_usage）：REST /usage 内联实现 → 提取 `ops/usage.collect_usage`
  服务，REST/MCP 共用（7 天每日新增，缺日补零）。
- core_memory_get（作者 core_memory_get/list）：`memory_service.get_core_memory`
  已有，MCP 补齐（identity/task/policy 核心记忆块只读）。
- verbatim_search（作者 facts_search）：REST /verbatim/search 已有（原文直存专用
  检索通道），MCP 补齐（FTS+向量，不进混合召回）。

## 明确不吸收（最终收尾判定）

- mem_update / mem_delete / mem_delete_all：硬编辑/删除无审计链，Checkpoint 回滚
  与衰减归档已覆盖修正路径。
- mem_observe：对话观察由 add_dialogue + 摄取链覆盖。
- mem_persona / facts_preferences：persona 归 core-memory identity 块。
- facts_entities / session_*：实体索引与会话生命周期归宿主侧（provenance 覆盖）。
- code_impact / code_graph_view：Code Graph 正交。
- autodream_status / raw_stats：status 由 stats/overview 覆盖；raw 统计并入 mem_stats。

## 票据

- 01-tool-parity：4 个 MCP 工具 + collect_usage 服务提取（34 → 38）
