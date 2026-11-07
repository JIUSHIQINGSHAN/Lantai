# 兰台文档与代码一致性审计报告（并行流 C）

- **日期**: 2026-11-07
- **审计对象**: HEAD `1195d29c`（= master，PR #33 合入后），worktree `.wt-v068-docs`，分支 `chore/v068-docs-audit`
- **审计方法**: 4 路并行子审计（协议/插件文档、MCP/API 面文档、流程治理文档、ADR 治理与版本一致性）+ 主线 CONTEXT.md 词汇表逐条核对。每条发现均双侧取证（文档侧 文件:行号 + 原文引句；代码侧 文件:行号 + 原文引句），主审计员对全部 P0 与关键 P1 逐条亲自复核。
- **范围说明**: 只读审计，未改动任何被审文件。docs/ 下自 dated 的历史报告（`dry-run-*`、`param-*`、`step7-*`、`agent-handoff-*`、`aidumem-port-results.md`、`docs/research/`、`docs/benchmarks/`、`docs/memory-quality/`）按归档口径处理，仅对被现役文档引用的做核对。
- **复核（第二轮，同日）**: 票 01-09 修复后重审（HEAD `115d9834`）：**P0×8 / P1×11 全部消除，总体评级反转为 ✅ 通过**（见 §七）；复核另发现修复文本自身引入的问题 3 项（P2×1、P3×2）与一处存量引文疑点，见 §七「复核新发现」。

---

## 总体评级：❌ 不通过（文档可发布性）——失败面集中，可一轮收口

**核心契约文档的「承诺-实现」对账相当扎实**：ADR-0084 否决收口全链（`mark_vetoed` 终态、backfill 拒改 vetoed、missed 只扫 pending、报表单列、插件回执延后、retrieval_veto 动作）与代码逐条吻合；host-hook-protocol 六动作、宿主矩阵、超时/樊篱/安全语义与代码一致；release_check.py 的门禁语义、CI 对账、schema 回放、MCP fail-closed 默认档与文档一致；CONTEXT.md 词汇表引用的 42 个文件路径、6 个实体类、4 个路由全部存在；pyproject/uv.lock/README/app.py/mcp.py 七处版本 0.66.0 全一致。

**但面向使用者的表层文档存在成组失效**：约 50 条原始发现，去重后 **P0 × 8 组、P1 × 11、P2 × 20**。P0 的共性是「照文档做必然失败或得到与预期相反的结果」，根因高度集中——四波变更（包更名 remembrance→lantai、MCP server 从 `scripts/mcp_server.py` 迁移到 `lantai/cli/mcp.py` + 默认档 fail-closed 收窄、v0.65 D-04 画像兜底、v0.67 PR #33 否决收口）只更新了协议与代码，没有回写到 README / 矩阵 / 手册 / runbook 这一层。

---

## 一、P0 发现（8 组，均经主审计员复核）

### P0-1 插件文档与 plugin.yaml 谎报闲聊去向，与 ADR-0026「沙汰」直接矛盾
- 文档侧: `docs/lantai-hermes-plugin.md:15`「闲聊（过短/社交结束语）→ 候选进待审队列（不静默丢弃）」；`hermes-plugin/lantai-hook/plugin.yaml:3`「…闲聊入待审队列」（随插件分发的 manifest）
- 代码侧: `lantai/ingestion/dialogue.py:115-127`「# 2) 闲聊 → 直接 rejected（沙汰，ADR-0026），不进待审队列 … status="rejected"」；ADR-0026 同口径
- 影响: 文档验证段让部署者聊几句后查 `GET /candidates/pending`，闲聊永远不进队列——按文档会误判部署失败。
- 修复: 两处改「闲聊 → 直接 rejected（沙汰）；低置信度/提取失败兜底才入待审队列」。

### P0-2 发布文档的版本同步步骤指向不存在/不含版本的文件，门禁真正核对的三处未写
- 文档侧: `docs/release-process.md:34,45-46`「`api_server.py`:`FastAPI(..., version=...)`」「`scripts/mcp_server.py`:serverInfo 的 version」；`docs/development-workflow.md:104` 同病
- 代码侧: `scripts/release_check.py:38-56` `VERSION_REF_SPECS` 核对的是 **`lantai/api/app.py`**、**`lantai/cli/mcp.py`**、**`uv.lock` 根包版本**；根目录 `api_server.py` 是 4 行薄壳无任何版本字符串；`scripts/` 下无 `mcp_server.py`
- 影响: 照文档执行"更新版本号"找不到目标，且漏掉门禁真正核对的三处（其中 uv.lock 漂移正是票 05 续18 修过的真实假绿案例）。
- 修复: 两处文档改列 `lantai/api/app.py` / `lantai/cli/mcp.py` / `uv.lock`。

### P0-3 MCP server 入口文档整层指向已删除的 `scripts/mcp_server.py`；两处代码侧断线随行
- 文档侧: `docs/mcp-client-matrix.md:9,12,22,33,48`（含硬编码 `C:\Users\Asus\Desktop\记忆` 路径）；`docs/lantai-manual-call.md:32-39,79`（launcher 指引）；`docs/hermes-install-handoff.md:71,132`（交付物表称该文件"已就绪"）
- 代码侧: `scripts/mcp_server.py` 已于 `bea001c1` 删除；现行入口 `pyproject.toml` `lantai-mcp = "lantai.cli.mcp:main"`（`lantai/cli/mcp.py:3874`）。**代码侧断线**：`launcher_mcp.py:48` 与 `launch_mcp.bat:18` 仍 `from mcp_server import main`（照跑 ImportError）；`scripts/verify_lantai.py:62` 仍 `import scripts.mcp_server` →「MCP server 可导入」自检恒 FAIL，交接文档"8 项全 PASS"按文档不可达。
- 修复: 文档统一改 `lantai-mcp`（或 `python -m lantai.cli.mcp`）；`launcher_mcp.py`/`launch_mcp.bat`/`verify_lantai.py` 需开代码票。

