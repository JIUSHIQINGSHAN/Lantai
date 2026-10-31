# 兰台记忆（Lantai）v0.62.0 发布交付报告 —「磨勘」

**发布时间**：2026-10-30  
**版本代号**：「磨勘」（Mokan · 多源认知证据交叉勘验与可信度自证引擎）  
**发布提交**：`a16b7191` (`chore(release): bump version to 0.62.0 (磨勘)`)  
**Git Tag**：`v0.62.0`（已推送至 `origin/master`）  

---

## 一、版本核心攻坚背景与出处

- **意象出处**：出自《宋史·选举志》「磨勘之法，核验资历功过以定升降」，宋代设审官院、三班院核验官吏实绩功过与文书证据。
- **架构转正**：完成 `ADR-0083`（《磨勘：多源认知证据交叉勘验与可信度自证引擎》）编写与架构转正，登记于 `CONTEXT.md` 词汇表与 `docs/adr/0013-naming-system.md`。
- **突破点**：将多源异构证据交叉核验、事实可信度纯函数自证打分（TrustScore）与矛盾冲突审计提升为一级认知引擎 `MokanEngine`。

---

## 二、工单端到端完成情况

| 工单 | 状态 | 交付成果与接缝验证 |
| :--- | :--- | :--- |
| **01-mokan-core-engine** | `done` | 1. 彻底拔除 `scripts/shell_hook.py` 运行时修改 `lantai.retrieval.hybrid` 导致的单例交叉污染；<br>2. 修复 `test_genglou_migration.py` SQLite NOT NULL 约束与 `KaogongResult` 契约缺陷；<br>3. 交付 `lantai/cognition/mokan.py` 核心引擎及 `tests/test_mokan_engine.py` 4 接缝不 mock 冒烟单测全绿。 |
| **02-mokan-service-mcp-and-context** | `done` | 1. 封装 `lantai/services/mokan_service.py` 统一服务入口；<br>2. 集成 REST 端点 `POST /mokan/verify` 与 `GET /mokan/audit`（`lantai/api/routes_mokan.py`）；<br>3. MCP 96 工具池扩充（新增 `mokan_cross_verify`，支持 `verify`/`audit` 双模式，且与 `TOOL_HANDLERS` 1:1 双向对齐）；<br>4. 认知上下文 `CognitiveContext` 深度融合 `mokan_digest` 结构化切面并强化樊篱防注入围栏；<br>5. 交付 `tests/test_mokan_service_and_mcp.py` 4 接缝不 mock 冒烟单测全绿。 |
| **03-v0620-release-gate** | `done` | 1. 版本一致性 6 处引用及 CHANGELOG 严格同步为 `0.62.0`；<br>2. Ruff lint & format 门禁 100% 通过（765 文件全绿）；<br>3. `release_check.py v0.62.0 --online` 门禁 3/3 通过；<br>4. 打标 `v0.62.0` 并成功推送至远程 `origin/master --tags`。 |

---

## 三、门禁与质量数据留证

- **代码规范**：`uv run ruff check . && uv run ruff format --check .`（All checks passed, 765 files already formatted）。
- **冒烟测试集**：`uv run pytest tests/test_mokan_engine.py tests/test_mokan_service_and_mcp.py tests/test_genglou_migration.py tests/test_shell_hook.py`（38 passed in 17.30s）。
- **MCP 96 工具一致性**：`len(TOOLS) == 96` 与 `len(TOOL_HANDLERS) == 96` 严格双向 1:1 对齐。
- **发布状态**：当前分支 `master`，工作区干净（`working tree clean`），远程 tag 已就绪。
