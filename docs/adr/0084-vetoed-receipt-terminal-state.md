# ADR-0084: 否决轮的检索痕迹收口——回执延后与 `vetoed` 终态

**日期**: 2026-11-07
**状态**: Accepted
**决策者**: Claude（票据 `.scratch/v0.67-next-window-2026-11/issues/01-veto-round-retrieval-trace.md`，P0）
**来源**: 周报 2026-11-03 P0-1 第 4 点 + 巡检 2026-11-06 22:00「沿用之前的未结项」；承接 ADR-0049 回执链与 PR #26（否决轮不写记忆，只收口了对话写通道与底本五段块）。

---

## 1. 背景与痛点

Hermes v0.22.6 起插件可在 `pre_llm_call` 返回 `{"action": "block"}` 否决一轮（DLP 拦密码、密钥）。这一轮从未发给模型，但兰台在三个位置留下了假痕迹：

1. **检索日志留原话**：`log_retrieval` 把用户原话存进 `RetrievalEvent.query_text`（+ `query_norm_hash`）。DLP 拦它是为了不让它到模型，兰台却把它永久落库——归一化哈希对低熵原文（密码/密钥）就是可验证口令，留着等于没抹。
2. **注入回执照发**：插件在 `pre_llm_call` 检索成功后立即 `_call_backfill`——此刻 Hermes 尚未裁决，被否决一轮的回执已把 `used_ids` 回填为 `acked`。
3. **假注入计数**：回执链上 `_touch_keepalive` 刷过 `injection_count`/`last_used_at`——这一轮从未被模型消费，考功/三省/遗忘却多了一次真实输入。

## 2. 决策

### 2.1 回执延后到 `on_session_end`，不做「立即回执 + 反冲」

候选方案对比：

- **立即回执 + 反冲**（否决时把 `injection_count` 减回去）：否决发生在回执之后，任何「先发后撤」都要面对并发反冲——同一窗口内其他合法回执可能正并发累加同一批记忆的计数，减法会误伤。
- **回执延后**（采纳）：`pre_llm_call` 把 `event_id`/`used_ids` 挂到会话缓冲条目上；`on_session_end` 走到 `_flush_session` 说明这一轮**未被否决**，才逐条发回执；被否决的条目改发 `retrieval_veto` 帧。假计数**从根上不产生**。

崩溃窗口的取舍：进程崩在 turn 中途 → 回执保持 `pending` → `mark_missed_receipts` 如实置 `missed`。符合「宁 miss 不脏写」：少一次弱标注可以接受，脏写假注入计数不可接受。

**边界（如实登记）**：无缓冲条目（无 `session_id`）的轮次保持旧行为**立即回执**——`_on_session_end` 无 session 早退，那些轮次的否决链本来就不可达，延后无处安放。旧版插件（升级窗内 in-flight）对已否决轮已发出的回执：`mark_vetoed` 清 `used_ids`/置 `vetoed` 但**不反冲**保活计数——同仓库同机部署下窗口只有升级瞬间的一轮，接受。

### 2.2 服务端 `mark_vetoed(event_id)`：抹原文 + `vetoed` 终态

- 抹 `query_text` 与 `query_norm_hash`（低熵秘密的哈希可枚举验证，必须同抹）；
- 清 `used_ids`；置 `receipt_status="vetoed"`（**新终态**）+ `receipt_at`；
- 行本身保留（`vetoed` 单列计数可观测——守门命中了多少轮），`result_ids`/`latency_ms` 等无原文元数据不动；
- **不做** `injection_count` 反冲（延后回执下否决轮从未回执；对已回执行反冲并发不安全）；
- 幂等：已 `vetoed` 或缺失 id 返回 False（无新状态转移），重复帧无副作用。

### 2.3 `vetoed` 是终态：回执链语义扩展

ADR-0049 的状态机从 `pending → {acked, missed}` 扩展为 `pending → {acked, missed, vetoed}`：

- `mark_missed_receipts` 只认 `pending`——`vetoed` 不被超龄扫描改写（结构性保证，非巧合）；
- `backfill_used_ids` 拒改 `vetoed` 行——迟到的回执不得把它拖回 `acked`、不得回写保活计数；
- `receipt_traceability_report` 补 `vetoed` 单列计数，不入 `acked`/`pending` 任何分子分母。

### 2.4 协议动作 `retrieval_veto`

宿主协议新增 `{"type": "retrieval_veto", "event_id": ...}`（`host_protocol.ACTION_VETO` + `shell_hook._handle_veto`），响应 `{"ok": true, "event_id": ..., "vetoed": bool}`——`vetoed=False` 表示幂等重放或事件不存在，如实上报不编造成功。当前唯一调用方是 `lantai-hook` 插件的否决分支；REST/MCP 侧不设 veto 入口（插件走 serve 通道已够达，不为用不到的面扩 API）。