### P0-4 README 的 as_of 时态查询写错位置：query 参数被静默丢弃
- 文档侧: `README.md:173`「`POST /search?as_of=...` 查任意历史时点的世界快照，`time_from/time_to` 圈时间窗」
- 代码侧: `lantai/api/routes_search.py:14-18` query 参数仅 `trace`/`explain`；`as_of`/`as_of_recorded`/`time_from`/`time_to` 全在 `SearchReq` 请求体（`lantai/models/schemas.py:74-76`）
- 影响: 调用方以为在查历史快照，实际 FastAPI 静默忽略 `?as_of=` 返回当前视图——这是最危险的沉默失真。
- 修复: 改「请求体传 `as_of` / `time_from` / `time_to`」。

### P0-5 MCP 工具面口径整层失效：矩阵钉在 40 工具、README 钉在三工具，默认档收窄零记录
- 文档侧: `docs/mcp-client-matrix.md:58`「工具清单(40)」、`:93`「`tools/list` 返回 40 个工具」，全文无 `LANTAI_MCP_PROFILE`；`README.md:192`「`search`/`add`/`feedback` 三工具」
- 代码侧: `lantai/cli/mcp.py:3712-3714` `HERMES_CORE_TOOLS` 默认档 6 工具（fail-closed）；`TOOLS`/`TOOL_HANDLERS` 注册表 96 项（AST 实测两侧零缺漏）；矩阵所列 40 个工具名全部存在于 96 注册表，但 `:15` 点名的 raw_add/rollback/conflicts/candidates 在默认档一律 `-32602`
- 修复: 两份文档统一改「默认核心 6 / `LANTAI_MCP_PROFILE=full` 全量 96」两档口径，矩阵验证清单按档改写。

### P0-6 手册（lantai-manual-call.md）示例照做即失败
- 文档侧: `:58`「起服务（默认 8000 端口）」、`:62` `curl http://127.0.0.1:8000/search`；`:44-46` add 示例无 title、正文仅 9 字；`:72` 速查表称 title 可选
- 代码侧: `lantai/core/settings.py:27` `PORT: int = 8767`；`lantai/models/schemas.py:26` `title: str = Field(min_length=1)`、`:58` `content must be at least 10 characters`
- 修复: 8000→8767；示例补 title、content ≥10 字；速查表标「title 必填非空、content ≥10 字」。

### P0-7 运维手册（ops-runbook.md）两条命令不可用
- 文档侧: `:116`「`python scripts/run_digest.py` 手动生成每日盘点」；`:105-107`「Docker 模式：`docker compose up -d`」
- 代码侧: `scripts/` 无 run_digest.py（真实入口 `POST /monitor/workers/digest/run` 或 `GET /digest/today`）；仓库无 docker-compose.yml/compose.yaml
- 修复: 改指 worker 运行端点；compose 行删除或指向 README 的 `docker run` 路径。

### P0-8 MCP `add` 的 media_url 是假承诺（广告了、没接线）
- 文档侧: `docs/mcp-client-matrix.md:62`「add 支持 media_url 图片感知，v0.10 目识」
- 代码侧: 广告 schema 有 `media_url`（`lantai/cli/mcp.py:2114-2117`），但处理器 `:359-363` 构造 `AddMemoryReq(title/content/lane)` 不读 media_url——传 media_url+空 content 报 -32602，传 media_url+有 content 被静默丢弃；目识链只在 REST `POST /add` 生效
- 修复: 二选一——代码侧一行透传（建议，随文档修复开代码票），或文档删句注明目识仅 REST 可用。

---

## 二、P1 发现（11 项）

| # | 文档 | 问题 | 证据（文档 → 代码） |
|---|------|------|---------------------|
| 1 | `docs/host-hook-protocol.md:65,70` | 空命中/超短/异常返回「无 context 键/`{}`」未同步 D-04 画像兜底 | `scripts/shell_hook.py:387-388,427-429,563-564` 三路走 `_persona_only()` 返回画像帧 |
| 2 | `docs/host-hook-protocol.md:124` | §3「超时只返回空帧」对 query 通道不成立 | `shell_hook.py:792-809` query 带 `_query_fallback`，超时降级返回画像帧 |
| 3 | `docs/lantai-hermes-plugin.md:22-24` | 仍写「注入成功后立即回执」，未同步 PR #33 回执延后 + retrieval_veto | `hermes-plugin/lantai-hook/__init__.py:544-560,498-514,310-340` |
| 4 | `docs/used-ids-backfill-guide.md:24-28,74` | 把 MCP backfill 当主力通道；backfill 不在默认档 6 工具，不设 full 即 -32602 | `lantai/cli/mcp.py:3712-3714` |
| 5 | `docs/used-ids-backfill-guide.md:62-63` | 验证代码 `from remembrance.*` import，包已更名 lantai，一跑即炸 | `lantai/storage/db.py`、`lantai/models/tables.py` |
| 6 | `docs/hermes-install-handoff.md:55,76` | 称迁移脚本写 `REMEMBRANCE_HOME`、「子进程能 import remembrance」——脚本现写 LANTAI_HOME、包已更名 | `scripts/migrate_home.py:98-115`；标题/正文通篇旧名（建议头部加"历史文档"横幅一并处理） |
| 7 | `docs/release-process.md:8,82` | 镜像名 `ghcr.io/…/remembrance` 与 CI 实际产物 `lantai` 不符（照做 pull 失败） | `.github/workflows/ci.yml:85-86` |
| 8 | `docs/release-process.md:26-35` | 门禁实际 11 项，文档清单缺 lint/CI 对账/wheel 构建/遗忘质量 4 项，且 pytest 命令口径与门禁不符（门禁带 `--cov`+绊线+真实通过例要求） | `scripts/release_check.py:897-912,1239-1349` |
| 9 | `docs/adr/0013-naming-system.md:143` | §7 版本代号表止于 v0.64.0 缮治，缺 v0.65.0 钩玄、v0.66.0 探赜，违反 release-process.md:31「CONTEXT.md 与 ADR-0013 映射表双向登记」 | `CONTEXT.md:105-106` 已登记、CHANGELOG 已发布 |
| 10 | `CONTRIBUTING.md:158` | 示例命令 `pytest tests/test_gate.py -q` 指向不存在的文件 | tests/ 下无 test_gate*；闸门测试在 test_features.py/test_prefilter.py |
| 11 | `docs/lantai-manual-call.md:19-22,71,77` + `docs/ops-runbook.md:94` | gate 规则三处过时：15 字硬卡已被 ADR-0028 放宽（技术词≥4 字/问句≥5 汉字放行，`prefilter.py:209-218`）、「帮我查」不在任何触发正则（`prefilter.py:34-53`）、漏 `force` 参数；ops-runbook `:48` 观察期门槛写「7 天中至少 3 天」实际 `REFLECT_OBSERVATION_WINDOW_DAYS=14/REQUIRED_RUNS=7`（`settings.py:478-479`）；`:78`「CANDIDATE_MIN_CONFIDENCE 当前 0.0」实际默认已 0.15（`settings.py:71`） | 见左 |

