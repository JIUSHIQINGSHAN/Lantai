# 01 记忆广播链（烽燧，v0.11）

借鉴源：aiduMEI v18.3 `ducky/pipeline/memory_broadcast.py`——seed 记忆逐层触发关联记忆的
传播链（端点 /recall_chain，深度 3 / 分支 3 / 最低分 0.3 / 总量 20）。

## 设计

- `lantai/ops/recall_chain.py::build_recall_chain(seed_text, max_depth=3, branch=3, min_score=0.3, total_max=20)`
  BFS 逐层传播（见 spec.md）。
- REST `GET /recall/chain` + MCP `recall_chain`（工具 39 → 40，只读）。
- 明确不吸收：workspace 冷记忆自动清理、J-lens 整包、Ignition 双路径。

## 测试要求

- 纯函数校验 / 空库 / 空 seed / 多跳传播 / 自匹配排除 / 总量封顶 / min_score 过滤；
  真实 SQLite+FTS + 本地 ngram 嵌入 + 假向量库（仅替换外部网络）。

## 状态：resolved（2026-08-12，随提交推送）

### 实现记录

- `lantai/ops/recall_chain.py`（新）：`validate_chain_params`（REST/MCP/纯函数三处共用）+
  `build_recall_chain`（BFS 传播链：分数降序取 branch、自匹配锚点整链排除、跨层去重、总量封顶、单条失败只缺层）
- `lantai/api/routes_recall_chain.py`（新）：`GET /recall/chain`（只读，422 校验失败）；api/__init__ + api_server 受保护路由注册
- `scripts/mcp_server.py`：`recall_chain` 工具（TOOLS/TOOL_HANDLERS/handle_recall_chain）
- `tests/test_recall_chain.py`（新）：7 例；`tests/test_mcp.py` 工具数断言 39 → 40

### 验证

- `pytest tests/test_recall_chain.py -q` → 7 passed
- 回归 `pytest tests/test_mcp.py tests/test_graph.py tests/test_vision.py -q` → 58 passed（1 例为工具数断言同步）
- 命名纪律：「烽燧」已登记 CONTEXT.md 词汇表 + ADR-0013 映射表（先登记后使用）

### 明确不吸收

- workspace 冷记忆自动清理（兰台 archived/tier 已有）、J-lens 整包（trace/recall_report 已覆盖）、
  Ignition 双路径（trace 体系已覆盖）
