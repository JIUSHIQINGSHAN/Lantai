# 兰台三宿主接入包

本包为 Mirasim、Claude Code、Codex 和 DSH 提供现有兰台 MCP 与命令 Hook 的入口配置。MCP 默认保持六工具最小集，核心协议与服务实现保持基线契约。详细规格、边界和验收口径见 [ADAPTERS.md](ADAPTERS.md)。

`plugin.json` 与 `.claude-plugin/plugin.json` 共用 `.mcp.json` 和现有 `lantai-memory` 技能。打包配置使用已安装的 `lantai-mcp` 命令，要求宿主进程的 PATH 能找到所选兰台环境，并在启动宿主前显式配置 `API_KEY`、`LANTAI_HOME` 及所需模型环境。配置文件中不保存密钥。本包不安装插件、不注册频道助手、不改变现有宿主状态。

为消除 PATH 与源码目录的歧义，可用已同步 `uv.lock` 的 Python 生成三宿主的独立配置。以下命令只预览，不读取凭据、不创建数据目录：

```powershell
python integrations/mirasim-lantai/configure.py --host claude-code --home D:/LantaiData
python integrations/mirasim-lantai/configure.py --host codex --home D:/LantaiData
python integrations/mirasim-lantai/configure.py --host dsh --home D:/LantaiData
```

默认使用运行此命令的解释器；也可用 `--python` 显式指定已安装兰台依赖的绝对路径。结果为“相对文件名 → 文件正文”的 JSON，Claude Code 使用 `.mcp.json`，Codex 使用 `.codex/config.toml`，DSH 使用 JSON 兼容 YAML 的 `cordis.patch.yml`。生成结果绑定当前源码位置；移动仓库后重新生成。

所选数据目录须先按仓库 [README](../../README.md) 的运行步骤完成数据库初始化。配置生成不建库，客户端入口也不自动创建或迁移数据库；握手和工具发现成功不能代替实际读写验收。

Claude Code/Codex 需要命令钩子时显式加 `--hooks`：包含 `SessionStart` 底本与 `UserPromptSubmit` 检索，缺省不启用。加 `--output <全新审阅目录>` 才写出文件，目标目录已经存在时整项拒绝，避免覆盖用户配置；审阅后按宿主规则合并，保留现有 MCP、Hook、规则和插件。规则样本在 `rules/`，是否加载取决于宿主配置，不能把文件存在当成已生效。

Windows 命令 Hook 支持含空格的路径；含命令解释器特殊字符（如 `&`、`%`）的路径明确拒绝，请选择普通数据目录。MCP stdio 使用独立实参数组，不受此 Hook 限制。

DSH 结果供 [归因桥接插件](../dsh-lantai/README.md) 使用，默认禁用。启用前只保留一条 `lantai` MCP 连接，避免工具重名；按每次调用传递来源标签，不改变模型实参、读取身份或数据权限。核心记忆与对话采集继续由既有独立开关管理。

本包生成的入口要求显式数据目录和非空 `API_KEY`，并把 SQLite、Chroma 指向同一目录；启动前校验失败只输出诊断，不启动服务。MCP 固定核心档，命令 Hook 复用 `scripts/shell_hook.py` 的协议、回执和失败处理。治理用完整工具集须由维护者另配原生入口并显式授权，不能把默认档扩大成全量。
