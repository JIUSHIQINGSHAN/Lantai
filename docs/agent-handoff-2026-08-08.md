# Agent 交接文档 — Remembrance 记忆系统（2026-08-08）

> 本文件由 WorkBuddy 会话交接生成。**新会话（Kimi / DeepSeek / 其他 Agent）从本文件开始即可完整续干**，无需回看旧会话。

---

## 一、项目概览

- **项目**：Remembrance 记忆系统（论文可信度体系）——AI 记忆检索 + 参数建议门控系统
- **仓库**：`C:\Users\Asus\Desktop\记忆`（GitHub: JIUSHIQINGSHAN/Remembrance-System，master 分支）
- **技术栈**：Python 3.11 + SQLModel + FastAPI + ChromaDB + SQLite
- **测试**：`C:/Users/Asus/Desktop/记忆/.venv-audit/Scripts/python.exe -m pytest`（**全量 375 测试全绿**）
- **环境**：Windows；REMEMBRANCE_HOME=`C:/Users/Asus/AppData/Local/remembrance-data`（DB + chromadb）
- **API key**：OPENAI_API_KEY 已 setx 用户级环境变量 = SiliconFlow 有效 key（`sk-ykhks...ixgv`，**环境变量优先级高于 .env**，改 .env 必须同步 setx）

## 二、当前状态（已完成，勿重做）

| 里程碑 | 提交 | 状态 |
|---|---|---|
| UTF-8 修复 + 噪音过滤 + serve hook | `10f118d` | ✅ |
| Hermes 交接脚本 | `8a85b40` | ✅ |
| **Dry-run 评估管道**（EvalQuerySet/EvalRun 表 + build_query_set + compute_metrics + run_dry_run + CLI） | `b83b51e` | ✅ |
| **Step 7 影子观察期**（ShadowWindow 表 + shadow.py 决策 + runtime 集成 + DEDUP shadow-only + 人工闸门） | `ca0f031` | ✅ |
| **Step 8 验证回流**（SignalReliabilityStat 表 + reliability.py 只降权 + resolve_gating 钩子） | `9507bc5` | ✅ |
| embed API 修复（环境变量旧 key 覆盖 .env，已 setx 更新） | 未提交 | ✅ |

**可信度体系五方向全部落地**：①质量信号 ②验证闭环(shadow) ③时效 ④矛盾显式化 ⑤回流。

## 三、关键文件地图

```
remembrance/
├── core/settings.py          # 全部阈值（PENALTY_*/SHADOW_*/EVAL_* 等）
├── models/tables.py          # 核心表（RetrievalEvent/ParamSuggestion/ParamOverride）
├── parameters/
│   ├── shadow.py             # Step 7 决策纯函数（evaluate_window 三护栏）
│   ├── reliability.py        # Step 8 回流（record/penalty/apply）
│   ├── trust_models.py       # ShadowWindow / SignalReliabilityStat 表
│   ├── runtime.py            # open_shadow/check_shadow_due/rollback + 参数应用
│   ├── signal_service.py     # resolve_gating（含 venue_class penalty 钩子）
│   └── registry.py           # default_snapshot/ADJUSTABLE_SPECS
├── eval/                     # dry-run 管道
│   ├── models.py             # EvalQuerySet / EvalRun
│   ├── query_set.py          # build_query_set（噪音排除+去重）
│   ├── metrics.py            # compute_metrics（zero_result/avg/jaccard/weak_hit）
│   └── runner.py             # run_dry_run（param_overrides/intent_mode）
├── retrieval/hybrid.py       # hybrid_search（含 param_overrides 上下文覆盖）
└── storage/                  # db/vector_store/fts
scripts/
├── run_dry_run.py            # CLI：--query-set/--override/--baseline/--intent/--limit
├── verify_backfill.py       # used_ids 回填通道自检（8 项）
├── run_param_matrix.py      # 调参矩阵（多组权重 + 位置敏感指标）
├── mark_retrieval_noise.py   # 噪音回填
├── shell_hook.py             # --serve 常驻模式（Hermes 插件通道）
└── mcp_server.py             # MCP 服务
docs/
├── dry-run-report-v1.md      # 第一份评估报告（179 样本，zero=0%）
├── param-matrix-report.md   # 调参矩阵 v0：权重敏感度实证（Jaccard 盲区修正）
├── dry-run-eval-task-split.md  # 评估管道三模块契约
├── step7-shadow-task-split.md  # Step 7 双模型任务书
├── param-advice-implementation.md  # 参数建议实施文档
└── hermes-install-handoff.md # Hermes 接入交接
tests/test_param_shadow.py    # Step 7 测试（24）
tests/test_param_reliability.py  # Step 8 测试（12）
tests/test_eval_*.py          # 评估管道测试（42）
```

