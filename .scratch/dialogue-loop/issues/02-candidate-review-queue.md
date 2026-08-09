# 02 - Candidate Review Queue 候选可见队列

Status: resolved
Type: task
Blocked by: (none)

## 目标

被闸门拒绝的候选不再静默丢弃，进待审队列；提供 list/review 入口；TTL 自动归档。

## 范围

- `memorycandidate` 加 `review_due_at DATETIME NULL`；status 扩展 `pending_review`
- 现有 reject 路径改为入队（gate 决策层小改，语义：校验失败不自动修正，交用户决策）
- REST：`GET /candidates/pending`、`POST /candidates/{id}/review`（approve→proposal 链，reject→归档）
- MCP：`candidates_pending` / `candidate_review`
- 每日 TTL 任务：超龄（CANDIDATE_TTL_DAYS=7，settings）pending_review → rejected
- 铁律文档：AGENTS.md「宁 miss 不脏写」补"进待审队列"语义

## 验收

1. reject 候选出现在 pending 列表
2. approve → 进入 proposal 链；reject → 标记归档
3. 超龄候选自动归档，digest 可统计
4. 核心函数（list/review/ttl）有不 mock 冒烟测试

## 相关文件

remembrance/models/tables.py、remembrance/gate/decision.py、
remembrance/api/routes_candidates.py（新）、scripts/mcp_server.py、
remembrance/workers/digest_worker.py（TTL 任务）、tests/test_candidate_queue.py（新）

## Answer（2026-08-09 已实现，全量 409 测试全绿）

实现内容：
- 数据模型：`memorycandidate.review_due_at DATETIME NULL` + status 扩展 `pending_review`；
  `storage/db.py` 幂等 ALTER TABLE 迁移（老库自动加列）。
- settings：`CANDIDATE_TTL_DAYS=7`、`CANDIDATE_TTL_CRON_HOURS=24`（零硬编码）。
- 新 service `remembrance/services/candidate_service.py`：
  - `enqueue_rejected`：reject 入队（pending_review + due=now+TTL），幂等；
  - `list_pending_candidates`：按 review_due_at 升序；
  - `review_candidate`：approve → propose_from_candidate + apply_proposal 立即闭环
    （用户已裁决，不重复走 gate）；reject → 归档（rejected，清空 due）；
  - `run_candidate_ttl_once`：超龄自动归档。
- 改造 `evolve_worker.run_evolve_once`：REJECT 不再置 rejected，改调 enqueue_rejected。
- REST：`GET /candidates/pending`、`POST /candidates/{id}/review`（routes_candidates.py，
  已注册 protected_routers）。
- MCP：`candidates_pending` / `candidate_review`（scripts/mcp_server.py）。
- 每日任务：`remembrance/workers/digest_worker.py::run_candidate_ttl` 挂 APScheduler
  （hours=CANDIDATE_TTL_CRON_HOURS）；Ticket 03 digest 将扩展此文件。
- 文档：AGENTS.md 铁律补「进待审队列」语义；CHANGELOG Unreleased 记录。

验收对照：
1. ✅ reject 候选出现在 pending 列表（低置信度路径 + TTL 测试覆盖）
2. ✅ approve → 提案链并应用（cand→gated，proposal→applied）；reject → 归档
3. ✅ 超龄自动归档（run_candidate_ttl_once 冒烟测试；digest 统计在 Ticket 03）
4. ✅ 核心函数不 mock 冒烟测试：tests/test_candidate_queue.py 13 例（service 层真实内存 SQLite，
   仅 mock 外部 LLM/embedding/向量存储基础设施）+ test_mcp.py 新增 3 例

实现说明/偏差：
- approve 采用「直接应用」而非「提案挂起二次审批」——队列本身就是人工审查环节，
  二次审批会重复打扰用户；仍可经 checkpoint 回滚。
- 新增 CANDIDATE_TTL_CRON_HOURS 调度阈值（原 ticket 只列 TTL_DAYS，为满足零硬编码补上）。
