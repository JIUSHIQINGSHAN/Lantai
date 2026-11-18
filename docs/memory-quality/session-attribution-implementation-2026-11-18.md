# Session Attribution 实施验收

日期：2026-11-18
分支：`fix/session-attribution-20261118`
基线：生产升级提交 `f9999a23`（Lantai 0.68.2）

## 已实现

- 新增受限、不可变的 `Attribution` envelope：`io.lantai/attribution`，只接受 version、request_session_id、request_id；不接受用户/租户/权限字段，不修改 Principal 或读取 scope。
- 写入口把显式 origin session 优先于 request attribution；缺失时才 fallback；冲突记录 `attribution_state=conflict`。旧客户端缺字段继续使用 NULL/旧行为，历史行不回填。
- schema v46 新增零正文 `ingest_event`，记录接收时刻、认证主体维度、origin/request attribution、channel 和最新 stage/outcome。候选、提案、最终新记忆、旧记忆 dedup/reinforce/merge 都能关联；原始记忆的 origin 不覆盖。
- 对话同步/异步、普通 add、raw add、coalesce 缓冲、MCP 原生 handler 和 REST search/dialogue 均支持可选 attribution；异步 idempotency 重放不重复创建事件。
- `collect_ingest_liveness` 改为阶段化、半开 UTC 窗口 `[start,end)` 的聚合：候选/提案/MemoryItem/摄取事件分别统计；NULL/空白 session 标为 unknown，不推断后台；按 tenant/user/agent 收窄；跨会话或跨 agent 不再互相掩盖。旧计数键保留但文案如实。
- `ingest_event` 终态保留策略默认 30 天；processing/candidate/pending 等未决事件保留，终态按 updated_at 清理。
- DSH review bundle `integrations/dsh-lantai/`：默认禁用、静态配置、不读取 profile/conf、每次 MCP 调用从 ToolExecution 生成 `_meta`，支持 stdio/Streamable HTTP、并发、取消、超时、重连、schema 和 teardown。未安装/启用。
- 独立 Python bridge 副本 `scripts/lantai_mcp_bridge.py`：显式白名单映射 attribution 到 REST body，无全局 `X-Session-Id`，不输出 key 或 HTTP 错误正文。
- coalesce job fingerprint 改为结构化 JSON，修复冒号字段碰撞；idle flush 重新执行注入/secret gate，批内所有 secret 命中合并处理。

## 验证

- 最终全量第三轮：`4208 passed, 5 skipped, 2093 warnings, 92% coverage`，584.59s；真实开发库绊线已 armed，未命中 `[HIT]`，以临时 `D:\AI_Cache\lantai-attribution-tests-*` 数据目录运行。
- 受晚期安全/agent scope/MCP schema 修改影响的定向回归：`198 passed`。
- 归因/事件/MCP/桥接/缓冲/REST 集成新增或定向套件：通过；包含真实 SQLite、FTS、Chroma 外部边界替身、真实 FastAPI 路由和 loopback HTTP。
- DSH 安装版 Electron/SDK bundle：4 组 smoke 通过，覆盖并发 A/B、agentless、错误脱敏、输出 schema、取消、超时、工具列表刷新、重复列表回滚和 teardown。
- 离线遗忘质量门禁：PASS（stale 0、typo 1、fresh 1、temporal 1、superseded order 1）。
- schema v46 migration/legacy parity 定向测试：23 passed；没有 v0.68.2 git tag，未伪造旧版 tag 快照。
- Ruff check/format 与 py_compile 通过。

## 限制和部署顺序

1. 本分支未修改生产、未切换 DSH profile、未读取 profile secrets。bundle 安装/启用仍需人工选择，且必须先禁用旧 `include:mcp-lantai`，避免重复注册工具。
2. 后端先部署 schema v46 和 Python bridge/服务，再启用 DSH bundle。回滚时先禁用 bundle、恢复旧 bridge；不要删除 event 表或回填历史 session。
3. 生产真实 DSH GUI ToolRuntime 端到端调用尚未在此分支启用验证；bundle 的安装版本机 loopback smoke 已通过。session 是不可信关联标签，不是 ACL 或认证凭据。
4. 当前 event 记录是最新 outcome 的有限保留诊断关联，不是永久事件溯源账本；正文、查询词和凭据不入 event 表。

