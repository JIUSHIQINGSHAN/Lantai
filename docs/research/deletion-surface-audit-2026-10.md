# S3 删除面盘点报告（P-a 预防性项）

> 2026-10-07 · 票据：`.scratch/llm-concurrency-gate/`（S1）同批计划项，源自
> `docs/research/upstream-f03-gap-and-plan-2026-10.md` §3.2 P-a
> 上游教训：aiduMEM f0.3++ 要求**全量删除必须显式 confirm，拒绝发生在 WAL/存储副作用之前**。

## 结论

**兰台没有「全量/一键清空记忆」端点——上游 confirm 闸的核心痛点不存在。**
删除面全部是**单条 + 归属校验 + 可回滚/有审计**形态，无需开 confirm 票。
两个观察项（O1 合并删源、O2 场景重建）如实记录，均非 P0。

## 全部物理删除点（正则枚举 `s.delete(|session.delete(`，产品代码 9 处）

| # | 位置 | 删什么 | 护栏 | 风险判定 |
|---|---|---|---|---|
| 1 | `routes_terminal.py:321-361` `DELETE /terminal/memory/{id}` | 单条记忆 | `ensure_can_delete` 归属校验 + FTS/向量同步 + 审计 | ✅ 单条有护栏 |
| 2 | `routes_terminal.py:547` `/terminal/merge` | 合并后删 source 记忆 | 双方各自 `ensure_can_delete` + supersedes 边留痕 | ⚠️ O1 见下 |
| 3 | `promoter.py:720` `delete_memory` | 单条（文档级联目标） | `ensure_can_delete` + docstring 自证「全库最具破坏性」 | ✅ |
| 4 | `source_service.py:195-205` `delete_document` | 文档 + chunk + 候选 + 边 + 无主记忆 | **先全量校验后删除**（forbidden 整体中止，一条都没删） | ✅ 教科书式 |
| 5 | `record_ops_service.py:537` 起复（撤销巩固） | supersedes 边 | 隶属 ADR-0052 事务 + checkpoint + 审计 | ✅ 边非记忆 |
| 6 | `scene_service.py:142` `rebuild_scenes` | **全量旧场景行** | 场景是派生数据（可幂等重建），记忆正文不动 | ✅ 派生数据 |
| 7-9 | `eval/`（3 处） | 评测夹具 | 非产品路径 | ✅ |

## 与上游 confirm 闸的逐条对照

| 上游要求 | 兰台现状 |
|---|---|
| 全量删除须显式 confirm | **无全量删除端点**（`/wipe /reset /clear /purge` 全仓 grep 零命中）；MCP 38 个工具无一删除类 |
| 拒绝发生在 WAL/存储副作用之前 | `delete_document` 已是「先校验后删，forbidden 整体中止」；`delete_memory`/`rollback` 在 `s.delete`/`setattr` **前**过 `ensure_can_delete` |
| 删除走墓碑/可恢复 | 兰台另有笔削四分法（ADR-0047）：删除是四态之一，撤回/归档可逆；checkpoint 可回滚 |

## 观察项（不动产品代码，仅记录）

- **O1 合并删源**：`/terminal/merge` 删 source 是业务语义（合并=吸收），有 supersedes
  边留痕，source 内容已拼进 target。若要更保守可改「archive source」——**收益低**，
  不动（宁 miss 不脏写的反向：不为不存在的病灶动刀）。
- **O2 场景重建**：`rebuild_scenes` 清空全量 `MemoryScene` 行——是派生数据的幂等重建
  语义，且记忆的 `scene_id` 一并清空重写，无信息丢失。**不需要 confirm**。
- **遥测清理**：`telemetry.py:134` `DELETE FROM operation_logs WHERE created_at < cutoff`
  是保留期维护清理（`MONITOR_RETENTION_DAYS`），审计面不涉及记忆正文。✅

## 遗留

无。本报告即 S3 的全部交付物；不开 confirm 票（盘点结论：无可保护的靶子）。