---

## 三、P2 发现（20 项）

| # | 位置 | 问题 |
|---|------|------|
| 1 | `README.md:301` | 「ADR 0001-0053」实为 84 份（建议写「持续增补」防再过期） |
| 2 | `hermes-plugin/lantai-hook/plugin.yaml:2-4` | 品牌/作者仍 Remembrance；版本 1.1.0 未随 PR #33 行为变更提升（建议 1.2.0） |
| 3 | `docs/adr/0042,0043,0046` | 三份 ADR 缺状态行（其余 81 份均有） |
| 4 | `CHANGELOG.md:12` | 「test_plugin_receipt_defer.py（10 例）」实为 9 例 |
| 5 | `CONTEXT.md:96`、`docs/adr/0084:5,67`、`CHANGELOG.md:21` | 引用 `.scratch/` 票据目录，但 `.gitignore:48` 忽略 `.scratch/`（早期票据已入库、新窗口未入库，口径混合）——克隆侧链接悬空 |
| 6 | `CONTEXT.md:20` | fastpath 条目设计引用 `parsing/fastpath.py` 缺 `lantai/` 前缀（与他条不一致） |
| 7 | `CONTEXT.md` | 命名登记缺口：格物在 `lantai/cognition/causal.py:2` 自称功能名但词汇表只有版本代号条目；`retrieval_veto`/`mark_vetoed`/`receipt_status="vetoed"`（ADR-0084 新协议状态）未入词汇表技术字段；`context.py`(CognitiveContextBuilder)/`evolution.py`/`lifecycle.py`/`blb.py` 无对应条目 |
| 8 | `docs/future-roadmap.md` | 严重滞留：引言说优先方向一【沉潜】、列表方向一却是探颐；四个方向（探颐/格物/同袍/悬镜）按 ADR-0037/0061/0058/0038 已全部落地；被 ADR-0058/0059 作历史出处引用，不宜删，建议加「已全部落地」归档横幅 |
| 9 | `docs/key-rotation-runbook.md:26,55` | 残留旧机器路径 `C:\Users\Asus\Desktop\记忆`（现 D:\Projects\记忆） |
| 10 | `docs/WHITEPAPER.md` | 自钉 v0.15.2 快照（42 工具/797 测试为当时数字，尚属自明）；但 `:355` 镜像名 remembrance 已错（实际 lantai）；建议加"历史快照"横幅 |
| 11 | `docs/mcp-client-matrix.md:14` | 数据目录回落顺序漏 `%LOCALAPPDATA%\remembrance-data` 探测（`settings.py:513-521`） |
| 12 | `docs/lantai-manual-call.md:4` | 工具前缀 `mcp__remembrance__*`，serverInfo 实为 lantai |
| 13 | `docs/host-hook-protocol.md:47` | query 回落条件多写「且带查询字段」——无查询字段也回落（`host_protocol.py:126-133`） |
| 14 | `docs/daily-digest.md:9-16` | 统计表缺第七项「反思提案」（`digest_worker.py:367`）；其余逐字一致 |
| 15 | `docs/development-workflow.md:78,95-96,52` | `.venv-verify`/「必须 PYTHONPATH=.」过时（pyproject 已配 `pythonpath=["."]`）；诊断报告归档路径 `docs/memory-quality/<slug>-diagnosis-*` 全仓零实例 |
| 16 | `docs/agents/triage-labels.md` | 五分诊标签在全部票据中零使用（存量票据只有 resolved）——启用或注明 |
| 17 | `docs/agents/domain.md:23` | 示例树用 `src/`，本仓库包根 `lantai/` |
| 18 | `.pre-commit-config.yaml:9` | ruff rev v0.6.0 vs pyproject dev 钉 `ruff>=0.16.0`，两套口径分叉 |
| 19 | `CONTRIBUTING.md:168`、`docs/glossary.md:72` | 测试数「700+」/「1200+」过时（实测约 2744+，341 个测试文件） |
| 20 | `DESIGN.md:71` | 「零外部庞大框架依赖」与悬镜实际加载 D3@7（CDN+本地兜底 `lantai/api/ui/index.html:691-692`）矛盾，建议收窄为「无构建步骤」 |

---

## 四、根因分析

1. **四波变更只回写了协议层，未回写使用面**：包更名（remembrance→lantai）、MCP server 迁移+默认档 fail-closed（0.66 续31）、画像兜底（0.65 D-04）、否决收口（PR #33）都在 host-hook-protocol/CHANGELOG/ADR 层同步得很好（本次核对零差异），但 README/mcp-client-matrix/lantai-manual-call/ops-runbook/used-ids-guide/lantai-hermes-plugin 这层几乎全部漏更。
2. **文档引用的入口文件被删后无对账机制**：`scripts/mcp_server.py` 删除（bea001c1）波及 5 份文档 + 2 处代码（launcher_mcp.py、launch_mcp.bat、verify_lantai.py）。
3. **版本同步步骤的描述与门禁实现脱钩**：门禁核对七处引用的清单演化过（uv.lock 是续18 新加），文档从未跟上。
4. **验收示例没有跟随行为变更**：闲聊沙汰（ADR-0026）改变候选去向，插件文档的验证段还在教旧口径。

