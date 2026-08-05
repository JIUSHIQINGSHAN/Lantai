# 论文驱动参数调整建议——实施报告

> 依据 GPT-5.6 Sol 方案（`C:/Users/Asus/Desktop/GPT建议.txt`）实施，由 DeepSeek-V4-Flash 执行。
> 状态：**已实现，175 测试全绿**（原 120 + 新增 55）。

## 一、功能概述

论文摄入 → 批量窗口触发 LLM 建议生成 → 待审队列（pending）→ 人工审阅（批准/拒绝）→ 带 before/after 快照应用 → 可一键回滚。

**辅助模式，绝无自动应用路径。** 任何参数变更必须经 `/decision` 人工批准。

## 二、方案校准（GPT 方案 × 项目实际）

| 项 | GPT 方案 | 实际执行 |
|---|---|---|
| FK 引用 | `raw_document.id` | `rawdocument.id`（项目无 `__tablename__`，实际表名驼峰小写） |
| API 前缀 | `/api/...` | `/param-suggestions` 等（项目路由无前缀） |
| JSON 列 | `*_json` 字符串 | `Column(JSON)` dict（项目风格） |
| Migration | Alembic | 无，`create_all` 直建 |
| 主键 | `default_factory` | 项目风格外部 `new_id("psg")` |
| BEGIN IMMEDIATE | 显式锁 | SQLite 单写者 + 条件 UPDATE rowcount==1 + revision UNIQUE（同语义） |
| 时区 | — | SQLite 读回 naive datetime，统一 `replace(tzinfo=UTC)`（项目标准） |

## 三、文件清单

### 新增（11）
- `remembrance/parameters/__init__.py` — 门面导出
- `remembrance/parameters/registry.py` — 参数白名单（6 可调 + 物理排除 + 分组约束 + canonical hash）
- `remembrance/parameters/schemas.py` — LLM 输出判别联合 + API DTO（全 `extra="forbid"`）
- `remembrance/parameters/validation.py` — 快照/变更/LLM 输出三层校验（Decimal 精度、quote 子串）
- `remembrance/parameters/advisor.py` — user prompt 拼接 + LLM 调用（无 fallback）
- `remembrance/parameters/queue.py` — 论文入队/批量领取/卡死恢复
- `remembrance/parameters/runtime.py` — DB head 读取、settings 原位刷新（id 不变）
- `remembrance/parameters/service.py` — 审阅/回滚 CAS 事务
- `remembrance/workers/param_advice_worker.py` — 批量窗口触发生成
- `remembrance/api/routes_param_advice.py` — 6 个薄路由
- `tests/conftest.py` + 5 个 `tests/test_param_*.py` — 55 个测试

### 修改（8）
- `models/tables.py` — 4 张表（ParamAdviceRun / ParamAdvicePaper / ParamSuggestion / ParamOverride）
- `llm/prompts.py` — `PARAM_ADVICE_SYS`（18 条约束，Only output JSON）
- `core/settings.py` — `PARAM_ADVICE_*` + `PARAM_OVERRIDE_REFRESH_SECONDS` 控制参数
- `workers/ingest_worker.py` — paper 落库后幂等入队，摄入提交后触发 advice
- `core/scheduler.py` — advice worker（30min）+ runtime 刷新（5s）双 job
- `api/__init__.py` / `api_server.py` — 路由注册进 protected_routers；lifespan 启动加载 override
- 未删任何文件（ADR-0001 门面铁律）

## 四、API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/param-suggestions?status&limit&offset` | 建议列表 |
| GET | `/param-suggestions/{id}` | 详情（diff/证据/风险/验证计划） |
| POST | `/param-suggestions/{id}/decision` | `{"decision": "accepted\|rejected", ...}` 审阅 |
| GET | `/param-overrides?limit&offset` | 变更历史（追加式事件） |
| POST | `/param-overrides/{id}/rollback` | 仅 head apply 可回滚，追加 rollback 事件 |
| GET | `/runtime-params` | 当前生效六参数 + revision + hash |

错误码：`404 suggestion_not_found` · `409 suggestion_already_decided / snapshot_conflict / revision_conflict / rollback_conflict` · `422 registry_validation_failed`

## 五、安全设计（红线验收全过）

1. 无自动 accepted 路径 ✓
2. 非白名单参数（LLM/API/DB 三路）均无法应用 ✓（default deny）
3. LLM 失败无 fallback，转 retry（≤3 次）或 consumed ✓
4. 批准前复验 revision + snapshot hash（CAS）✓
5. 每次 apply/rollback 完整 before/after 快照 ✓
6. 回滚走追加事件，不删记录 ✓
7. `.env` 永不被写入 ✓
8. `id(settings)` 不变，旧 import 全绿 ✓
9. 核心函数全有冒烟测试（仅 mock chat_json 网络层）✓
10. 原 120 + 新 55 全绿 ✓

## 六、使用流程

