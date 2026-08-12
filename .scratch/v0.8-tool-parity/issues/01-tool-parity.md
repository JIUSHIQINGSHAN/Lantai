# 01 工具面第三波（reflect_run / mem_usage / core_memory_get / verbatim_search）

借鉴源：aiduMEI v18.3.0 `mcp_server.py` 工具面（37）vs 兰台（34）剩余缺口收尾。

## 设计

- `ops/usage.py::collect_usage(days)` 纯聚合服务（原 routes_health.usage 内联逻辑
  提取，REST /usage 改调共用）；测试沿用 test_usage.py（路由行为不变）。
- MCP 4 工具：
  - `reflect_run`：包装 `reflector.run_reflect_once`（无参，dry-run 不支持——
    反思本身产出待审提案或高置信 auto-apply；REFLECT_ENABLED=false 时跳过）
  - `mem_usage`：包装 collect_usage（缺省 7 天）
  - `core_memory_get`：包装 get_core_memory（只读 blocks 列表）
  - `verbatim_search`：包装 hybrid_search(memory_types=["verbatim"], use_rerank=False)
- 工具数 34 → 38。

## 测试

`tests/test_mcp.py`：tools/list 计数 38；mem_usage 聚合正确（真实 SQLite）；
verbatim_search 走 verbatim 通道；core_memory_get 只读；reflect_run 冒烟
（mock LLM/embedding/向量存储，只验证接线与 skipped 路径）。

## 状态

resolved（2026-08-12，随 feat(absorb) 提交推送）。