### 2.5 零命中轮的否决链可达性（v0.68 票 01 增补，2026-11-07）

初版收口只覆盖「检索有命中且 evidence 非空」的轮次，留了一个覆盖洞：**零命中轮 / 空 evidence 轮**的检索事件同样带原话落库，但 (a) `build_context` 零命中分支经 `_persona_only()` 返回，`event_id` 被丢；(b) 插件挂载条件 `if event_id and used_ids` 把挂载与 `used_ids` 耦合——两端合起来，DLP 否决零命中轮时插件构造不出 `retrieval_veto` 帧，`mark_vetoed` 永远够不着，原话（含 `query_norm_hash`）永久留库，正是本 ADR §1 要消灭的状态。

增补决策（不改变 §2.1–2.4 的任何语义）：

- **响应契约对齐**：`query` 零命中分支把已落库事件的 `event_id` 带出（REST `routes_search` 与 MCP `handle_search` 的同名零命中分支本就如此，三条入口口径归一）；`request_id` 该分支本就没生成，如实不带。
- **挂载与 `used_ids` 解耦**：插件挂载只看 `event_id` 非空（`used_ids` 有则一并挂）；**回执条件不变**（`event_id and used_ids`）——回执语义是「用了哪些」，零 `used_ids` 无可回执；否决分支不变（条目有 `event_id` 即发 veto）。
- **`mark_vetoed` 零改动**：对零命中事件（`result_ids`/`used_ids` 本为空）同样正确——抹原文痕迹、置 `vetoed` 终态即可，无反冲问题（延后回执下该轮从未回执）。
- 边界（v0.68 票 11 增补校准，按三条通道实际行为陈述——本句初版把 shell_hook 的行为误写成普适边界）：查询过短（≤ `SHELL_HOOK_MIN_CHARS`）与 gate 判 `needs_memory=False` 的轮次在 **shell_hook 注入面不落事件**（`build_context` 在任何 `_try_log` 之前返回，无 veto 可言）；**REST/MCP 检索面则保留落事件**（拦截也是一次 `zero_result` 观测，供 digest/recall 遥测），但**写入时即空 `query_text`/`query_norm_hash`**（`log_retrieval(persist_query=False)`；`is_system_noise`/`estimated_tokens` 等标记以原查询在抹除前照算）——拦截轮原话在三通道一律不留。

### 2.6 missed/pending 同样抹原文（v0.68 票 10 增补，2026-11-07）

§2.1 接受崩溃窗 `pending→missed` 时只谈「少一次弱标注」的取舍，**从未谈原文留存**——这是一个真空带：missed 行的 `query_text`/`query_norm_hash` 与 vetoed 行形态完全相同（`query_norm_hash` 带索引，对低熵原文就是可验证口令，本 ADR §2.2 自己论证过），唯一区别是「宿主是否用过这轮」未得到确认而非已确认否定。原话不该因为回执缺失这个**事实**而无代价永留库。

增补决策（不改变 §2.1–2.5 的任何语义）：

- **missed 即抹文**：`mark_missed_receipts` 在 pending→missed 转移的同一事务里置 `query_text=""`、`query_norm_hash=""`——与 `mark_vetoed` 同源论证、同等待遇。**不清 `used_ids`**（弱标注源：missed→acked 回执仍合法，backfill 只拒 vetoed）、不回冲保活链（missed 行本就没刷过 `injection_count`，无假计数可回冲）。
- **扫描必须进调度器**：抹文逻辑挂 `receipt_miss` interval job（`RECEIPT_MISS_CRON_MINUTES`，默认 10 分钟，取值 5–60）——没有这一半，转移逻辑永远不会在生产触发，未回执事件永停 pending（v0.68 票 10 立案时的实况）。
- **存量一次性 scrub**：转移逻辑够不着已落库的存量 pending/missed 行；`scripts/scrub_retrieval_query_text.py`（缺省 dry-run、`--apply` 写库、幂等）按 `receipt_status in (pending, missed)` 一次性抹文，acked/vetoed 零接触。
- **边界不变**：acked 行原话保留是弱标注源的设计前提（本票不扩展全表 TTL）；§2.4「REST/MCP 侧不设 veto 入口」不变——抹文不改变任何回执语义，只消灭原文留存。
- **配套如实代价**：`build_query_set` 样本池排除空 query（抹文后空行不掺 `sample_count` 口径）；recall 报告对空 query 显示「(已抹除)」占位——抹文是可观测的，不是隐身的。
- **超时轮孤儿事件就地收尾（`mark_abandoned`，随行增补）**：`query` 通道撞硬墙超时时，主线程先把画像兜底帧回给宿主——**不带 event_id**；daemon worker 不可取消，跑完仍会 `log_retrieval` 落一条带原话的 pending 事件，宿主手里无 id、回执与 `retrieval_veto` 都发不出，原话只能滞留到上条 300s 扫描兜底。`lantai/observability/retrieval_log.py::mark_abandoned`（shell_hook 超时回调 `_query_abandoned` 消费）就地完成同一转移：pending→missed 并抹 `query_text`/`query_norm_hash`/`used_ids`，行保留可观测——与 300s 扫描殊途同归，只是不必等；只动 pending（已 vetoed/acked/missed 不改写），行为锁 `tests/test_hook_timeout_orphan_event.py`。