## 五、修复建议（建议立票顺序）

1. **代码票 ×3**（文档修复带不动）：`launcher_mcp.py`/`launch_mcp.bat` 改 lantai-mcp 入口；`verify_lantai.py:62` 导入检查改 `lantai.cli.mcp`；MCP `handle_add` 透传 `media_url`（一行）。
2. **P0 文档一轮收口**：P0-1/2/4/5/6/7 均为小改，可并入本分支一次提交。
3. **P1 按文档分组**：host-hook-protocol 画像兜底两处 + 插件回执段；used-ids 指南两处；release-process 镜像名 + 门禁清单；ADR-0013 §7 补两行；CONTRIBUTING 换示例；gate 口径三处统一改写。
4. **P2 搭车**：建议随 v0.68 文档票一并清；`.scratch` 悬空引用需先决策「票据随发版入库 or 文档不引用未入库路径」。
5. **防复发**：考虑在 release 门禁或 CI 加一条轻量对账——文档中出现的 `scripts/*.py` 路径必须存在（本次 3 个 P0 均属此类，可结构性拦截）。

## 六、已核对且一致（覆盖面证明，摘要）

- **ADR-0084 ↔ 代码全链**：`mark_vetoed`（retrieval_log.py:260-294）抹 query_text/query_norm_hash/清 used_ids/置 vetoed、幂等、不反冲；backfill 拒改 vetoed（:228-229）；missed 只扫 pending（:314）；报表单列（:334-370）；`ACTION_VETO`（host_protocol.py:26）+ `_handle_veto` 响应逐字一致（shell_hook.py:645）；插件回执延后/否决剔轮/无 session 立即回执边界与 ADR §2.1 完全一致。
- **host-hook-protocol**：六动作名逐字、字段回退序、backfill 整帧覆盖语义、超时（SHELL_HOOK_TIMEOUT=2/DIALOGUE_TIMEOUT=30）、宿主矩阵 LANTAI_HOST、install_host_hooks/install_hermes_plugin 行为全部一致（除上列 P1-1/2、P2-13）。
- **release gate**：只读承诺、Git 四判据、老库回放 fixtures、MCP fail-closed 探针、CI 步骤对账、VERSION_REF_SPECS 运行时 7 值全对上。
- **CONTEXT.md**：42 个文件路径、MemoryItem.tier/MemoryCandidate/MemoryProposal/MemoryCheckpoint/MemoryNode/SkillCrystal、`/ui/vault`（routes_ui.py:859）、`GET /graph`（routes_graph.py:16）、`GET /recall/chain`（prefix /recall + /chain）、`/stats`（routes_health.py:167）、ADR-0013 §7 登记表存在——全部属实。
- **README 其余部分**：徽章 4 个 URL、快速开始全链路、Docker 约束、接口表抽查 16 端点、环境变量表 18 项逐字对照 settings.py、鉴权三层描述——除 P0-4/P0-5/P2-1 外一致。
- **版本三角**：0.66.0 七处一致；[Unreleased] 与 PR #33 吻合；回归锁 7 个测试文件存在。
- **daily-digest**：除缺一行统计外全对（cron 22:00 UTC≈上海 06:00、TTL 7 天、pending/review 端点）。
- **ADR 治理**：0001–0084 连续无重复，81/84 有状态行。

---

*审计执行：主审计员 1 名（主线词汇表 + 全部复核）+ 并行子审计 4 路（协议/插件 13 条、MCP/API 14 条、流程治理 13 条、ADR/版本 7 条），合计约 50 条原始发现，去重合并如上。全部发现为只读取证，未改动仓库文件。*

---

## 七、复核（2026-11-07 第二轮：票 01-09 修复后）

- **复核对象**: HEAD `115d9834`（diff 范围 `1195d29c..115d9834`，9 提交 20 文件：3 张代码票 01/02/03 + 6 张文档票 04-09 + CHANGELOG 补记）
- **方法**: P0×8 主审计员逐条双侧亲核；P1×11 子审计逐条取证、新发现主审计员亲核；全仓陈旧模式扫描；代码票回归实测。本轮仍为只读复核，未改动被审文件。

### 复核结论：总体评级反转为 ✅ 通过（文档可发布性，按本报告口径）

**P0 × 8 组全部消除**（双侧证据，逐组）：

