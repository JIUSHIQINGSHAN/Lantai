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

## 4. 复现

```bash
# 解释器按 docs/project/development-environment.md 的机器记录取，清空 PYTHONPATH
<python> -m pytest tests/test_opt_refactor_smoke.py tests/test_opt_candidate_flow_smoke.py -q
<python> -m pytest tests/ --basetemp <ASCII 临时目录> -q
<python> -m ruff check lantai tests scripts
<python> -m ruff format --check lantai tests scripts
```