# 09 MCP 工具扩容（第二波）——反查已有服务补齐暴露

借鉴源：aiduMEI v18.x `mcp_server.py` 共 37 个 MCP 工具；兰台 21 → 28。
原则：**不新增服务，只把已落地的服务能力暴露成 MCP 工具**；每个工具都有
不 mock 冒烟测试（真实 SQLite + FTS，仅 mock embedding/向量存储两个外部依赖）。

## 新增工具（7）

| 工具 | 对应兰台服务 | 作者对应工具 |
|------|--------------|--------------|
| mem_recent | `memory_service.list_memories(status="active")` | mem_recent |
| mem_stats | `ops.overview.get_overview`（只读聚合） | mem_stats |
| mem_health | SQLite 可读 + vector_store 探活（不触发外部 LLM） | mem_health |
| autodream_report | `evolution.autodream.run_autodream_once(dry_run=True)`（预演不写库） | autodream_report |
| autodream_trigger | `run_autodream_once(dry_run=False)`（落 pending 提案，人工裁决才应用） | autodream_trigger |
| proposals_list | `evolution_service.list_proposals` | mem_reflect / proposals 流 |
| proposal_decide | `evolution_service.decide_proposal`（approve 先落 Checkpoint 可回滚） | mem_reflect 裁决流 |

## 明确不吸收（登记理由）

- mem_delete / mem_delete_all：硬删除无审计链，与「可审计、可回滚」原则冲突；
  修正路径由 Checkpoint 回滚 / 衰减归档覆盖。
- mem_update：原地编辑破坏版本链；兰台以提案 + Checkpoint 回滚替代。
- session_start/end/list/report：会话生命周期属宿主侧，兰台以 add_dialogue +
  provenance 时间戳继承覆盖。
- mem_observe / mem_reflect：依赖 LLM 的实时反思为后续赛道（reflect 已有 REST，
  等 MCP 侧 agent 身份传递就绪再暴露）。
- code_impact / code_graph_view：Code Graph 与记忆系统正交（沿用既有「不吸收」结论）。
- facts_* / core_memory_* / knowledge_tree / crystals_*：已有 lanes / verbatim /
  core-memory 覆盖；树状图谱与结晶为后续赛道。

## 测试

`tests/test_mcp.py` 追加 8 例：tools/list 计数 28；mem_recent 只读过滤 archived；
mem_stats 聚合正确；mem_health ok；autodream_report dry-run 不写库；
autodream_trigger → proposals_list 闭环；proposal_decide reject 记 decision_reason /
approve 应用出新记忆。全部不 mock 内部逻辑。

## 状态

resolved（2026-08-12，随 feat(absorb) 提交推送）。
