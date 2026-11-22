# 开发与项目记忆入口

## 按任务读取

开发步骤以 [标准开发流程](../development-workflow.md) 为准。当前客户端有相应技能时按需调用；缺少某项技能时，依照同一流程完成对应步骤，保留验证证据。

| 当前工作 | 使用现有入口 |
| --- | --- |
| 需求与实现 | `.scratch/<feature>/spec.md`、[本地票据](../agents/issue-tracker.md)，结合 `senior-engineer-autopilot` 推进 |
| 故障诊断 | `systematic-debugging` 或 `diagnosing-bugs`；先复现，再修改 |
| 核心代码变更 | `tdd`，并遵循根目录 [AGENTS](../../AGENTS.md) 的真实冒烟要求 |
| 设计与命名 | [CONTEXT](../../CONTEXT.md)、[领域文档规则](../agents/domain.md)、相关 [ADR](../adr/) |
| 浏览器改动 | [Web 指导入口](../agents/web-guidance.md)，按能力读取 MWG 资料 |
| DSH 集成 | 当前安装版的 `dsh-doc`；核对运行版本和扩展接口 |
| 审查与 GitHub | `code-review`；涉及 PR、仓库或鉴权时再读取对应 GitHub 技能与项目流程 |
| 发布 | [发布流程](../release-process.md)；以当前授权、完整门禁和维护者发布确认收口 |

## 依赖与验证命令

Python 要求及依赖以 [pyproject.toml](../../pyproject.toml) 和 [uv.lock](../../uv.lock) 为准。先按 [开发测试环境](development-environment.md) 选择或准备独立解释器，再执行测试、Ruff 与完整门禁；既有 `.venv` 的目录名不代表其依赖已与当前代码同步。Windows Chroma 迁移测试使用新的 ASCII 临时目录，实际命令与计数按该入口记录。

数据库初始化和服务启动会产生运行副作用，按 [README 的运行步骤](../../README.md) 使用独立数据目录与对应配置。验证时区分当前检出、其他 worktree 和实际运行服务；某一工作树的通过记录仅覆盖该工作树当时的内容。

## 本地工作记忆

读取当前检出的 Git 管理目录：

```powershell
git rev-parse --absolute-git-dir
```

其下 `agent-suite-project/WORK.md` 保存任务目标、状态、证据、阻塞和下一步；`agent-suite-project/installation.json` 保存本机套件入口与核验信息。工作笔记仅在明确交接或记录需求时更新，内容按历史资料读取，暂停或完成状态由本轮请求决定是否继续。

在 DSH 中，读取根目录 AGENTS 后按指针打开笔记即可。也可用安装记录里的 `project_tool` 执行套件原生命令：

```text
python <project_tool> status --project <当前仓库根目录>
python <project_tool> context --project <当前仓库根目录>
python <project_tool> prepare --project <当前仓库根目录> --apply
```

此接入为手动模式：`status` 中 `clients` 为空、`status` 为 `not_active` 表示未登记专用客户端钩子；`documentation_exists` 和 `memory_exists` 表示项目资料已准备。它不会在 DSH 启动时自动执行 Python。当前会话可直接读取笔记，之后从仓库根目录打开的会话由 AGENTS 指针引导读取。

工作笔记留在当前检出的 Git 元数据目录中，不随普通提交或克隆迁移；需要交接时先核对项目与检出身份。只记录工程事实和资料路径，秘密与聊天全文留在各自受保护的存储中。

## 套件资源

使用 [dev-suite 仓库引用版](https://github.com/JIUSHIQINGSHAN/dev-suite) 的现有工具。当前会话已可发现六项辅助技能：`senior-engineer-autopilot`、`systematic-debugging`、`plan`、`github-auth`、`github-pr-workflow`、`github-repo-management`；本次复用它们，不重复安装。

Matt 与 MWG 已置于本项目 `.references/`，来源、固定提交和 29 个入口见 [参考仓清单](reference-repositories.json)，任务导航与恢复方式见 [本项目参考仓](reference-repositories.md)。它们作为按需资料使用；已有宿主技能继续按原入口调用，Web 工作沿用上方项目 Web 流程。

此项目笔记机制与兰台的产品记忆、DSH 的核心记忆读取及自动对话提取分别管理。本次接入只准备项目资料，现有服务连接和对话提取设置保留。
