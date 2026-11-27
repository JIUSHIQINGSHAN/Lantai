# 开发测试环境

测试环境按 [pyproject.toml](../../pyproject.toml) 和 [uv.lock](../../uv.lock) 配套准备。源码要求 Python 3.11+，当前主要 CI 使用 Python 3.14；环境目录名或 Python 版本本身不能证明依赖已经同步。

## 选择与准备

已有服务正在使用的解释器和数据目录继续保留。为开发测试选择一个新的独立环境，再从项目根目录同步冻结依赖。Windows 下可执行文件放在项目之外的工具目录，避免继承工作区的低完整性标签；以下目录是可替换的示例：

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'D:\DevEnvs\lantai'
uv sync --locked --all-extras --python 3.14 --link-mode copy
$devPython = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
$env:PYTHONPATH = ''
```

`--locked` 保持锁文件，并将当前源码注册为该独立环境的可编辑包，供隔离子进程和 `lantai-mcp` 安装态测试使用；安装不会启动服务。Linux/macOS 可同样指定独立的 `UV_PROJECT_ENVIRONMENT`，解释器位于该环境的 `bin/python`。已经具备缓存时可加 `--offline`，缺少缓存则保留明确失败或在有网络的授权环境完成下载。

当前机器已验证的解释器位置保存在当前 Git 管理目录的 `agent-suite-project/development-environment.json`。本机复用入口如下，先核对记录的 `project` 与当前仓库根目录相符：

```powershell
$gitDir = git rev-parse --absolute-git-dir
$dev = Get-Content -LiteralPath (Join-Path $gitDir 'agent-suite-project/development-environment.json') -Raw | ConvertFrom-Json
$repoRoot = (Resolve-Path -LiteralPath (git rev-parse --show-toplevel)).Path
if ((Resolve-Path -LiteralPath $dev.project).Path -ne $repoRoot) { throw '开发环境记录不属于当前检出' }
if (-not (Test-Path -LiteralPath $dev.python -PathType Leaf)) { throw '开发解释器不存在，请重新准备环境' }
$devPython = $dev.python
$env:PYTHONPATH = ''
```

该本机记录不随普通克隆迁移；新检出按本节重新准备环境。工作区内现有 `.venv` 可能仍供旧服务或旧库工具使用，先检查版本与使用者，再决定是否能更改。

## 运行与验收

先执行受影响测试。Windows 的 Chroma/HNSW 迁移测试需要 ASCII 临时路径，使用新的任务专属目录；不要把 `--basetemp` 指向已有数据目录，pytest 会管理该目录内容。每轮同时隔离操作系统临时根目录，避免其他进程创建的同前缀目录干扰回收断言：

```powershell
$runTemp = 'D:\DevTemp\lantai-pytest-' + [guid]::NewGuid().ToString('N')
$null = New-Item -ItemType Directory -Path $runTemp
$env:TEMP = $runTemp
$env:TMP = $runTemp
$env:TMPDIR = $runTemp
$testTemp = Join-Path $runTemp 'pytest'
& $devPython -m pytest tests/test_attribution_integration.py tests/test_chroma_upgrade.py tests/test_chroma_migration.py --basetemp $testTemp -q
& $devPython -m ruff check lantai tests scripts
& $devPython -m ruff format --check lantai tests scripts
```

普通全量回归使用同一已核验解释器：`& $devPython -m pytest tests/ --basetemp $testTemp -q`。这里 `$testTemp` 沿用上方任务专属 ASCII 目录，仅保存合成测试数据。`--basetemp` 只控制 pytest 的临时路径，不改变 `tempfile.gettempdir()`；因此 `TEMP`、`TMP`、`TMPDIR` 也必须在启动测试子进程前设置，且仅作用于本轮 shell，不改系统环境变量。该命令使用 conftest 的数据隔离；提交验收记录实际解释器、退出码和计数。发布放行仍按 [发布流程](../release-process.md) 的完整门禁执行，其全量测试另含覆盖率与宿主真实库绊线，且已处理 Windows 的 ASCII 临时目录。

浏览器端到端测试依赖开发组内的 Playwright，使用真实 Chromium 与本地 HTTP 服务。已有 Chrome/Chromium 可通过 `LANTAI_TEST_CHROMIUM` 指定绝对路径；未指定时按 `tests/support/ui_browser.py` 的宿主浏览器探测规则查找，测试不会下载浏览器。缺浏览器会明确失败，不跳过。CI 沿用 `/usr/bin/google-chrome`，无需安装 Playwright 自带浏览器。

`node --test tests/test_adapter_hermes_openclaw_node.mjs` 可独立运行：缺省使用 PATH 中的 Python 执行仓库内只依赖标准库的 NDJSON 测试端，临时帧日志写入 `.scratch/` 并自动清理。自选工装时必须同时指定 `LANTAI_TEST_PYTHON`、`LANTAI_TEST_HOOK`、`LANTAI_ADAPTER_TEST_LOG`；只指定一部分会报错。该工装只验子进程与宿主协议，真实兰台存储链由对应 Python 契约测试另验。

遗忘质量验证也需要独立的测试数据配置；使用发布流程中的隔离执行方式，不让验证程序默认打开宿主库。环境修复不意味着已部署、已迁移或已批准发布。

## 已知环境错配

`AttributeError: 'dict' object has no attribute 'to_json'` 曾由旧 Chroma 0.6.3 接收当前代码的 `configuration` 字典触发。先对照锁文件检查实际安装版本；本项目已采用 Chroma 1.5.9 API，修改配置回退到旧接口会偏离既有升级决策。

`Windows native HNSW work-dir/target requires an ASCII-only path` 表示迁移演练路径不满足 Windows 约束，应按上方临时目录方式重跑，保留路径检查和测试断言。

旧 Chroma 0.6.3 与新 1.x 磁盘格式不同。当前新环境只对全新合成测试库运行；存量库迁移必须按 [Chroma 迁移规程](../chroma-migration.md) 在冷副本和全新目标上另外执行，保留旧运行时与旧数据。