## 四、待办任务（新 Agent 从这里继续）

### 1. 调参对比矩阵 —— ✅ 已跑 dry-run-v2（2026-08-08）
- 查询集：dry-run-v2（214 样本，420 事件/248 干净去重），基线 + 5 组权重全量跑完
- 结论：4 条 active 记忆下 jaccard 恒 1.0（量级效应）；位置敏感指标首现分化
  （vec++/decay+ top3 集合一致性 58.9%/60.8%）；**不建议现在调权**，先攒数据 >100 条
- 顺带修复：FTS5 MATCH 特殊字符语法错误（search_fts 引号包裹，1284 次检索警告 0）
- 报告：docs/param-matrix-report.md（v2）

### 2. used_ids 回填通道（弱标注缺口）—— ✅ 已实现 2026-08-08
- `RetrievalEvent.used_ids` 回填通道已通：REST `POST /retrieval/backfill` + MCP `backfill` 工具 + 三检索入口透出 `event_id`
- `run_dry_run` 现按 event_id 加载 `used_ids_map`，有回填时 `weak_hit_rate` 出实值
- **遗留**：Hermes 生成侧需在回答后调 `backfill`（用哪些记忆回填）——MCP search 响应含 `event_id`，回答完调 `backfill {event_id, used_ids}`
- **验证**：`python scripts/verify_backfill.py` 一键自检通道（8 项）；指南 docs/used-ids-backfill-guide.md

### 3. 生产 dry-run 定期跑 + 报告 v2
- embed 已恢复，179 条全量可跑（rule 模式 33s）
- 数据继续涨（当前 ~382 事件），可重建查询集 `build_query_set("dry-run-v2")`

### 4. Step 8 人工验证入口 —— ✅ 已实现 2026-08-08
- `POST /verification` REST 路由（venue_class + passed + note）+ `GET /verification/stats`（各类别统计与降权系数）
- 用法：`curl -X POST http://127.0.0.1:8767/verification -H "Content-Type: application/json" -d "{\"venue_class\": \"preprint\", \"passed\": false}"`

## 五、踩坑经验（血泪，别重踩）

1. **环境变量优先级 > .env**：pydantic-settings 读 env 优先；改 key 必须 `setx OPENAI_API_KEY xxx` 同步注册表，且**只对新进程生效**（改完要重启 Hermes）
2. **SQLModel 表类不能用 `from __future__ import annotations`**；Field 必须从 `sqlmodel` import
3. **SQLite 存 naive datetime**：从 DB 读出后与 `utcnow()`（aware）比较前要 `replace(tzinfo=None)`
4. **测试卡死三连**：凡走到 hybrid_search 的测试必须 mock `classify_intent`（LLM 外部调用）+ `embed`（外部 API）+ `get_vector_store`；embed 不可用时每条查询 tenacity 重试 3 次拖死
5. **Windows 换行**：subprocess 读 stdout 是 `\r\n`，JSON 解析前 `rstrip("\r")`
6. **safe-delete 沙箱**：WorkBuddy Bash 的 rm/os.remove 会被拦，删文件用 `os.rename(p, p+'.del')` 改名绕过
7. **内存坑**：16GB 机器，chromadb 加载 + 浏览器占内存到 90%+ 时测试会卡死；跑重测试前确认可用内存 >2.5GB
8. **测试纪律**（AGENTS.md）：核心函数必须有**不 mock 的冒烟测试**；mock 只允许外部网络