### 2.7 检索事件 TTL 与证据留存的边界（v0.68 票 29 增补，2026-11-08）

§2.5/§2.6 的抹文都是**对留存行的内容脱敏**（行保留）；票 29 补上另一半——**对超龄行的物理删除**（容量）：`retrieval_event` / `llm_call_event` 是全库最高频写入表，此前零行级清理无界增长（三条观测链只有 `operation_logs` 有 `MONITOR_RETENTION_DAYS` prune）。两者叠加后老行整体消失，互不冲突。

增补决策（不改变 §2.1–2.6 的任何语义）：

- **TTL 与观察窗绑死**：清扫运行时钳制 `max(配置天数, RECALL_MONITOR_WINDOW_DAYS)`（`RETRIEVAL_EVENT_RETENTION_DAYS`，默认 30 天；`lantai/observability/retention.py`）——TTL 短于观察窗会让 recall_report 的窗口计数（ADR-0027）静默失真，宁可钳制 + warning 留痕。回执对账窗（`RECEIPT_MISS_TIMEOUT_SECONDS=300s`）远小于天粒度 TTL，结构性满足；missed/pending 的抹文（§2.6）在行存续期内先行发生，TTL 删除只发生在其后再过 `RETRIEVAL_EVENT_RETENTION_DAYS` 天。
- **证据留存 = TTL 内**：§2.2/§2.6 的「原话不再留库」与守门计数（vetoed 单列可观测）的**证据留存语义，以保留期 TTL 为边界**——vetoed/missed 行与其它行一样按 `RETRIEVAL_EVENT_RETENTION_DAYS` 过期物理删除，本 ADR 不为任何回执状态保留「永留」承诺（守门命中轮数的历史聚合由司天面 retention 块的时点可见性承担，不承诺跨 TTL 回溯）。
- **挂载与对账**：清扫挂 scheduler `observability_purge`（每小时，镜像 kanhe_purge）；发布门禁 `retention_symmetry` 项以 `OBSERVABILITY_RETENTION_TABLES` 清单为真源逐表对账「旋钮 ↔ 清扫函数 ↔ 生产挂载」——后续新增观测追加表必须进清单，否则拒发。
- **llm_call_event 同律**：外呼结局标量（零正文）保留 `LLM_CALL_EVENT_RETENTION_DAYS`（默认 30 天），司天 24h 降级率窗被天粒度 TTL 结构性覆盖。

## 3. 命名

不引入新中文正名：`vetoed`/否决沿用 Hermes 已有词汇（PR #26 起），`retrieval_veto` 为协议动作英文名。ADR-0013 的登记义务只对**新的正式中文名**生效，本决策无新增。

## 4. 后果

- DLP 拦下的原话不再留在检索日志（可由 `tests/test_retrieval_veto.py` 钉住）；
- 被否决轮的假注入计数从根上消失（`tests/test_plugin_receipt_defer.py`）;
- 回执链多一个终态，报表多一列（消费方需感知：`vetoed` 不算回执失败）；
- 崩溃窗口回执 `pending→missed` 略增：以 missed 换脏写，符合项目纪律。

## 5. 关联

- [ADR-0049](0049-receipt-chain.md)（回执链——本 ADR 扩展其状态机）
- [ADR-0049 终态语义在本仓库的现行描述](../host-hook-protocol.md) §2.3/§2.5
- PR #26（否决轮不写记忆——对话写通道侧，本 ADR 是检索侧补全）
- 票据：`.scratch/v0.67-next-window-2026-11/issues/01-veto-round-retrieval-trace.md`（初版收口）、`.scratch/v0.68-next-window-2026-11/issues/01-veto-coverage-hole-zero-hit.md`（§2.5 零命中覆盖洞）
