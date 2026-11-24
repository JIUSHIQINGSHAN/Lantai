# 存量代码审查与优化记录（2026-11-24）

- 分支：`refactor/optimize-legacy-code`（基于 `master` @ `7cb6f551`）
- 范围：混合检索与排序、候选/提案裁决链、事务与会话生命周期
- 纪律：每条都先实测取证再动手；测试替身只用于外部网络面（LLM / embedding /
  向量库临时实例），产品代码的判断逻辑一律真跑

## 1. 实测到的三条硬数据

### 1.1 MMR 多样性选择是 O(n²·k) 次 jieba 分词

`lantai/retrieval/hybrid.py::mmr_select` 的冗余度是「候选 × 已选」的成对
Jaccard，原实现对**每一对**都调一次 `_token_set`（内含 jieba 分词）。

| 形态 | 80 候选选 20 条 |
|---|---|
| 修前（热身后） | **3840 ms**（另一次实测 5160 ms） |
| 按正文缓存 token 后 | **72 ms** |
| 选择结果 | 逐条一致（等价性有测试锁） |

`MMR_ENABLED` 出场默认 `False`，所以这条不影响默认部署；但把它打开的人会
直接撞上秒级检索，属「开关一开就不对」的形态。

### 1.2 意图分类不在预算链上，单这一步就能撞穿 hook 的 2 秒硬墙

`hybrid_search` 第一步是 `classify_intent()`，它同步调外部 LLM（`chat_json`），
走 tenacity 3 连击。`shell_hook.build_context` 只把 `budget_s` 传给 embed 与
rerank，**没有传给意图分类**。

实测（把 LLM 客户端替换成「每次尝试挂 0.6s 后失败」，模拟网络黑洞而非快速
RST）：`classify_intent` 单次 **6.47s**。而 `SHELL_HOOK_TIMEOUT` 默认 2s——
`_run_with_timeout` 到点就放弃，整段注入只剩画像兜底。

修法：`classify_intent(query, timeout=None)` 与 `hybrid_search` 的预算同源
（有 `budget_s` 时按剩余预算单次调用）。无预算路径保持**单参调用形状**——
`tests/conftest.py` 与多处测试把 `hybrid.classify_intent` 替身成单参 lambda，
无条件传 `timeout=None` 会让它们 TypeError 后被吞成 `DEFAULT_INTENT`，
分类永不生效且无人察觉。

### 1.3 向量通道的 ACL 过滤仍是读路径最大头，但**没有动**

生产同款 chromadb 1.5.9、5000 行、32 维：

| 口径 | 每查耗时 |
|---|---|
| 无 `where` | 4.1 ms |
| `where={"user_id": {"$in": [viewer, ""]}}` | **28.4 ms**（7×） |
| `where={"$and": [{$or: …}, {"lane": …}]}` | 36.5 ms |

`$in` 与 `$or` 等价（同 id 序列、同 distance），换写法省不掉——chromadb 对带
元数据过滤的查询仍走元数据全表扫描，HNSW 不参与预过滤。

「先取 top-N×margin 再在 lantai 侧后过滤」实测能到 **4.9 ms**，但召回取决于
本人向量在库中的占比：

| 本人向量占比 | top-20 后过滤 | top-50 | top-100 | top-200 |
|---|---|---|---|---|
| 98%（单人部署，对齐真实库 96.8% NULL 属主） | 199/200 | 199/200 | 199/200 | — |
| **2%（多用户共用库）** | **5/200** | 25/200 | 49/200 | 83/200 |

结论：后过滤在单人部署下几乎无损，在多用户库里是灾难性的漏召回。本仓当前
形态是单人部署（真实库 657 行里 636 行 NULL 属主），但 `principal`
收敛到 `"default"` 的设计明确保留了多用户语义。**「宁 miss 不脏写」的反面是
「宁慢不瞎」，漏召回比慢更糟**，而这条改动需要按部署形态分支或引入配置，
不是本轮无争议的优化。故本轮**不改**，把数据登记在这里，留给后续独立票据
（可选方向：按库容阈值自动切换、或引入 `user_id` 的独立集合分片）。