1. **P0-1 闲聊去向**：`docs/lantai-hermes-plugin.md:15` 与 `hermes-plugin/lantai-hook/plugin.yaml` 描述均改「闲聊直接 rejected（沙汰，ADR-0026）」，与 `lantai/ingestion/dialogue.py:115-127`（`_is_chitchat` → `status="rejected"`）一致；插件文档验证段同步注明「纯闲聊被沙汰是预期行为而非部署失败」。
2. **P0-2 版本同步步骤**：`docs/development-workflow.md:104` 与 `docs/release-process.md` 改列 `lantai/api/app.py` / `lantai/cli/mcp.py` / `uv.lock` 根包版本，与 `scripts/release_check.py:36-56` `VERSION_REF_SPECS` 逐项对上（含 uv.lock 锚定注释）。
3. **P0-3 MCP 入口**：矩阵/手册/交接文档全部改 `lantai.cli.mcp`（console script `lantai-mcp`，`pyproject.toml:50`），旧 `scripts/mcp_server.py` 仅以「已删除」注记出现；代码侧三处（`launcher_mcp.py` / `launch_mcp.bat` / `scripts/verify_lantai.py`）已改现行入口，启动器仓库根与 venv 改脚本位置相对解析（`.venv` 优先、`.venv-audit` 兜底），旧机硬编码盘符清除；交接文档头部加「历史文档」横幅声明机器路径/品牌名按当时原貌保留。
4. **P0-4 as_of 时态查询**：README 改「`POST /search` **请求体**传 `as_of`/`as_of_recorded`/`time_from`/`time_to`」并保留「写在 URL 上会被静默忽略、返回当前视图」警示；与 `lantai/models/schemas.py:74-77`、`lantai/api/routes_search.py`（query 参数仅 `trace`/`explain`）一致。
5. **P0-5 工具面两档**：README 与矩阵统一「默认核心 6（search/add/feedback/offload_read/mem_recent/core_memory_get，fail-closed）/ `LANTAI_MCP_PROFILE=full` 全量 96」；实测 `HERMES_CORE_TOOLS`（`lantai/cli/mcp.py:3716-3718`）恰 6 项、`TOOLS`/`TOOL_HANDLERS` 注册表 AST 实测均 96 项、档位解析（不设/空/未知取值→核心集，未知额外告警）与文档一致；矩阵验证清单按档改写（raw_add 需 full 档、默认档 -32602 非部署故障）。
6. **P0-6 手册示例**：端口 8767（`lantai/core/settings.py:27`，`lantai/api/app.py:195` uvicorn 同源）与「settings.PORT」注记属实；`title` 必填非空（schemas `min_length=1`）、`content` ≥10 字（model_validator）、`media_url` 二选一、`force` 绕闸（`SearchReq.force` + `mcp.py:193`）全部与代码对上；add 示例已补 title、正文 ≥10 字。
7. **P0-7 runbook 命令**：每日盘点改 `POST /monitor/workers/digest/run`（`lantai/api/routes_monitor.py:78` `/monitor/workers/{worker_name}/run`）+ `GET /digest/today`（`lantai/api/routes_digest.py:18`）；`docker compose` 行改指 README「Docker 部署」的 `docker run` 单容器路径（README:97-102，Dockerfile `EXPOSE 8767`）。
8. **P0-8 media_url 接线**：`handle_add` 透传 `media_url`（`lantai/cli/mcp.py:362`），非法组合由 `AddMemoryReq`「media/content 二选一」校验拒绝（与 REST 同口径，不静默丢弃）；不 mock 冒烟 ×2 通过（caption 落 RawDocument 正文 + provenance 带 media_url；二选一拒绝 → -32602），视觉替身属 AGENTS.md 允许的外部 LLM mock。

**P1 × 11 全部消除**（子审计逐条双侧取证，关键项主审计员亲核）：host-hook-protocol 画像兜底（:65-72，`shell_hook.py` 三路 `_persona_only`、未配置常驻画像才退 `{}`）与 query 通道超时兜底（:127-130，`shell_hook.py:792-809` `_query_fallback`）；插件回执延后 + `retrieval_veto`（插件文档 ：22-30，与 ADR-0084 §2.1 逐句吻合，`mark_missed_receipts` 实证）；used-ids 指南两处（backfill 不在默认档需 full、REST 端点为主力，`POST /retrieval/backfill` 实证；import 改 `lantai.*`，全文件 remembrance 零残留）；交接文档 `LANTAI_HOME`（`scripts/migrate_home.py:98-117` 实证 setx/写入 .env）；release-process 镜像名（`ghcr.io/<owner>/lantai`，`ci.yml:85-86`）与门禁 11 项真清单（与 `release_check.py` main 检查序列逐项对应，pytest 参数口径逐字一致）；ADR-0013 §7 补登钩玄/探赜（与 CONTEXT.md:105-106、CHANGELOG 三方同代号）；CONTRIBUTING 示例换 `tests/test_features.py tests/test_prefilter.py`（均存在）；gate 口径三处统一（ADR-0028 放行通道、`force`、观察期 14 天窗口 ≥7 天 `settings.py:478-479`、`CANDIDATE_MIN_CONFIDENCE=0.15` `settings.py:71`、「帮我查」不在触发正则而「查…记忆」形状才算）。

**P2 × 20 维持未动**（预期内，票 10 needs-triage 待维护者裁决）：抽查 7 项（plugin.yaml 品牌与 1.1.0 版本、CONTEXT.md:20 fastpath 缺前缀、矩阵数据目录回落漏 `%LOCALAPPDATA%\remembrance-data`、README:300 ADR 计数 0001-0053、CONTRIBUTING/glossary 测试数 700+/1200+、ADR 0042/0043/0046 状态行、daily-digest 反思提案统计行）确认原状未清。

**代码票回归**：`tests/test_mcp.py` 全模块 **52 passed**（主仓 `.venv` 实测）；两个新增 media_url 冒烟为真不 mock——直调 `handle_add` + 真实 SQLite，符合 AGENTS.md 测试纪律。

### 复核新发现（修复文本自身引入，3 项，均经主审计员亲核）

| # | 级别 | 位置 | 问题 | 建议 |
|---|------|------|------|------|
| R-1 | **P2** | `docs/lantai-manual-call.md:26` | 新例子「⚠️ 孤立短名词（『大哥的电脑配置』，无技术词也无问句标记）→ 仍是 no_signal」与 gate 实际行为**相反**：`大哥` 命中自我指代正则（`lantai/gate/prefilter.py:71`），`配置` 命中技术词表（:76）且总长 7≥4——双通道放行，search 会真实检索。旧版只含糊说「大概率 no_signal」，新版把机制写死反制造直接矛盾 | 换真正不命中任何通道的例子（如「物业费在哪交」——单字「哪」不在 QUESTION_PATTERNS、无技术词、≤15 字） |
| R-2 | P3 | `docs/release-process.md:36` | 第 7 项标题「MCP 默认最小暴露」与门禁输出名「MCP 默认暴露」（`release_check.py:1306,1308`）差一词；第 3 项「目标版本格式」仅失败路径记账（:1268-1271，成功不输出），绿灯汇总实为 10/10，与 :27「下表与门禁输出逐项点名一一对应」有轻微表述出入 | 标题对齐门禁输出名；第 3 项注明「仅失败时出现在输出」 |
| R-3 | P3 | `docs/lantai-manual-call.md:22` | 「12 字内的短追问沿用上一轮判定」实为**单向**：仅上一轮 `needs_memory=True` 时短路沿用（`prefilter.py:193-195` `if cache["needs_memory"] and len(q) < 12`）；上一轮 no_signal 的短追问会继续走完整判定链并可能翻 True | 改「短追问沿用上一轮**命中**判定」 |

