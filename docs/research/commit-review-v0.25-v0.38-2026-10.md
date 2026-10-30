# 兰台 Lantai v0.25→v0.38 提交深度评审：问题清单

> 范围：`98a2d6bf`（v0.25.0）至 `206ece5f`（v0.38.0），共 36 个提交。只读评审，未改代码。

## 一、主干红灯（P0）

1. **GitHub Actions 门禁失守**：
   - GitHub Actions「Tests」最后一次通过是 v0.27.0（`51af20f6`）；v0.28.0（`8c9a8a48`）至 v0.38.0（`206ece5f`）连续 11 次发版全部 failure，而「Build and Push Docker Image」每次照常 success。
   - 违反 `docs/release-process.md` 上传门禁 1「全量测试 0 failed」。
2. **本地全量单测红灯统计**：
   - 本地全量 pytest（HEAD=`206ece5f`，Python 3.13，未配 API_KEY）：2258 passed / 33 failed / 2 errors / 5 skipped。
   - 失败归因：
     - **环境性 7 个**：`test_host_matrix` ×6、`test_install_host_hooks` ×1（缺 API_KEY）；
     - **`f74343e0` 引入 20 个**：`test_mcp_principal_wiring` ×3、`test_proposal_apply_ownership` ×4、`test_staged_eval` ×4、`test_promoter_rollback_vector` ×2、`test_reconcile_staged_labels` ×2、`test_e2_temporal`、`test_inject_frame_time`、`test_probing_resolution_smoke`、`test_staged_anchor_miss`、`test_zero_recall_remediation`；
     - **v0.32 同袍引入 3 个**：`test_genglou_migration` ×2 ERROR（`NOT NULL constraint failed: memoryitem.shared_scope`）；`test_keyword_fallback_owner_recall`；
     - **测试执行顺序状态泄漏 5 个**：`test_shell_hook` ×3、`test_retrieval_log::test_shell_hook_empty_query_no_log`、`test_mokan_items_budget_smoke`（测试间状态泄漏）。
3. **D-01 [P0] 测试桩反向污染生产环境**：
   - `scripts/shell_hook.py:243-246` 在生产代码里按请求改写 `lantai.retrieval.hybrid` 的模块全局（`get_vector_store` / `embed`），导致测试替身泄漏到后续用例（`lantai/retrieval/hybrid.py:925` 报 `'_FakeStore' object has no attribute 'add'`）；serve 多线程下同样是隐患。
   - `test_shell_hook.py` 仍按旧纯向量路径打桩，这 4 行是打补丁。
   - **修法**：删这 4 行，`test_shell_hook.py` 改为对 `hybrid_search` 打桩。
4. **D-06 [P0/P1] 时态评测精度大幅下滑**：
   - 同一提交 `f74343e0` 顺带改了 `lantai/retrieval/temporal.py::_ensure_utc` 及 `hybrid_search` / `wanbi_retriever` 的 `as_of` 透传。
   - E2 时态评测 `tests/test_e2_temporal.py::test_dual_view_accuracy_at_least_90` 从 37/37 掉到 31/37（6 题 got: `[]`，如 `e2-asof-travel-point`、`e2-asof-travel-next`、`e2-i4-editor-0/1`、`e2-i4-phone-0`）。
5. **D-13 [P2，但阻塞 CI] 数据库列约束漂移**：
   - `MemoryItem.shared_scope` 模型上 `NOT NULL` 且无 server default，迁移 `_migrate_v35` 加列带 `DEFAULT 'private'`，新库/迁移库约束漂移；
   - `_migrate_v19` 被事后改写。
   - 文件：`lantai/storage/migrations.py`。

## 二、自动改写事实风险（P0，R-01 / C-16）