## 2. 已落地的改动

| 位置 | 问题 | 改法 |
|---|---|---|
| `retrieval/hybrid.py::mmr_select` | 成对重复分词（上表 1.1） | 按正文缓存 token 集 |
| `retrieval/hybrid.py::_hybrid_search_impl` | 意图分类不接预算（1.2） | 传剩余预算；无预算保持单参形状 |
| `retrieval/intent.py::classify_intent` | 同上 | 新增 `timeout` 形参，有预算走单次调用 |
| `storage/db.py::init_db` | FTS 初始化用的 `raw_connection()` 用完不 close，启动即漏一条 DBAPI 连接（同函数上方迁移块有正确的 `finally`） | 补 `try/finally: conn.close()` |
| `storage/db.py` 新增 `get_session_dep` | 裸 `Session` 作 `Depends` 时 FastAPI 不做 teardown，`routes_cognitive` 五个端点异常路径无确定性 rollback，连接等 GC | 生成器依赖，请求结束一定 close；`get_session()` 保留给 341 处 `with` |
| `services/candidate_service.py::review_candidate` | 调 `propose_from_candidate` 漏传 `principal`：proposer 的 EXISTING 取数与 `target_key` 寻址都不收窄，A 审批时 B 的正文进外部 LLM 提示词 | 传 `principal` |
| `services/candidate_service.py::run_candidate_ttl_once` | 全量物化 `pending_review` 再在 Python 里比时间 | `review_due_at` 判定下推 SQL（谓词与 CAS 写时重验逐字一致） |
| `evolution/promoter.py::apply_proposal` | deprecate 提案白跑一次 embed（该分支只归档、不写正文），且在 CAS 认领之前，拉长竞态窗 | 只在会写正文的类型上 embed；add 有反向保险 |
| `evolution/promoter.py::rollback` | 为取上一版把该记忆的**全部** checkpoint（整条记忆的 JSON 快照）读回 | `.limit(2)` |
| `services/knowledge_candidate_service.py::_evidence_rows` | 每条 evidence 再发一次 `select(MemoryItem)` + 可能一次 checkpoint 查询；scope 在循环内每行重算 | 批量 `IN` 取来源与候选出身；scope 提到循环外 |
| `services/consolidation_service.py` | `_consolidation_proposal_blocked` 在簇循环内每簇重跑一次全量提案查询 | 一轮建一次索引；新提案手动补登（否则后出现的同簇漏判 dupes） |
| `evolution/reflector.py::theme_triggered` | 全表读 MemoryItem 再在 Python 判窗口；`any()` 是 O(N×batch) | 窗口下推 SQL；成员判定改 set |
| `retrieval/evidence.py::build_evidence` | 整个函数 `except Exception: return []` 零日志，与「本来没有来源」同形 | 补 `logger.warning(..., exc_info=True)` |
| `storage/fts.py` 两处查询失败 | 同上 | 加 `exc_info=True` |
| `workers/ingest_worker.py` | 外部 LLM 提取（单发 60s）在写事务内逐 doc 调用，N 条 doc 把 SQLite 写锁持有 N×60s；期间检索埋点/遥测/LLM 事件全排队报 database is locked | 拆「无事务准备（闸+密钥+提取到内存）」与「短写事务（只 INSERT）」两阶段 |
| `workers/ingest_worker.py` | 摄取无进程内单飞：cron / `/ingest/run` / `/monitor/workers/ingest/run` 三面可并发跑同一批 source | 按 `evolve_worker._RUN_ONCE_LOCK` 范式加非阻塞锁 |
| `cognition/context.py::build` | `_task_relevance` 对**每条记忆**重切一遍 task（构建 O(库容) 次分词），`/cognitive/context[/prompt]` 每请求都走 | 任务词集提到循环外算一次，排序逐位不变 |
| `cognition/canshang.py` | `principal=None` 时 `_check_access` 直接放行、查询侧也不带属主条件——MCP 匿名扫描能读到全库记忆的正文片段 | 收敛 default 属主空间（对齐 famen/zuozheng/muci/diben 票 57 口径），查询与主键直读两条路同判 |
| `services/shiyi_service.py` | 引擎 `_check_access` 对 None 全放是**内部调用契约**（worker/CLI 不空转），但服务入口是 MCP/REST 面——两者相接等于匿名调用拿到全库模糊召回 | 不动承重墙：服务入口收敛 default（同 `hybrid_search._converge_principal` 范式） |
| `eval/forgetting_quality.py` | 评测种子后调 `apply_forgetting()`（`principal=None` = 全表），而默认 CLI 直连部署库——一次评测给真实记忆做衰减并自动归档，archived 无 undo | 作用域收窄到评测自己的 `eval_fq` 种子，逐行判定口径原样搬 |
| `api/routes_cognitive.py::/cognitive/observe` | 只 `db.add`+`commit`，不 `sync_fts` 也不 `index_memory_item`——写进来的观测对两条召回通道永久不可见 | FTS 同事务同步（FTS_OK=False 或真缺表时降级）、向量在 commit 后补 |
| `security/injection_guard.py` | 折叠结果与原串相同时，第 2 遍全篇正则与第 1 步是同一模式同一输入的重复 | `folded != content` 才扫第二遍 |
| `cognition/chenfeng.py::search_archived` | `limit`/`offset` 三层都没界，`limit=-1` 在 SQLite 是 `LIMIT -1` = 无上限，一次拉回全部归档全文 | 引擎拒绝（bool 显式排除）+ REST `Query` 声明 + MCP `_int_param` |
| `cli/mcp.py` 考功/治命 | 裸 `int(params.get("limit"))`：负值直落 `q.limit(-1)`，一次调用过全库并写回 tier | 改 `_int_param`（缺省 100 不变） |
| `services/auto_triage_service.py` | 批量采纳的 `actions` 无条数上限，一个请求可放大成上万次写 + 每条 approve 一次外部 LLM | service 层 200 条上限并拒绝（不静默截断），REST 译 422 |
| `parameters/reliability.py` | `/verification/stats` 逐行调 `reliability_penalty`，而它每次重查同一行（N+1） | 判定体搬进纯函数 `penalty_from_stat(stat)`，函数保留为薄壳 |
| `api/app.py::CORE_ROUTERS` | `routes_diben_router` 注册两遍（OpenAPI 刷 Duplicate Operation ID，依赖与中间件各挂两份） | 删重复项；回归锁从 OpenAPI 反查而非数列表长度 |