```bash
# 1. 论文源（arxiv 已内置）
curl -X POST http://127.0.0.1:8767/sources \
  -d '{"kind": "arxiv", "config": {"query": "cat:cs.AI", "max_results": 10}, "enabled": true}'

# 2. 等 ingest 拉取 + 攒批（5 篇或 7 天）→ 自动生成建议
curl http://127.0.0.1:8767/param-suggestions

# 3. 看详情（证据/风险/验证计划）→ 批准或拒绝
curl -X POST http://127.0.0.1:8767/param-suggestions/psg_xxx/decision \
  -d '{"decision": "accepted", "note": "先跑 Recall@10 验证"}'

# 4. 生效后不满意 → 回滚
curl -X POST http://127.0.0.1:8767/param-overrides/pov_xxx/rollback -d '{"note": "MRR 下降"}'

# 5. 查当前生效参数
curl http://127.0.0.1:8767/runtime-params
```

## 七、环境注意（重要）

- 本地 hermes venv 的 `opentelemetry-exporter-otlp-proto-grpc==1.44.0` 与 `-common==1.39.1` **版本错配**，chromadb import 即崩（`ModuleNotFoundError: _exporter_metrics`）。
- 测试已在 `tests/conftest.py` 打 otel stub 绕过（仅测试环境）。
- **生产建议**：升级 common 至 1.44.0 对齐 grpc；或使用 Docker（`Dockerfile` 独立环境无此问题）。pip 升级失败是沙箱回收站限制，需在正常终端执行：
  `pip install --upgrade opentelemetry-exporter-otlp-proto-common==1.44.0 opentelemetry-proto==1.44.0`

## 八、后续可做（不在本次范围）

- 渠道扩展（产业博客 RSS / 评测榜单）——按《渠道评估矩阵》分层接入
- 参数白名单扩容（衰减半衰期等"价值观参数"需更谨慎论证）
- 建议密度统计仪表盘（/stats 扩展）

---

# v2 可信度体系深化（论文可信度三层过滤 → 四段式生命周期）

> 依据第二轮 GPT 方案（五方向）实施。**当前进度：Step 1 完成，198 测试全绿。**

## 执行裁定（方案 × 项目实际校准）

| 项 | GPT 方案 | 实际执行 |
|---|---|---|
| 新表主键 | `id: int` 自增 | `str + new_id()`（项目全表 str，FK 同型必需） |
| dry-run 参数注入 | contextvars | `hybrid_search` 加可选 `param_overrides`（sync 线程池 contextvars 不传递） |
| 信号传递 | arxiv 适配器内落库 | 解析借道 `doc.meta`，`ingest_worker` 落库后写独立表 |
| 检索埋点 | service 出口 | `/search` 路由层（项目无检索 service） |
| V1→V2 输出结构 | 方案内部矛盾（2.5 追加式 vs 5.2 批量） | 裁定**批量结构**（suggestions[]/abstentions[]/contradictions[]）——矛盾分区必需 |
| used_ids 回填 | 生成侧回填 | 系统无生成侧，默认空 + 诚实标 `unavailable` |
| 查询集数据源 | — | 依赖 RetrievalEvent 积累，无数据时 dry-run 标 `unavailable` |

## Step 1 已交付（质量信号，零行为变更）

- `parameters/paper_signals.py` — 纯函数：extract/classify_venue/classify_tier/compute_staleness（NEGATIVE 优先）
- `parameters/trust_models.py` — `PaperQualitySignal` 表（来源锁 arxiv_atom）+ 视图模型
- `parameters/signal_service.py` — upsert_from_draft（缺失一律 tier D 保底）/ load_signal_views / resolve_gating
- `ingestion/arxiv.py` + `workers/ingest_worker.py` — 信号解析与落库接入
- `core/settings.py` — 方向一阈值（TIER_WEIGHT / QUORUM_BY_TIER / DELTA_BUDGET_FACTOR / 时效参数）
- 测试：`tests/test_param_signals.py` 23 个（含真实 arXiv Atom 固件）——全绿

## Step 2-3 已交付（219 测试全绿）

**Step 2（信号进链路 + 矛盾显式化）**
- `PARAM_ADVICE_SYS_V2` — 批量结构（suggestions[]/abstentions[]/contradictions[]）+ 规则 19-22
- `parameters/validation.py` 追加 5 校验器：污染检测 / 主证据资格 / quorum / 权重只降 / 预算缩放
- `parameters/schemas.py` — BatchParamAdvice / ContradictionItem
- `parameters/trust_models.py` — `ParamContradictionReport` 表（矛盾参数禁止 apply）
- worker 批量入库：逐条建议 fingerprint 去重 + 矛盾报告落库
- 测试：`tests/test_param_v2.py` 18 个

**Step 3（检索埋点）**
- `models/tables.py` — `RetrievalEvent` 表（弱标注源）
- `observability/retrieval_log.py` — log_retrieval / backfill_used_ids（失败零侵入）
- `api/routes_search.py` — /search 出口埋点（gate 拦截也记录 zero_result）
- 测试：`tests/test_retrieval_log.py` 3 个

## Step 3 后半-8 待办

- 查询集构建（EvalQuerySet/Item）+ dry-run 评估（eval_metrics/eval_runner）
- 时效复查（override_review_worker）+ 影子观察期（ShadowWindow + DEDUP shadow-only + 护栏回滚）
- 验证回流（SignalReliabilityStat 只降权）+ 全量回归
