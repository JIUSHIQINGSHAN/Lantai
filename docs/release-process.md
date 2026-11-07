# 版本上传规范流程

> 适用范围：兰台记忆（Lantai）发布新版本并上传到 GitHub / GHCR 的完整流程。
> 原则：**发布是人工闸门**。脚本和 Agent 只做检查与准备，最终 `push tag` 由维护者确认后执行。

## 术语

- **版本上传**：把已收口的版本以 `vX.Y.Z` tag 推送到 `origin/master`，触发 GitHub Actions 构建并推送 `ghcr.io/<owner>/lantai:vX.Y.Z` 镜像。
- **发布版本**：`pyproject.toml` 中的 `version`，遵循 Semantic Versioning。
- **发布门禁**：`scripts/release_check.py`，只读检查，不修改任何文件、不推送。

## 发布节奏（2026-10-27 起，承实际节拍数据）

历史 tag 间隔（8-30→9-12→9-26→10-07，各 11–14 天）表明自然节拍就是**约两周一个窗口**，据此立规：

1. **窗口版（minor，X.Y.0）**：每个 15 天开发窗口收口时发一版。窗口主题即版本主题，**版本代号必登记**（ADR-0013 §7 + `CONTEXT.md` 词汇表）。窗口内评审/调参/文档类成果一并入这版，不为单独一件事开版。
2. **补丁版（patch，X.Y.Z>0）**：两类触发即发、**免代号**（沿 v0.23.1–v0.23.3 先例）：
   - **生产回归/安全雷**：修完过全量门禁当发，不等窗口；
   - **留证钉板**：公开基准报告、迁移指南等需要把「证据锚定到不可变引用」的成果，完成后立即打 tag（报告留证链里的代码版本从 moving 的 master 变成固定的 tag）。
3. **不触发发版的事**：上游/竞品动态、调研报告、纯文档票——归入当期窗口，不单独出版本。
4. **软上限**：两版间隔不超过 3 周。超了只有两种合法原因：窗口没收口（记原因进 `map.md`）或上版刚发不足一周（攒进下一版）。
5. **人工闸门不变**：tag 与 push 仍是维护者确认动作；节奏只约束「何时该准备好」，不替代确认。

## 上传前门禁

`python scripts/release_check.py vX.Y.Z`（上传前可加 `--online` 检查远程 tag）实际判定
**13 项**，全部 PASS 才允许进入上传步骤。下表与门禁输出**逐项点名一一对应**——照单
自查，缺一项就会被门禁打红（自查口径窄于门禁 = 假绿）：