## 3. 看过但判定不用动的

- **WAL 未启用**（`journal_mode=delete`）：`tests/test_sqlite_deployment_contract.py`
  把「生产实测 delete + 文档声称 delete」做成双向合同，改它须配套备份方案与
  部署文档，不是本轮顺手能动的。
- **`get_vector_store()` 单例缓存**：已正确缓存，只在首并发时可能构造两次，
  风险低，不值得为它引锁。
- **FTS 每查询重算关键词**：`_bm25_terms` 是纯 CPU 且被 24 gram 截断，成本可忽略；
  为它引缓存反而会破坏 `bm25_terms_truncated` 的「纯函数恒同判」契约。
- **`migrations.py` 的 21 处宽 catch**：属「降级而非阻断启动 + 留痕 + `/health/deep`
  可见」的刻意设计，已在正确的形态上。
- **巩固聚类的全表读**：聚类本质需要全量正文做关键词共现，SQLite 无等价下推；
  且是夜间任务、有单飞锁。
- **reflect 逐条提案各开 session**：`_reject` 是 LLM 网络调用，把网络调用放在
  事务外是刻意设计；改批提交反而持写锁跨网络往返。
- **`health_scan` 的全表读**（`reflector.py`，每轮反思读两次：前/后快照）：确实
  是「只产出 ≤20 条候选却物化整张 active 表」，但它同时要产出五个信号的**全量
  计数**（`snapshot`），而那五个判据分别落在 `valid_to` / `use_count` /
  `helpful_count` / `importance` / `decay_class` 上，且 R1 的 superseded 判定
  要先建全量边映射。逐信号下推 SQL 是**五个独立查询 + 语义重写**，不是一行改动；
  且它跑在后台 cron、不进请求路径。按「够用先于精巧」本轮不动，留作独立票据。