## 接续实施：协议错误与整链验收

本次接续以 `50998cf4` 为基线，在同一独立工作区完成以下增量：

- 安装版 SDK 2.2.0 的 `listTools` 已自动分页，初始“只取第一页”假设被实测否定。真实缺口是 `_listAllPages` 对重复游标/同页静默 break，可能将未完成列表当成功发布。适配器保留 SDK 的分页、缓存、schema/header 校验及 64 页上限，在公开 request 边界拒绝重复游标；整批发现共用总超时。分页错误、重复工具、游标循环、页数超限或总超时均保留原工具集。
- 独立 Python 桥对 HTTP/domain 错误设置 MCP `isError`；继续只返回 HTTP 状态，不回显服务端错误正文。无 id 的取消/进度通知不产生响应帧，有 id 的同名请求仍明确返回 method-not-found。
- 新增 `tests/test_dsh_attribution_chain.py`，将实际 DSH 适配器/registry、安装版 SDK、真实 stdio 子进程、Python urllib、FastAPI、SQLite/FTS/Chroma 串为同一次测试。仅外部 LLM/embedding 使用仓库统一替身。

整链覆盖两个会话并发发起调用（stdio 桥顺序处理 HTTP）、agentless 不虚构 session、显式历史来源优先且记录 conflict、请求正文伪造 user_id 不替换认证主体、两次同文摄取形成不同事件并关联同一旧记忆、检索 request_id 落库且读取 scope 不变、REST 校验失败与摄取拒绝如实传播到 DSH。前者在接收前拒绝无 event，后者保留 rejected event；两者不可混计。

验证：安装版 DSH 5 组 smoke 通过（包含分页全部失败形态与总超时），整链单独运行 1 passed，前期归因定向回归 109 passed，Ruff check/format 通过，遗忘质量门禁 PASS。

最后完整回归：`4212 passed, 5 skipped, 2143 warnings, 92% coverage`，1176.06s，退出码 0；日志 `.scratch/session-attribution/followup-final.log`。`followup-final-tripwire.txt` 中宿主真实库绊线已 armed，零 `[HIT]`。本次配置了安装变量，DSH 整链用例实际执行，未跳过。未配置变量时另行实测为 6 passed / 1 skipped，带明确原因且未启动后端服务。

前一轮全量为 4210 passed / 5 skipped / 2 failed，失败均来自新环境跳过点漏登 `tests/test_skip_mask_inventory.py`。已按原门禁要求登记新增环境前置条件，并将守卫直接表达为安装环境变量检查；保持扫描判据不变。最终全量两项均通过。实现已由 `ae8cd9e3` 收录，随后提交测试门禁修复与本验收结果；没有修改业务实现以消除该测试门禁失败。

本次仍未安装/启用插件、修改生产或读取真实 conf/profile。自动采集所有对话轮次不在此适配包的实现范围；真实 GUI ToolRuntime 与真实认证服务的验收继续保留为未执行。客户端取消等待不保证服务端已接收的写入撤销。

集成验收补充：installed DSH bridge-chain 测试需要外部 Electron 运行时作为 Node 和 Python 子进程协作，设置 `LANTAI_TEST_DSH_CHAIN=1`、`DSH_NODE_EXECUTABLE` 和 `DSH_INSTALL_DIR` 后执行。若未选择外部运行时，测试按明示环境 skip；超时则按失败处理，不自动跳过。集成期间出现过子进程在 runner 启动前无输出的超时；此前“桌面单实例占用”的解释未被证实，不能作为根因结论。随后同安装 Electron 的最小 Node 表达式及完整整链均通过（2026-11-19，1 passed，9.28s），最终发布门禁需显式启用该测试重新验证。独立 Python bridge loopback smoke 不依赖 GUI。