1. **CI lint 门禁**：`ruff check` + `ruff format --check` 全绿。门禁先自查自身可跑性——它红 = CI 的全量测试一步不跑。
2. **CI 步骤覆盖核对**：CI（tests.yml）每条检查命令都有门禁项认领、无失配死账，且门禁 pytest 口径包含 CI 口径、addopts 钉子仍惰性。
3. **文档引用对账**（工单 09 防复发哨兵）：现役文档（`docs/*.md` 顶层 + `README.md` + `CONTRIBUTING.md`）中引用的 `scripts/<name>.py` 必须真实存在，引用已删/改名入口即判红并点名 file:lineno（流 C 审计 P0-3/6/7 同根因的结构性拦截）。豁免走显式白名单（`DOCS_REFERENCE_WHITELIST`，现仅收编 P0-3 修复的「旧入口已删除」历史注记）——新历史文档先登记再入档，fail-closed。
4. **目标版本格式**：X.Y.Z（SemVer）。仅失败路径记账（成功时不在绿灯汇总输出）。
5. **版本一致性**：6 处版本引用（`pyproject.toml` / README 版本徽章 / README Docker 示例 / `lantai/api/app.py` FastAPI `version=` / `lantai/cli/mcp.py` serverInfo `version` / `uv.lock` 根包版本）+ CHANGELOG 最新发布段 = vX.Y.Z。
6. **构建产物（wheel）**：`python -m build --wheel` 离线构建成功，METADATA 版本 = vX.Y.Z，含 `lantai/` 载荷与 `lantai-mcp` / `lantai-server` 入口。
7. **Git 状态**：当前分支为 `master`，除待提交的发布变更外工作区干净，`vX.Y.Z` tag 本地不存在，远程 `origin` 存在；加 `--online` 时同时确认远程同名 tag 不存在。
8. **MCP 默认暴露**（票 05 续31，评审线 G3/N-04/C-15/R-02）：`lantai/cli/mcp.py::_get_active_tools()` 的**默认档必须是 `HERMES_CORE_TOOLS` 核心白名单**——不设 `LANTAI_MCP_PROFILE`/`MCP_TOOL_PROFILE`、设空、或设了认不出来的取值，一律 fail-closed 到核心集（未知取值额外可观测告警，绝不静默）。全量注册表只有一个入口：显式 `LANTAI_MCP_PROFILE=full`。门禁项「MCP 默认暴露」以探针实测判定（`isolated_probe_env()` 剥掉 profile 变量，只看默认档；部署侧设变量不能让该项变绿，也不能拿它冒充默认已收窄）。收窄/放宽属契约变更：动 `_get_active_tools()` 的语义必须同步改 `tests/test_mcp_default_minimal_exposure.py` 的判据与本条口径，需要全量测试面里依赖非核心工具的模块显式打标 `pytestmark = pytest.mark.mcp_full_surface()`（这是「白纸黑字选了 full」的登记形态，不许拿它当默认）。**注入帧点名约束（巡检 2026-11-06 22:00 P1）**：`scripts/shell_hook.py` 等给模型看的注入文案里点名的 MCP 工具必须在核心集内——往注入帧加新工具指引之前，先把该工具并入 `HERMES_CORE_TOOLS`（或换用已在核心集的工具），否则默认档部署的模型会去调一个 `tools/list` 里不存在的工具（-32602）。该约束由 `tests/test_hook_named_tools_in_default_profile.py` 自动拦截（PR #32），此处登记是给读门禁的人与 AI 说明约束来源。
9. **命名空间 provenance**（票 04，`scripts/release_check.py::namespace_provenance_issues`）：第一方（`lantai`/`tests`）与三方哨兵（`pydantic`/`sqlmodel`/`fastapi`）在**当前门禁会话**里的解析落点必须全在本仓或活动解释器内。发布门禁恰恰运行在维护者本机——Hermes 宿主拉起的 shell 带全局 `PYTHONPATH`（hermes-agent 目录 + 宿主 venv site-packages），会从两个位次遮蔽：正式包压命名空间（`tests` 面）、PYTHONPATH 先于 venv site-packages（三方依赖面，实测 hermes 的 pydantic 二进制不兼容崩）。不拦就是把「跑在别人的代码上」当绿灯发布。**判红时的处置：清空 `PYTHONPATH`（或换干净 shell）复跑门禁**，不要改代码迁就环境。本仓侧的防线（勿回退）：`tests/__init__.py` + `tests/support/__init__.py` 正包标记（删掉即回归票 04 病灶）、`tests/support/harness_state.py::scrub_foreign_sys_path` 装配期清污（AST 扫描器对登记式写入器豁免 SYSPATH_WRITE，其余文件仍一律判红）；回归锁 `tests/test_pytest_collection_env_shading.py`（收集/解析双臂 + 依赖遮蔽臂）与 `tests/test_release_check.py` 的 provenance 三臂（含 sitecustomize 位次实证与修复前世界 tripwire）。威胁边界如实声明：门禁拦的是 PYTHONPATH/包解析位次劫持；解释器级启动钩子（sitecustomize 直接装 import hook、改 `sys.modules`）超出文件级判据的拦截能力，属宿主机治理问题。CI 的发布阶段（`build.needs: tests`）由套件内的同款回归锁间接闸住；本条是维护者本机闸门的显式登记。
10. **迁移与列约束漂移**：临时 `LANTAI_HOME` 内 `init_db()` 全链通过，metadata 与实表列/NULL 约束一致。
11. **老库升级回放与列对齐**（门禁自动执行，票 06）：`tests/fixtures/legacy-schema/*.json`里每个锚点（上一发布标签的真实 DDL 快照，**不含任何行数据**）经当前迁移链升到目标版本后，必须与模型完全对齐——缺表 / 缺列 / 主键 / 索引一律为零，NULL 方向差异只能落在`scripts/schema_parity.py` 的书面清单内。锚点为空、迁移链没跑到目标版本、清单里出现复现不出来的死条目，都判红。**每次发布后补一份上一个标签的快照**：`python scripts/legacy_schema_snapshot.py --tag v<上一版>`。
12. **全量测试 + 宿主目录绊线**：门禁自己以 **CI 同口径**跑 `pytest tests/ -q --cov=lantai --cov-report=term-missing -o addopts= -p no:cacheprovider -p <绊线插件>`，退出码 0 且**至少一条真实通过用例**（空跑/skip 不算测试通过），全程未连接宿主真实 `LANTAI_HOME`。发布者自查请用同参数命令——裸 `pytest tests/ -q` 口径窄于门禁（缺覆盖率插桩与绊线），不作为放行依据。
13. **遗忘质量离线门禁**：`python scripts/run_forgetting_quality.py --check` 通过（CI tests.yml 的同一条步骤）。