- **staged 评测往部署库泄漏 9 条记忆**（`eval/staged.py`）：清场只删 `evs-`
  前缀，而 ③ 段晋升出的记性主键是 `mem_`。真解要 `apply_proposal` 回传新建
  memory_id，且 ④/⑤ 段的对账断言依赖现有 id 形态——改动面与风险都超出「顺手
  修」的范围，登记后单独立票。
- **观测同步落库 5.1ms/次**（`observability/retrieval_log.py`）：占 hook 预算
  约四分之一，属既有性能基线登记过的热点 H4。异步化要动遥测线程模型（崩溃
  窗口、pending→missed 语义），不是本轮能安全收口的改动。
- **MCP `handle_search` 的双 `_principal_from_params`**：只构造一个 dataclass，
  无 IO 无查询，复用省下的量可忽略，且会让「注入摘要用哪个身份」更难读。
- **`build_cognitive_summary` 的 `except: pass`**（`cli/mcp.py`）：改成
  `logger.warning` 是零成本，但它会改变 MCP 返回体（要不要带 error 标记涉及
  宿主契约），本轮不动，只登记。

## 4. 复现

```bash
# 解释器按 docs/project/development-environment.md 的机器记录取，清空 PYTHONPATH
<python> -m pytest tests/test_opt_*.py -q          # 本轮全部回归锁（15 个文件 68 例）
<python> -m pytest tests/ --basetemp <ASCII 临时目录> -q
<python> -m ruff check lantai tests scripts
<python> -m ruff format --check lantai tests scripts
```

## 5. 本轮新增的回归锁（`tests/test_opt_*.py`）

每一条都带**变异对照**（把修复前的写法放回去，实测该锁变红），不是「写了
断言就交差」：

| 锁 | 咬住什么 |
|---|---|
| `test_opt_refactor_smoke.py` | MMR 缓存等价性 + 只分词一次 + 耗时阈值；意图分类的预算传导与无预算单参形状；`init_db` 连接归还 |
| `test_opt_candidate_flow_smoke.py` | 审批不漏传 principal；deprecate 不白跑 embed（add 有反向保险） |
| `test_opt_evidence_batch_smoke.py` | 批量来源判定与逐条判定逐条一致；checkpoint 只发一次 IN |
| `test_opt_context_tokens_smoke.py` | task 只分词一次；切面顺序不变 |
| `test_opt_canshang_acl_smoke.py` | 匿名扫描读不到他人记忆；NULL 老行仍可见；显式点名同样受控 |
| `test_opt_shiyi_entry_acl_smoke.py` | 服务入口收敛；引擎直调仍全放（两头都锁） |
| `test_opt_ingest_worker_smoke.py` | 单飞（含异常放锁）；提取期间另一连接能立刻写（事务边界用行为取证） |
| `test_opt_observe_index_smoke.py` | 观测记忆 FTS 可召回、向量确被调用、失败不回滚、缺表不 500 |
| `test_opt_forgetting_scope_smoke.py` | 评测不碰真实记忆；陈旧种子仍被判归档；种子不残留 |
| `test_opt_injection_scan_smoke.py` | 折叠相同时只扫一次；全角/NFD/控制字符变体仍命中 |
| `test_opt_chenfeng_limit_smoke.py` | limit/offset 三层边界 + bool/字符串混入 |
| `test_opt_mcp_batch_limit_smoke.py` | 考功/治命 limit 先验后用；合法值真的传下去 |
| `test_opt_triage_batch_cap_smoke.py` | 批量采纳上限；恰好到上限照常处理；REST 译 422 |
| `test_opt_verification_penalty_smoke.py` | 两种算法等价；行数翻倍 session 次数不变 |
| `test_opt_router_assembly_smoke.py` | 无重复注册（从 OpenAPI 反查） |