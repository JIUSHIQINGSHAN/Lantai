# 为兰台记忆（Lantai）贡献

感谢您对兰台记忆（Lantai）的兴趣！产品运行与接入见 [README](README.md)，工程导航见 [开发入口](docs/project/DEVELOPMENT.md)。以下流程用于开发、验证和提交贡献，不等同于生产部署或版本发布。

## 开发环境搭建

### 前置要求

- Python 3.11+；冻结开发环境与主要 CI 使用 Python 3.14
- Git
- uv（用于同步仓库 [uv.lock](uv.lock) 冻结依赖）
- Node.js 24.9+（DSH bundle 和宿主适配契约测试；`LANTAI_TEST_NODE` 可指定普通 Node 可执行文件）
- 已安装的 Chrome / Chromium（真实浏览器回归；开发依赖包含 Playwright，不在测试中下载浏览器）
- （可选）Docker
- （可选）pre-commit（独立工具，不包含在项目开发依赖中）

### 快速开始

在已有检出中开发前，先按 [开发测试环境](docs/project/development-environment.md) 核对实际解释器和冻结依赖。正在供服务或旧库工具使用的环境保留，开发测试另建；以下快速开始适用于新的源码检出。

```bash
# 1. 克隆仓库
git clone https://github.com/JIUSHIQINGSHAN/Lantai.git
cd Lantai

# 2. 按锁文件同步运行时、开发组与可选依赖，不改写 uv.lock
uv sync --locked --all-extras --python 3.14

# 3. 激活环境（Linux/macOS）
source .venv/bin/activate
# Windows PowerShell：.\.venv\Scripts\Activate.ps1

# 4. 先运行受影响的测试，再做完整验证
python -m pytest tests/test_features.py tests/test_prefilter.py -q
```

测试本身创建合成数据库，无需先运行 `scripts/init_db.py`，也不要复制生产 `.env`。手动启动服务时另按 [README](README.md) 配置专属 `LANTAI_HOME` 与凭据。已有运行环境、存量数据库和其他工作树保持独立；`UV_PROJECT_ENVIRONMENT` 可将开发解释器放在仓库外，Windows 临时目录与 `--basetemp` 必须采用 [开发测试环境](docs/project/development-environment.md) 的任务专属 ASCII 路径。宿主 `PYTHONPATH` 有污染时先在本轮终端清空，不改系统变量。

可选安装提交钩子：先独立安装 pre-commit，再同时安装代码检查与提交信息两个阶段；只安装默认阶段不会校验提交信息。

```bash
uv tool install pre-commit
pre-commit install --install-hooks --hook-type pre-commit --hook-type commit-msg
```

没有安装钩子时仍须手动执行下方 Ruff 和测试检查，不能把钩子是否安装当作验收证据。

## 需求与实现流程

遵循 [标准开发流程](docs/development-workflow.md) 和 [AGENTS](AGENTS.md)：

1. 在 `.scratch/<feature>/spec.md` 写清目标、边界与验收条件，在 `issues/<NN>-<slug>.md` 拆分可独立验证的票据，使用 [本地票据规则](docs/agents/issue-tracker.md) 与 [分诊标签](docs/agents/triage-labels.md)。`.scratch/` 不随 Git 提交，PR 需另附必要的需求与验证摘要。
2. 修复缺陷先稳定复现，再按单一假设隔离根因；不靠吞异常或放宽断言消除红灯。
3. 涉及接口、存储、权限或算法取舍时补 ADR；新正式中文名先登记 [CONTEXT](CONTEXT.md)，再用于实现与文档。
4. 功能与缺陷修复先见证失败测试，再最小实现；每个新增或修改的核心函数至少有一个真实输入、内部逻辑不 mock 的冒烟测试。
5. 自查正确性、契约、安全与性能，执行受影响测试、Ruff、全量回归及遗忘质量检查，记录解释器、命令、退出码和实际计数。
6. 按下方 PR 流程交付；纯文档改动不单独升级版本或推标签。正式发版另走人工闸门。

## 分支策略

- `master`：稳定分支，只接受经过测试的合并
- `feat/xxx`：新功能开发
- `fix/xxx`：Bug 修复
- `docs/xxx`：文档更新