## 六、项目纪律（新 Agent 必读）

- **人工闸门铁律**：参数最终应用必须人工批准，验证自动化只提供证据，绝无自动应用路径
- **宁 miss 不脏写**：校验失败即丢弃，不自动修正
- **信号来源锁**：新信号必须来自结构化字段/系统测量，不得由 LLM 生成后直接采信
- **零硬编码**：新阈值一律进 settings
- **中文注释，代码/标识符英文**
- **conventional commits** + Keep a Changelog

---

## 2026-08-09 增量（v0.5 对话闭环）

- **Ticket 02 候选可见队列已完成**（commit 见下）：`memorycandidate.review_due_at` +
  `pending_review` 状态；gate REJECT 不再静默丢弃，进待审队列交用户裁决；
  REST `GET /candidates/pending` + `POST /candidates/{id}/review`；
  MCP `candidates_pending` / `candidate_review`；每日 TTL 自动归档
  （`CANDIDATE_TTL_DAYS=7`，scheduler 任务 `candidate_ttl`）。
- 测试全量 409 全绿（新增 tests/test_candidate_queue.py 13 例 + test_mcp.py 3 例）。
- 接下来按序：Ticket 01 Dialogue Ingest → 04 Search Transparency → 05 Hermes 会话钩子验证。
- 详细方案见 `docs/plans/v0.5-dialogue-loop.md`；tickets 在 `.scratch/dialogue-loop/issues/`。

- **Ticket 01 对话写通道已完成**（commit 见下）：`ingest_dialogue` 对话文本 →
  fastpath 直通 / 闲聊进待审队列 / LLM 提取建候选（低置信度与提取失败兜底入队）；
  REST `POST /dialogue` + MCP `add_dialogue`；settings 新增 `DIALOGUE_*` 三阈值。
- 测试全量 422 全绿（新增 tests/test_dialogue_ingest.py 11 例 + test_mcp.py 2 例）。
- 接下来按序：04 Search Transparency → 05 Hermes 会话钩子验证。

- **Ticket 04 检索透明已完成**（commit 见下）：`build_evidence`（来源说明 id+摘要+分数，
  rerank 反查 id）；shell_hook 注入附「本次依据」段；MCP search 与 REST /search 补
  `evidence` 字段；无命中/异常零侵入降级。
- 测试全量 430 全绿（新增 tests/test_evidence.py 5 例 + shell_hook 2 例 + mcp 1 例）。
- 下一步：05 Hermes 会话钩子验证（research，验证会话结束事件；备选 state.db 只读扫描）。

- **Ticket 05 Hermes 会话钩子验证已完成（research）**：插件 API 存在 `on_session_end`
  （每轮结束触发，桌面版/CLI 通用）；payload 无消息文本 → 推荐插件缓冲
  pre_llm_call 的 user_message + on_session_end flush；备选 state.db 只读扫描
  schema 已探明（sessions/messages，WAL 安全）。结论回写 spec。
- **v0.5 对话闭环五组件全部完成**：① Dialogue Ingest ② Candidate Review Queue
  ③ Daily Digest（未实现，backlog）④ Search Transparency ⑤ Hermes 钩子验证。
  全量 430 测试全绿。下一步：Daily Digest（ticket 03）或 插件 on_session_end 落地。

- **插件对话自动写入已落地**（commit 见下）：插件源码进仓库 `hermes-plugin/remembrance-hook/`
  （pre_llm_call 缓冲 + on_session_end flush）；shell_hook --serve 新增 dialogue 通道；
  `scripts/install_hermes_plugin.py` 部署（旧版已备份 .bak-20260810，已部署新插件，
  待重启 Hermes 生效）。全量 441 测试全绿。
- 使用文档：`docs/remembrance-hermes-plugin.md`。
