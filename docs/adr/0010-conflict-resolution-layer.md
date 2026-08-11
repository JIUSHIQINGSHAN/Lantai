# ADR-0010: 冲突消解确定性层（规则层 + LLM 层双通道）

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [发展方向调研](docs/research/direction-research-report.md)「立即做」+ v0.5 优化方案 P0-2

## 决策

冲突消解由「仅 LLM」改为「**确定性规则层优先 + LLM 回落**」双通道，要点：

1. **规则层（零 LLM、可复现）**：`gate/conflict_rules.py::check_rules` 纯函数——互斥规则集
   `CONFLICT_MUTEX_RULES`（settings 可配，pair 内两项互斥：状态开关/开关/版本变更），
   new 命中 A 且 existing 命中 B（或反向）→ 确定性冲突（severity=high）。
2. **账本可溯源**：规则命中在闸门决策事务内写 `ConflictEvent`（memory_id / incoming_ref / rule_name /
   detail / status），REST `GET /conflicts` + `POST /conflicts/{id}/resolve` 与 MCP
   `conflicts_list` / `conflict_resolve` 提供审计与人工裁决（resolved / dismissed）。
3. **LLM 回落（降级不阻断）**：规则未命中才调用现有 `check_contradiction`；LLM 异常返回 contradicts=false
   的既有兜底保持不变。规则命中时**短路 LLM**（省调用、可复现）。
4. **闸门决策语义不变**：冲突仍走 `ARCHIVE_CONFLICT` → 候选进待审队列（锦囊），人工裁决；
   确定性命中同样不自动应用（宁 miss 不脏写）。

## 理由

- **行业共识**：时间/关系/矛盾消解是记忆系统公认薄弱点（Zep 的唯一显著优势即「what changed when」）；
  纯 LLM 判断慢、不可复现、依赖外部 API——规则层先判可解释。
- **零硬编码**：规则集、开关全部进 settings（`CONFLICT_RULES_ENABLED` / `CONFLICT_MUTEX_RULES`）。
- **最小侵入**：只改 `gate/decision.py` 的冲突循环 + 新增 1 表 + 1 service；LLM 通道与提案链完全不动。
- **安全**：账本只记录不裁决——人工闸门铁律不被绕过（resolve 只标记处置，不改变记忆状态）。

## 影响面

- `lantai/models/tables.py`：新增 `ConflictEvent` 表（create_all 自动建，无需列迁移）。
- `lantai/gate/decision.py`：冲突循环改为「规则 → LLM 回落」，命中写账本（同事务提交）。
- `lantai/services/conflict_service.py`：list / resolve（新）。
- `lantai/api/routes_conflicts.py` + `api_server.py` / `api/__init__.py` 注册；`scripts/mcp_server.py` 两工具。
- **明确不做**：不做自动属性覆盖写回（override 语义留待提案链人工审批）；不做多规则加权打分。

## 已知限制

- 规则为子串匹配，仅覆盖用户显式配置的互斥对；语义级矛盾仍依赖 LLM 回落。
- 账本只对「确定性规则命中」落库，LLM 判定的矛盾不落账（避免外部 API 抖动刷账）。