从最新 `origin/master` 建分支；外部贡献者先 fork。并发任务使用独立工作树，各自留票据，不在已有未提交工作上混入改动。提交后推送自己的分支并向 `master` 提 PR，描述问题、变更范围、验证结果及尚未覆盖的边界；不要把个人数据、`.env` 或本地运行产物带入提交。

## 提交规范

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

### 提交格式

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### 类型（Type）

| 类型 | 说明 |
|---|---|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `build` | 构建系统或外部依赖变更 |
| `chore` | 非业务性代码修改 |
| `ci` | 持续集成流程变更 |
| `docs` | 文档修改 |
| `style` | 代码样式调整（不影响功能） |
| `refactor` | 代码重构（不改变功能逻辑） |
| `perf` | 性能优化 |
| `test` | 测试用例修改 |
| `revert` | 回退提交 |

### 范围（Scope）

可选字段，用圆括号包围，描述变更涉及的模块。常用范围：

- `api`：API 路由
- `gate`：相关性闸门
- `evolution`：演化模块
- `memory`：记忆模块
- `storage`：存储层
- `retrieval`：检索模块
- `ingestion`：摄取模块
- `security`：安全相关
- `docs`：文档
- `ci`：CI/CD

### 示例

```
feat(evolution): add salience-based contradiction gate

Fog integration: when new fact conflicts with existing memory,
compare salience scores before overwrite. Higher salience wins.

Refs: #42
```

```
fix(storage): resolve SQLite deadlock in apply_proposal

Use outer session for MemoryEdge to prevent self-deadlock
under concurrent write operations.

Refs: #38
```

## 代码规范

### Python 风格

- 遵循 PEP 8
- 行长度：100 字符
- 使用 double quotes
- 排序 imports（ruff 自动处理）

### 类型注解

所有公共函数必须有类型注解：

```python
def search_memories(
    query: str,
    top_k: int = 10,
    trace: bool = False,
) -> SearchResults: ...
```

### 文档字符串

公共函数、类、模块必须包含文档字符串：

```python
def add_memory(title: str, content: str, lane: str) -> Memory:
    """Add a new memory entry.

    Args:
        title: Brief title for the memory.
        content: Full content of the memory.
        lane: Memory lane (fact/chat/preference).

    Returns:
        The created Memory object.
    """
```

## 测试

### 运行测试

```bash
# 代码与格式（范围与 CI 一致）
python -m ruff check lantai/ tests/ scripts/
python -m ruff format --check lantai/ tests/ scripts/

# 运行特定模块（相关性闸门测试在 test_features / test_prefilter）
python -m pytest tests/test_features.py tests/test_prefilter.py -q

# 全量测试与覆盖率（Windows 另加任务专属 --basetemp）
python -m pytest tests/ -q --cov=lantai --cov-report=term-missing
```

全量测试包含真实 Chromium UI 回归。浏览器发现共用 `tests/support/ui_browser.py`，依次检查 `LANTAI_TEST_CHROMIUM`（显式路径无效时直接报错）、PATH、`PLAYWRIGHT_BROWSERS_PATH` 指定目录、当前平台默认 Playwright 缓存，最后检查 Windows/macOS 标准 Chrome 安装位置。默认缓存为 Windows 的 `%LOCALAPPDATA%/ms-playwright`、Linux 的 `${XDG_CACHE_HOME:-~/.cache}/ms-playwright`、macOS 的 `~/Library/Caches/ms-playwright`；支持这些目录内常见的 `chromium-*` 与 `chromium_headless_shell-*` 布局。特殊值 `PLAYWRIGHT_BROWSERS_PATH=0` 的包内安装、自定义/未来布局及其他安装位置请用 `LANTAI_TEST_CHROMIUM` 指向实际可执行文件。发现函数只定位已有文件、不下载浏览器；MCP 沙箱等端到端测试还需要开发依赖中的 Playwright Python 包。缺少依赖或浏览器时应修复环境，不通过跳过掩盖问题。

CI 两个测试 job 显式使用 ubuntu-latest 的 `/usr/bin/google-chrome`；本地 Windows 验证不代表该 runner 已实际运行。CI 路径失效时测试会直接报错，需修复环境准备，不能靠跳过浏览器测试继续。