存量疑点随票 08 扩散一处：ADR-0013:144（及既有 CONTEXT.md:105、CHANGELOG:53）称「钩玄」语出《进学解》「拔萃便适，钩玄提要」——《进学解》原句为「记事者必提其要，纂言者必钩其玄」，「拔萃」不出自该篇。措辞非本轮新造，但已随 §7 补登写入 ADR-0013，建议人工核对引文（P3）。

### 其他复核记录

- **陈旧模式扫描**：`scripts/mcp_server.py` 引用、旧机盘符路径、8000 端口、`docker compose`、`run_digest`、「40 个工具」在现役文档面清零；剩余命中全在 CHANGELOG 历史条目与 dated 归档报告（按头部范围说明不核对）。交接文档（hermes-install-handoff）残留的旧机路径由新增「历史文档」横幅显式声明为原貌保留，不再计为缺陷。
- **P2-5 增量**：修复提交在 CHANGELOG（:18,:21）与代码注释（`lantai/cli/mcp.py`、`launcher_mcp.py`、`launch_mcp.bat`）新增多处 `.scratch/v068-docs-audit/` 引用——`.scratch/` 在 `.gitignore:48`，属已登记 P2-5「克隆侧链接悬空」类的增量，归票 10 一并决策。
- **现役文档 scripts 路径对账**（票 11 关注面抽样）：ops-runbook/矩阵/交接文档引用的 9 个 `scripts/*.py` 全部存在。
- **环境备注（复核可复现性）**：本机 venv 的 `PYTHONPATH` 混入 hermes-agent 路径，其自带正则包 `tests`（有 `__init__.py`）会遮蔽本仓库命名空间包 `tests`（无 `__init__.py`），conftest 加载即 ModuleNotFoundError——与 `launcher_mcp.py` 头注释所述 Hermes venv 污染同源；复测需先清 `PYTHONPATH`。

*复核执行：主审计员（P0 逐条亲核 + 新发现亲核 + 扫描/回归实测）+ 子审计 1 路（P1×11 取证）。仍为只读取证，未改动仓库文件。*

## 八、第三轮收口（2026-11-07：§七 复核新发现 R-1/R-2/R-3 修复，票 12）

| # | 修复 | 双侧证据 |
|---|------|---------|
| R-1 | `docs/lantai-manual-call.md` no_signal 反例换成「物业费在哪交」，类别措辞改「孤立短查询」 | 真实 `relevance_check` 不 mock 冒烟（worktree 代码实测）：旧例「大哥的电脑配置」→ `self_reference/identity` 放行（证实原句写反）；新例 → `no_signal`，与文档新表述一致 |
| R-2 | `docs/release-process.md` 第 7 项标题对齐门禁输出名「MCP 默认暴露」；第 3 项注明仅失败路径记账（绿灯汇总为 10 项 PASS） | 门禁输出名 `release_check.py:95,1306,1308`（`tests/test_release_check.py:843` 以输出名断言，故改文档侧）；失败路径记账 `:1268-1271` 成功不输出。随行同步 `:623` 指路串（引用文档标题的失败文案；非判定逻辑、无测试断言，避免原样复刻 R-2） |
| R-3 | `docs/lantai-manual-call.md` 改「短追问沿用上一轮**命中**判定（15s 热缓存；上一轮 no_signal 不沿用）」 | 真实调用实测：上一轮 no_signal + 12 字内短追问 → `no_signal`；上一轮命中 + 同句 → `session_followup_hot`（`prefilter.py:193-195` 单向短路） |

- 回归：`tests/test_release_check.py + test_prefilter.py + test_mcp_default_minimal_exposure.py` **91 passed**（PYTHONPATH 隔离实测）。
- 存量疑点（钩玄引文「拔萃便适，钩玄提要」非《进学解》原文）开票 13 转 `ready-for-human`：取证与三选一裁决项已备，agent 不代决。
- 票 10（P2 批量 + 两项维护者决策）、票 11（文档引用路径对账门禁）维持 needs-triage 不动；本流 agent 侧工作至此收口。

## 九、增量审计（2026-11-07 第四轮：原基线上 master 前进 7 提交，v0.68 票 01-03）

本报告原审计对象 `1195d29c`；当日其上 master 新增 7 提交（`22099ac9` 零命中轮检索响应补 event_id → `0295cace` 插件挂载解耦 → `70d60b39` ADR-0084 §2.5 + 协议文档 → `005cf702` 更正闸下沉 `lantai/retrieval/supersede.py` → `5ae6caf3` build() 排除 superseded → `3b1e8d23` MCP search 接闸 → `57cf71a6` RSS/arxiv 摄取挂獬豸闸与密钥检测）。对本增量做同口径核查（文件面 17 个，+1044/-113）：

**结论：无 P0/P1，总体评级对增量保持 ✅（文档可发布性不回退）。**

- 协议层已随行：ADR-0084 §2.5（零命中覆盖洞增补）、`docs/host-hook-protocol.md` §2.1（`event_id`/`request_id` 零命中契约行）随码更新，CHANGELOG 票 01 条目含回归锁明细。
- 使用面无被推翻的表述：手册（gate 放行口径 / force / as_of）、README:173（as_of 请求体语义）、CONTEXT.md、ops-runbook 对新行为均无失真表述；WHITEPAPER「supersedes 降权」句仍为真且属历史快照（P2-10 待挂横幅）。检索排序（`_apply_supersedes_order`）零改动，既有降权描述不失效。
- 测试纪律合格：新增 `tests/test_mcp_search_supersede_guard.py`（183 行）/ `test_context_supersede_guard.py`（181 行）/ `test_ingest_xiezhi_gate.py`（301 行）均明示不 mock 被测链路（真内存库 + 真 `handle_search` / 真 `apply_supersede_correction`，仅全局开关走 monkeypatch）；`supersede.py` 四符号自 shell_hook 逐字搬入、既有更正闸测试经别名即其回归锁。
- 余项（P2/P3，立票 14 + 票 10 补记）：
  - **D-1（P2）**「更正闸」未登记 CONTEXT.md 词汇表/ADR-0013 §3：v0.68 票 02 后跨 shell_hook 注入面、MCP search、context build 三面使用并出现在日志文案（`lantai/retrieval/supersede.py`）；且系描述性复合词、非传统意象取材，登记形式需裁决。→ 票 10 P2-7 同类，已补记票 10。
  - **D-2（P2）** v0.68 票 02/03 无 CHANGELOG `[Unreleased]` 条目（票 01 已有），大概率随该流收口自消。→ 票 14 记账。
  - **D-3（P2）** MCP search 更正闸 / 零命中轮 `event_id` 挂载语义在使用面（手册 search 行、插件文档回执段 `docs/lantai-hermes-plugin.md:22-27`）零随行；均为补写而非纠错。→ 票 14。
  - **D-4（P3）** 手册「`force` 绕过闸门」与更正闸边界未注明（force 绕拾遗 relevance gate，不绕更正闸——`lantai/cli/mcp.py` handle_search 中 guard 在 force 旁路之外，仅时效视图跳过）。→ 票 14。

