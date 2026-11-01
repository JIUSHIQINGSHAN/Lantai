# 兰台方案审计（第三轮，2026-10-31）

对象：JIUSHIQINGSHAN/Lantai master a5502058（v0.64，PR #18 之后没有新提交，PR #18 仍未合并）。本轮代码分支 `audit/plan-audit-r3`，基于 master，与 PR #18 互不依赖。

## 结论

- **主干转绿了**：master 本地全量是 20 个失败；用了本分支以后，全量 2554 个通过、0 失败（Python 3.13，`API_KEY=test-key`）。20 个失败全都是测试之间互相污染、子进程测试会连外网造成的，产品代码本身没有回归。
- **Hermes 每天用到的记忆不会再被忘掉**：宿主回执会刷新 `last_used_at`；重要度 ≥0.8 的记忆不再因为衰减被自动归档。
- **首帧注入不再整帧丢失**：reranker 受注入帧的时间预算约束；整帧超时时，至少把常驻画像交出去；jieba 词典改成预热时加载。
- **X-04 只修了一半**：终端的编辑接口 `PUT /terminal/memory/{id}` 写入注入文本，改前返回 200，正文会重新嵌入、下一轮被注入。本轮已补上闸门。

## 本轮已修复（都配了测试）

1. **测试隔离（上一轮 P0-1）**：改 `tests/conftest.py`、`test_fence.py`、`test_xuanjing_topology_smoke.py`、`test_host_matrix.py`。
   - 每个测试结束后清空共享 SQLite 的全部业务表（含 FTS）和 Chroma 集合。
   - 查到两处真正的泄漏：一是 `test_fence` 用函数级 monkeypatch 改了 `param_env` 已经 patch 过的 `hybrid.get_vector_store`，两条撤销栈交错，把 DummyVS lambda 永久留在了 hybrid 上，下游 `test_genglou_retrieval` 的向量通道因此全空；二是 `test_xuanjing_topology_smoke` 直接给 `app.dependency_overrides` 赋值、事后不清，后面的 API 测试都顶着 user-1/t-1 的身份跑，`test_zuozheng_api_smoke` 因此 404。
   - 新增绊线：hybrid 的 `get_vector_store`、`embed`、`rerank` 被改脏时直接点名是哪个前置测试。
   - `test_host_matrix` 的子进程关掉了 reranker。原因是子进程不吃 conftest 的 `_disable_reranker`，在无网或无密钥时首帧要 4.6 秒，其中 rerank 占 3.4 秒。
2. **遗忘（上一轮 P0-4）**：改 `lantai/memory/forgetting.py`、`lantai/observability/retrieval_log.py`、`settings.py`。
   - 衰减归档按 `DECAY_ARCHIVE_EXEMPT_IMPORTANCE=0.8` 豁免。泳道豁免 `DECAY_ARCHIVE_EXEMPT_LANES` 默认关闭，因为遗忘质量评测的 stale 组明确要求“重要度 0.1、200 天没用过的偏好”被归档；我一开始做了泳道豁免，结果评测闸门失败，已经改回来。
   - 回执只刷新 `last_used_at`，**故意不加 use_count**：考功规则是 use≥3 且 helpful=0 就把重要度降到 0.1，三省也会因此告警，按注入次数加 use_count 会把天天在用的记忆反过来降级。
   - 回执是幂等的（同一事件重复回执不重复刷新），而且看归属（不会刷新别人的记忆）。
3. **注入帧时间预算（上一轮 P1-1）**：改 `lantai/retrieval/reranker.py`、`scripts/shell_hook.py`。
   - 新增 `rerank_deadline`：单次请求超时取剩余预算，预算不够“等 1 秒再重试”时直接降级，按融合分排序。不设预算的调用方（MCP、REST）行为不变。
   - 整帧超时时返回常驻画像，不再返回 `{}`。
   - jieba 改到 `_prewarm` 里加载，实测词典加载 0.94 秒。
   - 实测：无网 VM 上，常驻进程预热后的首帧从 4.6 秒降到 1.84 秒，而且带出了证据。
4. **终端编辑注入闸（X-04 补全）**：改 `lantai/api/routes_terminal.py`，`PUT` 遇到注入文本返回 422，不落库、不重新嵌入。

新增测试文件 `tests/test_audit_r3_fixes.py`，共 23 个测试。前 20 个放到改动前的产品代码上跑，失败的就是修复点。

## 复现核对（上一轮“只看了代码”的几项）

- X-03 终端自合并：返回 400，记忆保持 active，**确认已修**。
- X-04 `persona_set` 注入：抛出 ValueError，**确认已修**；终端 PUT 编辑**原先没修**，本轮补上。
- N-03 考功不带 principal：直接跳过，**确认已修**。

## 新建议（按优先级）

**P0**
- **合并 PR #18 和本 PR，并在 CI 上确认转绿**（0.5 天）。现在 CI 闸门已经生效，主干红着就发不了版。

**P1**
- **注入日期按用户时区显示**（0.5 天，改 `lantai/core/time.py` 的 `render_event_time` 和 settings）。证据：北京时间 10 月 31 日 7:30 写入的记忆，注入时显示成 `2026-10-30`。原因是 valid_from 按 UTC 取日期，凌晨 0 点到 8 点写的记忆都会早一天。建议只换算 valid_from 这个分支（它是真实时刻）；event_time 是文本里写明的日期，不要换算。时区建议默认 Asia/Shanghai，并在依赖里加上 `tzdata`，因为 Windows 的 zoneinfo 没有它就找不到时区。uv.lock 里已经有 tzdata，但 pyproject 没有声明。
- **中文相对时间归一**（1.5 到 2 天，改 `lantai/core/time_precision.py`、`ingestion/dialogue.py`）。“昨天”“上周”“下个月”原样入库，`time_precision.py` 的注释里自己也写着“中文相对时间解析器另票”。三个月后再注入“昨天去医院复查了”会误导模型。建议入库时以写入时刻（用户时区）为锚，补上绝对日期并写入 event_time，再加一组评测题。
- **两套衰减合一**（1 天，改 `forgetting.py`、`memory/decay_class.py`）。`decay_class` 里 semantic 半衰期是 180 天，这个值只在检索加权里用；归档用的是泳道强度 S=base+20×imp，两边口径不一致。

**P2**
- **探颐纠正入口没有注入闸**（0.5 天，改 `lantai/cognition/tanyi.py` 第 160 到 182 行）。用户回复纠正时直接改写 content，建议和终端编辑同口径加闸。
- **reranker 分数和融合分共用 0.5 阈值**（上一轮已提，仍未改）。

## 没做、没验证的

- LongMemEval 和 selfproof 没有重跑，VM 上没有模型密钥。
- Python 3.11 全量测试启动了，但在我写完时还没跑完，没有结果。
- 终端编辑注入闸那 2 个测试是在最后一次全量之后加的，只和 `test_terminal_write_guards` 一起跑过，都通过了，没有再跑一遍全量。
- 时区修复只给了建议，没有落地代码。
