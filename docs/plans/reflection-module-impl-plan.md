# 反思模块实现计划（Reflection Module Implementation Plan）

> 依据：docs/plans/reflection-module-spec.md（v1 = 方案 B：蒸馏 + 健康审计闭环）
> 日期：2026-08-11。方式：测试先行（TDD），遵守 AGENTS.md 测试纪律（核心函数必须有不 mock 内部逻辑的冒烟测试）。

## 1. 步骤顺序（每步红→绿）

1. **配置**：`lantai/core/settings.py` 新增 `REFLECT_*` 配置块（默认全关/保守）
2. **Prompt**：`lantai/llm/prompts.py` 新增 `REFLECT_CURATOR_SYS` / `REFLECT_REJECTER_SYS`（strict JSON）
3. **核心逻辑**：`lantai/evolution/reflector.py` 新增 `health_scan` / `_importance_waterline` / `_curate` / `_reject` / `propose_from_reflection` / `run_reflect_once`（保留 record_feedback 不动）
4. **提案落地扩展**：`lantai/evolution/promoter.py` 的 `apply_proposal` 新增 `deprecate` / `merge` 分支（add/update 语义不动，门面铁律）
5. **Worker 与调度**：新增 `lantai/workers/reflect_worker.py`；`lantai/core/scheduler.py` 注册 reflect job（`REFLECT_ENABLED` 时，cron hour + minute=1 与 digest 错开）
6. **Digest 扩展**：`lantai/workers/digest_worker.py` 报告追加反思统计行（反思提案 = `candidate_id IS NULL` 的 MemoryProposal，零迁移标识）
7. **测试**：`tests/test_reflect.py`（14 个用例，见 spec 第 4 节）

## 2. 关键设计决策（实现级）

- 反思提案标识：`MemoryProposal.candidate_id IS NULL`（evolve 提案必有 candidate_id；反思提案无）——零迁移，digest 可直接统计。
- deprecate 语义：`valid_to = now` + `status = "archived"` + `MemoryEdge(relation="supersedes")` + checkpoint + 删 FTS/向量索引（归档退出检索）。
- merge 语义：主记忆 content 更新 + evidence 并集 + version+1；被合并记忆 archived + supersedes 边 + checkpoint。
- 自动应用：`confidence >= REFLECT_AUTO_APPLY_CONF(0.7)` 且 rejecter `risk=low` 且 accept → apply；`risk=medium` 强制 pending；`accept=false`/`risk=high` → 丢弃（status=rejected，宁 miss）。
- R3 冲突账本闭环：提案自动应用成功后，对应 open ConflictEvent 标 resolved（走 conflict_service）。
- 空闲日零 LLM：健康候选为空且水位不足 → 只 record_run("reflect") 退出。

## 3. 与 spec 的取舍说明

- `REFLECT_IMPORTANCE_WINDOW_DAYS=7` 为新增配置：水位用「最近 7 天新增记忆 importance 累加」近似「自上次 reflect 以来」（无持久化时间戳表，零新表原则；标注：近似，待观察）。
- 其余与 spec 一致。

## 4. 验收

- `python -m pytest tests/test_reflect.py -q` 全绿
- `python -m pytest tests/ -q` 全量回归不破坏既有 120 用例
- 无新表、无既有函数改名、既有 import 全绿（门面铁律）