6. **危险的子串匹配改写真实记忆**：
   - `probe_resolve`（默认 full profile 暴露）→ `lantai/services/probing_service.py::resolve_probe_response` 对否定做子串匹配：
     - 「没有问题，是这样」「还是你帮我决定吧」「I know, that's right」（"no" 命中 "know"）会被判为否定，正确记忆被直接归档、置信度打到 0.1；
     - 「是的，但其实我还没决定」被判为肯定并原地改写正文；原地改写不更新 `valid_from`，污染 as-of 切片。
   - **建议**：新增 `PROBE_RESOLVE_MODE=shadow|review|auto`，默认 `review`（只进待审队列）；去掉单字/子串否定规则，「肯定+转折」判不确定；改写走新建行 + 旧行 `valid_to` 封口 + `MemoryEdge(supersedes)`；建立 ≥50 条中英对抗样例作为 CI 门禁。

## 三、Hermes 改造未按设计生效（P1）

7. **D-02 短消息常驻画像丢失**：
   - `hermes-plugin/lantai-hook/__init__.py:459` 的 15 字预过滤直接返回，不调用 `build_context`，画像随之丢失。
8. **D-03 阈值量纲混乱**：
   - `SHELL_HOOK_SCORE_THRESHOLD=0.5` 同时套在 reranker 分和 RRF 融合分两种量纲上（RRF 下排第 1 天然约 0.59+）。
   - 文件：`lantai/retrieval/hybrid.py`、`lantai/core/settings.py`。
9. **D-04 超时时间不合理导致静默丢失**：
   - hook 路径变成 embedding + reranker 两次网络调用（`RERANKER_TIMEOUT` 默认 30s），仍包在 `SHELL_HOOK_TIMEOUT=2s` 里，超时整段（含画像）丢弃；超时线程是 daemon 不会取消。
   - 文件：`scripts/shell_hook.py:488` `_run_with_timeout`、`:542/:551`；`lantai/core/settings.py:195, 264`。
10. **D-05 画像注入无边界且重复**：
    - 画像拼在 `fence_declaration()` 之前、未经 `wrap_as_data`，无字数上限，每轮重复注入（`scripts/shell_hook.py:378-387`）。
11. **C-03 死路径残留**：
    - `hermes-plugin/lantai-hook/__init__.py:62` 仍残留 `Path("D:/Projects/记忆")` 兜底。
12. **C-07/C-08 评测基线未更新**：
    - LongMemEval / selfproof 改造后一次没重跑；偏好题两道三配置仍 0 分（`docs/benchmarks/longmemeval-2026-10/summary-rev1.json`）。

## 四、重复生长与未接线代码

13. **D-07/C-09 代号混乱与历史失序**：
    - 代号复用：探颐 4 次（v0.20/0.30/0.34/0.38）、格物 2 次（v0.31/0.35）、悬镜 2 次（v0.21/0.33）、贯珠 2 次（v0.18/0.27）；
    - 三省与「反思」同出典；ADR-0013 缺 v0.30/v0.31，v0.32/v0.33 排序错乱；v0.26 漏 tag 且 `43ce6388` 标题写成 v0.25.0。
14. **C-10/M1 探颐多头实现且未接线**：
    - `lantai/cognition/tanyi.py` 的 `TanyiProbingEngine`（:405）、`execute_tanyi_resolution`（:126）、`parse_user_resolution_intent`（:88）无生产调用方。建议以 `probing_service` 为唯一入口合并。
15. **D-09 伪开关**：
    - `ENABLE_PROACTIVE_PROBING` 只在未接线的 `tanyi.py:422` 读取，已接线的 `probing_service` 不看它，关开关关不掉真正在跑的探针（`lantai/core/settings.py:261`）。
16. **C-11/D-11/D-12/M2 尘封多套归档逻辑冲突**：
    - `lantai/cognition/chenfeng.py:118` `archive_cold_memories` 另写一套归档，与 `:45 evaluate_cold_candidates` 都没接线；
    - 阈值写死 0.05/30 天违反 ADR-0002；`forgetting.py:114`、`record_ops_service.archive_memory`、`chenfeng` 三条归档路径副作用不同。
