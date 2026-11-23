# Mirasim Lantai 记忆插件 (Mirasim Lantai Integration Plugin)

本插件是兰台（Lantai 记忆中枢）面向 Mirasim 平台及其托管的所有智能体（Claude Code、Codex、Antigravity、DSH）的标准集成包。

## 一、架构与目录结构

```
integrations/mirasim-lantai/
├── plugin.json                 # Mirasim / Codex 插件清单
├── .claude-plugin/
│   └── plugin.json             # Claude Code 插件清单兼容映射
├── .mcp.json                   # stdio JSON-RPC 2.0 MCP 桥接配置
├── rules/
│   ├── AGENTS.md               # 通用智能体长期记忆协同规则
│   └── CLAUDE.md               # Claude 专用长期记忆协同规则
├── skills/
│   └── lantai-memory/
│       └── SKILL.md            # 兰台长期记忆操作技能手册
└── README.md                   # 插件说明与接入文档
```

## 二、平台级功能特性

1. **全智能体 MCP 投影**：向 Mirasim 全平台所有主流智能体（Claude Code、Codex、Antigravity、DSH 等 7 类）自动投影 22 项 `lantai` MCP 工具集。
2. **长期记忆协同规则**：内置主动召回与长期沉淀规则（`rules/AGENTS.md`），确保各助手在涉及用户画像、环境配置、历史指代及复杂排错时主动召回，关键成果自动沉淀。
3. **记忆治理技能**：集成 `skills/lantai-memory`，指导智能体执行记忆检索、沉淀、候选裁决与冲突消除。
4. **专属频道助手**：在 Mirasim 频道助手中注册了 `ai-lantai`（兰台记忆），可随时进行交互式记忆库治理。

## 三、安装与管理

在 Mirasim 环境下，可通过 `mirasim ui-cli` 驱动安装：

```powershell
# 安装插件
& mirasim ui-cli call installPlugin --args '{"source": "D:\\Projects\\记忆\\integrations\\mirasim-lantai", "scope": "global"}'

# 查看插件状态
& mirasim ui-cli call listPlugins
```
