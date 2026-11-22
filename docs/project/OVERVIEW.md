# 兰台项目说明

兰台为 AI Agent 提供长期记忆的摄取、质检、演化、检索和遗忘能力，供需要跨对话保留知识的客户端与服务使用。产品说明和接口以根目录 [README](../../README.md) 为入口；本文件负责便携开发套件的项目导航。

## 范围与验收

本次将 dev-suite 1.2.0 仓库引用版的项目初始化和工作记忆机制应用到此检出。开发工作沿用 [标准流程](../development-workflow.md)、[贡献指南](../../CONTRIBUTING.md) 和 [发布流程](../release-process.md)。具体任务的完成条件记录在 `.scratch/<feature>/spec.md` 与对应票据中，验证结果以实际执行证据为准。

领域术语由根目录 [CONTEXT](../../CONTEXT.md) 统一维护，架构取舍记录在 [ADR 目录](../adr/)；本入口沿用这些既有资料。

## 开发入口

准备修改或交接时，读取 [开发与项目记忆入口](DEVELOPMENT.md)。当前检出的工作笔记保存在 `git rev-parse --absolute-git-dir` 指向目录下的 `agent-suite-project/WORK.md`；其他 worktree 各自管理资料，不合并笔记。

本项目在 DSH 中使用套件支持的手动初始化方式，并由根目录 [AGENTS](../../AGENTS.md) 提供读取指针。客户端专用 SessionStart 钩子须在实际使用对应客户端时另行接入和验收。
