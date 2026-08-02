# manifest.json 插件清单

Type: grilling
Status: resolved
Blocked by: 11

## Question

aiduMEM 有 `manifest.json` 插件清单，描述插件能力供宿主发现。remembrance 是否需要？

1. **目标宿主生态**：如果走 MCP 路线，MCP 已有自己的 manifest 约定。如果走 Shell Hook，是否需要额外清单？
2. **清单内容**：工具列表、端点列表、配置 schema、版本号？
3. **是否生成**：手写还是从 FastAPI OpenAPI spec 自动生成？

依赖票据 11（MCP server 形态）的结论——如果 11 决定只走 REST API，此票据可能直接关闭。

## Answer

决议（grilling 2026-08-02 与用户确认，阻塞已随 11 清除）：

### 不需要独立 manifest.json

- 11 决定两者并存（Shell Hook + MCP）
- MCP 自带 manifest 约定
- Shell Hook 脚本本身就是清单
- 如果 11 只选了 Shell Hook 或 REST only，此票据直接关闭
- 结论：关闭，不建 manifest.json