配套人工项（门禁不判、缺一不可）：

- CHANGELOG 已收口：`## [Unreleased]` 内容迁移到 `## [X.Y.Z] - YYYY-MM-DD`，顶部保留新的 `## [Unreleased]`。
- 版本代号（若该版本需要）已按 [ADR-0013](adr/0013-naming-system.md) R7 登记到 `CONTEXT.md` 词汇表与 ADR-0013 映射表。

> 门禁只读：不改文件、不提交、不打 tag、不推送；`--skip-suite` / `--allow-dirty` /
> `--allow-mcp-full-exposure` 是排查旗标，跳过哪项如实记账（[SKIP]），不代表可发布。

## 上传步骤

### 1. 更新版本号

`pyproject.toml` 是版本事实来源，其余五处引用同步（README 两处；与门禁
`VERSION_REF_SPECS` 一一对应，改漏任何一处都会被门禁打红）：

- `pyproject.toml`：`version = "X.Y.Z"`
- `README.md`：版本徽章（`badge/version-X.Y.Z-blue`）+ Docker 示例（`lantai:X.Y.Z`）
- `lantai/api/app.py`：`FastAPI(title=..., version="X.Y.Z", ...)`
- `lantai/cli/mcp.py`：`serverInfo` 的 `version`
- `uv.lock`：根包（`name = "lantai"`）的 `version`——bump 后跑 `uv lock` 重算；
  uv.lock 漂移曾让 0.66.0 差点假绿（票 05 续18），门禁现已核对

> `BACKUP_MANIFEST_VERSION` 是备份清单格式版本，不随发布版本递增；只有备份格式变更时才单独提升。

### 2. 收口 CHANGELOG

- 把 `## [Unreleased]` 下的条目移到 `## [X.Y.Z] - YYYY-MM-DD`。
- 顶部保留新的 `## [Unreleased]`。
- 提交信息遵循 Conventional Commits。

### 3. 复跑门禁

```bash
python scripts/release_check.py vX.Y.Z --online
```

### 4. 提交并打 tag

```bash
git add -A
git commit -m "chore(release): vX.Y.Z"
git tag vX.Y.Z
```

### 5. 上传

```bash
git push origin master --tags
```

### 6. 验证 CI 产物

- 在 GitHub Actions 确认 `Build and Push Docker Image` 成功。
- 拉取镜像并冒烟：

```bash
docker pull ghcr.io/JIUSHIQINGSHAN/lantai:vX.Y.Z
docker run -d -p 8767:8767 \
  -e API_KEY=your-admin-key \
  -e OPENAI_API_KEY=sk-xxx \
  ghcr.io/JIUSHIQINGSHAN/lantai:vX.Y.Z
curl http://127.0.0.1:8767/health
```

### 7. 发布后记录

- 更新 README 里程碑 / 文档索引（如需要）。
- 在结果或交接文档中补充本次发布（如 `docs/aidumem-port-results.md`）。
- 若本次发布登记了新正式中文名，确认 `CONTEXT.md` 词汇表与 ADR-0013 已同步。

## 故障与回滚

- **门禁失败**：禁止上传。修复后重新跑门禁，不带病发版。
- **tag 打错 / 提交需修正**：删除本地与远程 tag 后修正再重新打：

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
```

- **CI 失败**：保留既有 tag / 镜像，修复后以新 PATCH 版本发布；不重写已推送的 `latest`。
- **镜像异常**：旧 tag 镜像不会被覆盖，可继续使用上一版本；先确认根因再发新版本。

## 自动化边界

- `scripts/release_check.py` 只读：不改文件、不提交、不打 tag、不推送；`--online` 也只查询远程，不写任何内容。
- Agent 可执行第 1-3 步与全部检查；第 4-5 步（提交 / tag / 推送）由维护者确认后执行，或由维护者显式授权后执行。