17. **C-12/M3 格物薄包装冗余**：
    - 格物 v0.35 `lantai/cognition/causal.py:46` `CausalEngine` 是 v0.31 `provenance_service` 的未接线薄包装（`attribute_decision = get_provenance` 别名）。建议删除。
18. **C-13/D-10/M4 三省逻辑重叠与指标割裂**：
    - `SanxingEngine` 真正重叠的是 `lantai/evolution/reflector.py:192` `health_scan`；
    - R1 读 `item.structure["superseded_by"]` 恒为空（`sanxing.py:108`），R4 写死 `use_count>=5`（`sanxing.py:139`）与 reflector 口径不同。建议并回 `health_scan`。
19. **D-08/M5 认知上下文空头支票**：
    - `CognitiveContext` 新增 `causal_provenance` / `archived_history` / `reflection_audit` / `clarification_probes` 四个切面 `build()` 从不填充（`lantai/cognition/context.py:25-28, 46, 172`），CHANGELOG v0.38 描述与事实不符。

## 五、MCP 工具面

20. **C-15/R-02 工具膨胀与 Profile 缺乏测试**：
    - MCP 工具从 60 涨到 69 个且默认全部暴露；
    - `LANTAI_MCP_PROFILE=hermes`（5 个工具）无测试、无文档、直接读 `os.environ`。
    - **建议**：profile 分 core（默认）/hermes/admin/full 并加测试锁定。

## 六、其他 P2

21. **D-14 selfproof 文实不符**：
    - selfproof 文字称 BM25 recall@5=0.84，数据 `summary-rev1.json` 为 0.88；n=25 应标注样本量（`docs/benchmarks/selfproof-report-2026-10.md:7, 11, 37`）。
22. **D-15 插件元数据脱节**：
    - `hermes-plugin/lantai-hook/plugin.yaml` 仍叫「Remembrance」、版本停在 1.1.0。
23. **D-16 缺乏针对性测试**：
    - Hermes 三项核心改动（常驻画像、MCP profile、阈值过滤）零专门测试。
24. **代码提交过程粗放**：
    - 33/36 提交由 Claude Code 共同署名且正文几乎为空；`f74343e0` 一个提交塞了 Hermes 改造 + 稽古时态 + 两个迁移 + MCP 瘦身。

---

## 评测重跑计划

- **E-0**：先修 D-01/D-06 让 CI 回绿；
- **E-1**：LongMemEval 原 25 题三配置重跑出 `summary-rev2.json`；
- **E-2**：新增注入级配置 `hermes-hook`（判定最终注入文本是否含答案证据，含画像）；
- **E-3**：阈值扫描 `{0, 0.3, 0.5, 0.7}` × reranker 开/关；
- **E-4**：hook p50/p95/超时率统计；
- **E-5**：修补 selfproof 文字与数据对齐。

---

## 三道发版闸门

- **G1**：Docker 镜像构建 `needs: tests`，master 分支保护要求 Tests 通过；
- **G2**：`scripts/release_check.py` 校验版本/CHANGELOG/tag/标题一致、代号登记不重复、新公共函数有非测试调用方；
- **G3**：改动 `lantai/retrieval/`、`scripts/shell_hook.py`、`hermes-plugin/` 必须附 E2 + LongMemEval 摘要且不低于上一版。

---

## P0 行动清单（约 3.5–4 天）

1. **删 `scripts/shell_hook.py:243-246` 全局改写**，重写 `test_shell_hook.py` 打桩（D-01，0.5 天）；
2. **修 `temporal.py` / as-of 透传回归**，E2 回到 ≥0.9（D-06，0.5–1 天）；
3. **修 `shared_scope` 新老库漂移** 与 `test_keyword_fallback_owner_recall` 口径，CI 回绿（D-13，0.5 天）；
4. **镜像发布依赖 Tests**；启用 master 分支保护（G1，0.5 天）；
5. **探针答复默认 review**，修否定子串匹配，原地改写改 supersede（R-01，1.5 天）。