**合并随行预告（非缺陷）**：本流分支与本增量交叠 3 文件——`CHANGELOG.md`（双方都在 `[Unreleased]` Fixed 顶部插入，冲突几乎必然）、`docs/host-hook-protocol.md`（票 05 回执延后段 vs §2.1 契约行，相邻不同 hunk）、`lantai/cli/mcp.py`（票 03 `handle_add` vs 增量 `handle_search`，不同函数，可自动合并）。合流时按此预估工作量；使用面补写（D-3/D-4）在合流后落笔，避免两头改。

## 十、增量审计（2026-11-07 第五轮：master 前进 2 提交，PR #35 记忆研究周报）

第五轮收口后 master 再前进 2 提交（`004dcc0f` 新增 `docs/research/papers/memory-weekly-2026-11-07.md` + `b2c941a0` merge PR #35），纯新增 1 个 dated 研究文档（+169，零既有文件改动）。同口径核查：

**结论：无 P0/P1，无新票，总体评级对增量保持 ✅。**

- 文档类别豁免：dated 研究周报（`docs/research/papers/`，同目录已有 11-03 周报先例），按本流门禁口径属归档历史文档豁免类（票 11 草案），非使用面；无独立 CHANGELOG 条目符合惯例（11-03 周报同样仅在建议被采纳时以出处形式进 CHANGELOG）。
- 代码断言逐条取证（对周报自报基线 `57cf71a6`），**全部属实、行号精确**：插件仅注册两回调（`hermes-plugin/lantai-hook/__init__.py:611-613`，master 行号精确；本 worktree 分支停 `1195d29c` 差 6 行，系 v0.68 票 01 增量所致，无矛盾）、`:498` `_flush_session`、`:112`/`:529` `_checkpoint_injected`、`checkpoint_service.py:200`、`retrieval_log.py:260` `mark_vetoed`、`supersede.py:59` 更正闸及其「superseded 且 superseded_by 非空才动」规则（与 docstring 逐字一致）、`enums.py:44-52` supports/causes/leads_to、`late_correction.py:73-78` 同事务直写 supersedes 边、全仓 `def purge` 恰为 `purge_expired`+`purge_facts_for_memory`（佐证「无按会话删除」）、`injection_count` 双文件（`forgetting.py:36,45`、`retrieval_log.py:138,144`）、`lantai/ingestion/dialogue.py`/`lantai/parsing/`/`lantai/llm/prompts.py` 存在、`scripts/shell_hook.py` 存在。
- 文档间引用同样属实：0.88 = LongMemEval-S cleaned 25 题会话级 recall@5（`docs/benchmarks/selfproof-report-2026-10.md:7,38`）；「被否决轮也触发 `post_llm_call`」查实内容在 `improve-2026-11-05-veto-no-write.md:79`（周报引 `:77`，±2 行漂移，对象本身是 dated 报告，不计发现）；handoff 第 3 步 = cli-config.yaml shell hook；PR #26/#30/#31/#33 合并记录核对（merge commits `77c49f50`/`8a725e15`/`dbde7dd2`/`1195d29c`）；「v0.66.0 之后 76 个提交」实测 `git rev-list --count v0.66.0..57cf71a6` = 76。
- 运行时观察（删除会话后 `rawdocument.session_id` 为空等）系作者标注的本地实测，静态审计不可复核，不计。
- 票 10/11/13/14 状态不变、无维护者批注；合并随行预告不扩大（PR #35 仅新增文件，与本流分支零交叠）。

本流 agent 侧继续保持收口：票 10（两项决策）、票 11（落点）、票 13（三选一）、票 14（落点/命名形式）全部等维护者批注。

## 十一、增量审计（2026-11-07 第六轮：master 前进 2 个首父提交，PR #34/#36）

第五轮基线 `b2c941a0` 之上 master 新增 2 个首父合并提交（`c91ab8f6` merge PR #34、`8175a745` merge PR #36，合计 11 个可达提交，16 文件 +1130/-39）：PR #34 收口 v0.68 票 05（MCP 入口三处断线：launcher/bat/verify 统一唯一入口 `lantai.cli.mcp:main`），并带 v0.68 票 07（add 工具 media_url 接线）、票 13（REST `/search` 与 graph_expand primary 面套更正闸）、票 15（拾遗补救扫描补 lifecycle 过滤）；PR #36 收口硬墙超时轮孤儿检索事件（`retrieval_log` 新增 `mark_abandoned` + `shell_hook._run_with_timeout` 新增 `_on_abandon`，巡检 2026-11-07 12:00）。对本增量做同口径核查：

**结论：无 P0/P1，总体评级对增量保持 ✅（文档可发布性不回退）；发现集中在「两侧重复修复分叉」与「随行缺口扩大」，全部归票 14/票 10 记账，agent 侧不代修。**