```powershell
$env:LANTAI_TEST_CHROMIUM = "C:\Program Files\Google\Chrome\Application\chrome.exe"
python -m pytest tests/test_ui_ime_browser.py tests/test_ui_dialog_browser.py -q
```

Linux/macOS 可用 `LANTAI_TEST_CHROMIUM=/path/to/chrome python -m pytest tests/test_ui_ime_browser.py tests/test_ui_dialog_browser.py -q`。这些测试使用临时 profile、合成数据和本机测试服务；合成组合事件不代表已验证 OS 原生输入法，Chromium 通过不代表 Firefox/Safari 或屏幕阅读器验收。

遗忘质量采用隔离数据目录运行：

```bash
LANTAI_HOME="$(mktemp -d)" DATABASE_URL= CHROMADB_PATH= \
  API_KEY=test-key OPENAI_API_KEY=test-key \
  python scripts/run_forgetting_quality.py --check
```

上例为 Bash，临时目录只放合成测试数据；Windows 按 [开发测试环境](docs/project/development-environment.md) 设置独立目录。正式发布的宿主真实库连接绊线、覆盖率与 Node 强制验证由 [发布门禁](docs/release-process.md) 统一执行，普通开发命令不能替代发布验收。

### 测试要求

- 新增功能必须包含对应测试
- Bug 修复必须包含回归测试
- 新增或修改核心存储、检索、决策函数时，必须真实构造最小输入直调主路径；外部模型网络可替代，内部计算不可 mock
- 全量测试必须通过，记录本次实际失败、通过和跳过数；README 徽章不能替代当前工作树的验收记录
- 测试命名清晰：`test_<feature>_<scenario>`

## PR 检查清单

提交 PR 前，请确认：

- [ ] 受影响测试与带覆盖率的全量测试通过，跳过项与测试边界如实说明
- [ ] Ruff 检查与格式检查通过；使用 pre-commit 时两个阶段均通过
- [ ] 遗忘质量检查在隔离目录中通过，未连接或改写生产库
- [ ] 提交信息符合 Conventional Commits 规范
- [ ] 新功能包含对应测试
- [ ] Bug 修复包含回归测试
- [ ] 文档按影响更新（README、CHANGELOG、ADR、CONTEXT 等）；本地票据之外的必要说明已附在 PR
- [ ] 无敏感信息泄露（API keys、密码等）

## 版本发布流程

版本发布遵循 [Semantic Versioning](https://semver.org/)，完整规范见 [docs/release-process.md](docs/release-process.md)。

发布前必须通过：

1. **发布门禁**：`python scripts/release_check.py vX.Y.Z --online` 全部通过。当前判定 **14 项**，覆盖 Ruff、CI 步骤与文档引用对账、版本格式和一致性、wheel、Git、MCP 默认暴露、命名空间来源、schema、观测保留、老库回放、带覆盖率和宿主真实库绊线的全量测试、隔离遗忘质量；逐项口径以发布流程和脚本为准。
2. **版本同步**：`pyproject.toml` 为真源；同步 README 徽章、源码标签与镜像示例、`lantai/api/app.py`、`lantai/cli/mcp.py`、`uv.lock`，核对 CHANGELOG 最新发布段。
3. **CHANGELOG 收口**：`[Unreleased]` → 新版本段，顶部保留新的 `[Unreleased]`；需要代号的版本先按命名规则登记。
4. **真实环境前提**：兼容的 Node.js、Chromium 和冻结依赖就绪。`--skip-suite`、`--allow-dirty` 等排查选项不是发布放行。

上传步骤：更新版本号 → 收口 CHANGELOG → 复跑门禁 → `chore(release): vX.Y.Z` 提交 → `git tag vX.Y.Z` → `git push origin master --tags` → 验证 GHCR 镜像。

**发布是人工闸门**：门禁脚本只检查不推送；Agent 只准备，不代替维护者确认。

### 版本号规则

- **MAJOR**：破坏性 API 变更（`feat!:` 或 `BREAKING CHANGE:`）
- **MINOR**：向后兼容的新功能（`feat:`）
- **PATCH**：向后兼容的 Bug 修复（`fix:`）

## 安全

发现安全漏洞？请勿公开 issue，直接联系维护者。

## 许可证

贡献即表示您同意您的贡献将以 [MIT 许可证](LICENSE) 发布；保留现有版权和许可声明。