- **E-1（P2，合流协调——本流分支与 master 对同一批病灶各落一版修复）**：本流票 01/02/03（P0-3/P0-8 代码侧）与 master v0.68 票 05/07 是同一批病灶的两侧修复，交叠面从第四轮预告的 3 文件涨到 **5 文件**：`CHANGELOG.md`、`lantai/cli/mcp.py`、`launch_mcp.bat`、`launcher_mcp.py`、`scripts/verify_lantai.py`。其中 launcher/bat **语义分叉**：分支版保留 venv 候选 graft（`.venv` 优先、`.venv-audit` 兜底，把 site-packages 注入 sys.path）；master 版彻底不 graft（非项目 venv 如实死于可解释的 ModuleNotFoundError——graft 外来 venv 正是 launcher 要防的 Hermes 污染），bat 回落 `python` on PATH。**取侧建议：launcher/bat/verify/mcp.py 一律取 master**（master verify 是分支超集：Shell Hook 子进程 `encoding="utf-8"` 修复 + 「数据目录」改信息项 + `main` 可调用核验；`handle_add` 两侧同 hunk 语义等价，`params.get("media_url", "")` vs `or ""`）；分支侧丢代码版、保文档票修复（矩阵/手册/交接文档 master 增量未触碰，P0 文档修复仍只在本分支）。`docs/host-hook-protocol.md` 本轮增量未再动——第四轮预告的该文件冲突源自更早的 §2.1/§2.5 随行（分支 P1-1/P1-2 修复 vs master 票 01 增补），该冲突预告仍然成立、与本轮无关。
- **E-1b（P2，随 E-1 产生的合流后假记载义务）**：分支 `CHANGELOG.md:18` 票 01-09 补记条目明文「venv 候选 `.venv` 优先、旧名 `.venv-audit` 兜底」——master 实现已移除该行为；合流时若原样保留该句，`[Unreleased]` 收口成发布历史后即为假记载。必须随取侧改写该句（或整条并入 master 票 05 条目、文档修复明细另列）。审计报告 §七 P0-3 的同款表述属 dated 历史记载，不改。
- **E-2（P2，CHANGELOG 随行缺口扩大，归票 14 D-2 补记）**：v0.68 票 07/13/15 三个修复均无 `[Unreleased]` 条目；PR #36 hook 修复显式跳过 CHANGELOG（dated 巡检文档自述「为避免冲突，没有改 CHANGELOG.md」）。先例对照：票 05 有条目、2026-11-06 22:00 巡检修复（offload_read）有条目——发布收口前需统一补齐。
- **E-3（P3，随行缺口，归票 14 D-3/D-5 补记）**：① 更正闸消费面扩大到 REST `/search` 与 graph_expand primary（票 13），使用面随行缺口覆盖面同步扩大——README:173 的 as_of 语义无被推翻（「写在 URL 上被静默忽略、返回当前视图」仍真；时效视图整段跳闸与该口径自洽），仍属补写非纠错；② 超时轮孤儿事件收尾（PR #36）：ADR-0084 状态机 `pending → {acked, missed, vetoed}` **无矛盾**（`mark_abandoned` 走既有 `pending→missed` 转移、只动 pending，与「missed 扫描只认 pending」互恰），但「超时轮就地抹原话置 missed」这一新入口路径在 ADR-0084 与协议文档 §3 均零记载（dated 巡检文档已完整记载），补写点候选：ADR-0084 §2.5 邻位或协议文档 §3 一行；③ 拾遗 lifecycle 过滤（票 15）：ADR-0069/0076 未钉扫描集含 superseded，无被推翻表述，零随行缺口。
- **测试纪律合格**：5 个新测试文件均真内存库直调被测链路——`test_rest_search_supersede_guard.py`（真路由函数 + 真更正链）、`test_shiyi_supersede_filter.py`（真 `remedy_recall` + 真 `apply_supersede_correction`）、`test_mcp_entry_points.py`（子进程干跑 launcher/verify）、`test_mcp_add_media_url.py`（vision_caption/extract_candidate 外部替身 = AGENTS.md 允许的 LLM mock，`handle_add` + 真 SQLite 真实执行）、`test_hook_timeout_orphan_event.py`（真 `_handle_one` + 真 `mark_abandoned`，仅 `hybrid_search` 换睡过硬墙的替身模拟外部 embed/rerank 慢——被测的超时编排与收尾链路真实执行，在允许边界内）。
- **巡检 dated 文档（`docs/research/兰台巡检-2026-11-07-1200.md`，豁免类）**：代码断言静态核对属实（`mark_abandoned` 只动 pending / 抹 `query_text`+`query_norm_hash`+`used_ids` / 置 missed、`_on_abandon` 完成与放弃同锁二选一、QUERY 分支接线 `_query_abandoned`）；其「其余新提交的审计结论」段对 v0.67 票 01 / v0.68 票 01-03 的复核与第四轮结论互证。其提出的 `scripts/reextract.py --apply` 绕獬豸闸建议属代码面事项、已记账于 dated 文档，非文档一致性缺陷，不另立票。
- **P2-5 类增量**：本批代码注释/测试 docstring 新增 5+ 处 `.scratch/` 引用（`v0.68-next-window-2026-11/07`、`_v068_probe_ticket13_rest_supersede.py`、`_t13_probe_graph_expand_supersede.py` 等），属票 10 P2-5「克隆侧悬空引用」类增量，归票 10 一并决策。

本流 agent 侧继续保持收口：票 10（两项决策 + P2-5 增量）、票 11（落点）、票 13（三选一）、票 14（落点/命名形式 + 本轮 E-1 取侧、E-1b 改写义务、E-2/E-3 补记）全部等维护者批注。

## 十二、增量审计（2026-11-07 第七轮：master 未前进，增量空）

第六轮基线即当前 master 头：本地 `master` 与 `origin/master` 均为 `8175a745`（fetch 后核实，非本地过期引用），首父视角自第六轮上界零前进；此前 10 个首父提交已由第四至六轮全部覆盖。票据面 `.scratch/v068-docs-audit/issues/` 最后改动时间停在 12:43–12:44（第六轮落笔时），票 10/11/13/14 无维护者批注，E-1/E-1b 合流协调面（5 文件交叠）无变化。**结论：本轮无审计对象、无新发现、无新票，流保持收口；下次触发的核查入口仍是 `origin/master` 是否越过 `8175a745`。**
