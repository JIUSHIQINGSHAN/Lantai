# Stage 5-A 悬镜与 MCP App 交互诊断

## 范围与环境

- 当前检出 `D:\AI_Cache\lantai-stage5-ui`，分支 `feat/stage5-ui`，基线 `7f33e572`。
- 需求与进展保存在本检出 `.scratch/stage5-ui/spec.md` 和 `issues/01-ui-interaction-and-first-screen.md`。
- 使用冻结解释器 `D:\AI_Cache\lantai-development\env-0691\Scripts\python.exe`，Python 3.14.10。
- 冻结环境缺少浏览器测试包；按锁文件离线安装 `playwright==1.65.2`、`greenlet==3.5.6` 到本检出 `.scratch/stage5-ui/browser-deps/`，没有改共享环境或依赖清单。
- 测试入口 `.scratch/stage5-ui/run_pytest.ps1` 清空 `PYTHONPATH`，仅为测试加载上述包，使用独立 TEMP/TMP/TMPDIR、合成库和不可达外网端点；通过共享互斥锁和文件锁串行使用 `--basetemp D:\DevTemp\pt-ascii`。
- 浏览器由 `tests/support/ui_browser.py` 发现，为本机已有 Chromium headless shell revision 1243；执行 `--version` 返回 `Google Chrome for Testing 153.0.8010.12`。产品脚本和真实临时 SQLite 执行；请求延迟/异常仅在外部响应边界注入。

## 可复现缺陷与修复

### 图谱名称随重绘消失

复现：真实控制台加载图谱后，`#xuanjingSvgTitle` 数量为 0，SVG 缺少预期名称。

假设与求证：`renderXuanjing` 清空 SVG 全部子元素，连 HTML 声明的 title/desc 一并删除；对应 DOM 断言真实失败。

修复：仅清理绘图层和 defs，保留标题与描述。加载和刷新后标题/描述各一个，可访问名称为“悬镜记忆星图”。

### 刷新后详情关闭不能返回原节点

复现：从节点以 Space 打开详情、刷新图谱再按 Escape，节点未重新获得焦点。

假设与求证：抽屉保存旧 SVG 节点引用，刷新重建后引用脱离文档；真实键盘断言失败。

修复：保存业务节点 ID，关闭时查找当前 DOM 中的对应节点。刷新内容不主动抢焦点；Escape 仅作用于活动悬镜页，且让原生模态弹窗先处理。

### MCP 星图选点重绘丢焦点

复现：Enter 与 Space 选中 MCP 星图节点后，详情正确但焦点落到文档主体，两项参数用例失败。

假设与求证：选点触发重绘并移除当前获得焦点的 SVG 元素；失败输出显示新节点已选中但未聚焦。

修复：重绘前记录焦点节点 ID，重绘后恢复到对应新节点；Space 按下阻止滚动，松开才激活。

## 其他交互与性能改动

- 主悬镜提供加载/空图/错误提示，加载时标记忙碌并禁用刷新，最新请求结束后恢复；响应格式不符时保留已有快照。
- 星点提供可聚焦按钮语义、选中状态和可见焦点；详情具名，打开后聚焦关闭按钮，关闭后返回节点。
- MCP 悬镜在图面显示空态，继续使用既有错误状态与快照保留。
- 核心 MCP App 的两路总览读取以 `Promise.allSettled` 并行执行，一路失败仍显示另一路结果；错误继续由现有状态区域呈现。
- 保留请求版本隔离：较晚返回的旧筛选结果不能覆盖新筛选，离开悬镜后的旧响应不能重绘隐藏页。
- 不可信正文、错误文本仍通过纯文本 DOM 展示；不改 MCP 权限、数据契约、CSP 或消息来源校验。

## 指导来源与取舍

途径为本地文件检索，查询词为 `keyboard focus management`、`accessible error announcement`、`conditional async dependencies`。MWG 参考副本实际 revision 为 `64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65`，只读工作树干净。

- `guides/accessibility/accessibility.md`：采用键盘按钮行为、焦点指示与返回、SVG 命名、状态播报。由真实键盘、DOM 和可访问名称验证。
- `guides/accessibility/accessible-error-announcement.md`：采用集中错误播报原则；未引入表单 `:user-invalid` 桥接，因为本票处理读取失败。
- `guides/performance/conditional-async-dependencies.md`：已核对适用范围；未采用顶层 await 或 polyfill，首屏优化直接并行已有宿主工具调用。
- 保留现有非模态详情布局，避免为只读抽屉封锁整个页面；无构建和同源资产遵循 ADR-0025，既有名称沿用 CONTEXT。

## 本轮实跑证据

以下 pytest 命令均通过上述隔离入口运行；日志在 `.scratch/stage5-ui/evidence/`。

| 验证 | 结果 | 日志 |
| --- | --- | --- |
| 新增图谱名称与焦点回归，修复前 | 4 failed，退出码 1，42.80 秒 | `red-focus-and-metadata.log` |
| 同组修复后 | 4 passed，退出码 0，21.82 秒 | `green-focus-and-metadata.log` |
| 八个相关测试文件 | 69 passed，退出码 0，135.43 秒 | `ui-mcp-regression.log` |
| 非空错误快照补验 | 5 passed、15 deselected，退出码 0，54.28 秒 | `snapshot-boundaries.log` |
| `python -m ruff check lantai tests scripts` | 通过，退出码 0 | 本轮命令输出 |
| `python -m ruff format --check lantai tests scripts` | 869 个文件已格式化，退出码 0 | 本轮命令输出 |
| `git diff --check` | 通过，退出码 0 | 本轮命令输出 |

相关回归命令：

```powershell
& '.scratch/stage5-ui/run_pytest.ps1' -Label 'ui-mcp-regression' tests/test_mcp_app_browser.py tests/test_xuanjing_ui_smoke.py tests/test_xuanjing_mcp_app.py tests/test_mcp_app_protocol.py tests/test_studio_ui.py tests/test_ui_interaction_browser.py tests/test_ui_dialog_browser.py tests/test_ui_ime_browser.py -q --tb=short
```

非空错误快照补验命令：

```powershell
& '.scratch/stage5-ui/run_pytest.ps1' -Label 'snapshot-boundaries' tests/test_xuanjing_ui_smoke.py -k 'error_keeps_snapshot_and_recovers' -q --tb=short
```

补验覆盖 1440/390 像素宽度和 HTTP 503/图谱结构异常两种响应，共新增 4 个主悬镜用例，另外复跑 1 个已有 MCP 悬镜错误恢复用例。因此与前述 69 项重叠 1 项，不将两轮计数直接相加；15 deselected 是 `-k` 的定向选择，其余原有用例已在 69 项中通过，没有 skip 或放宽断言。

错误文本包含合成 HTML 注入样本，真实断言确认按文本显示、没有插入图片或执行脚本、保留两个旧节点和统计、恢复刷新按钮并清除忙碌状态；正常响应恢复后错误提示隐藏。两种宽度均断言页面无横向溢出。

八张错误/恢复截图保存在 `.scratch/stage5-ui/evidence/test_observatory_error_keeps_s*-snapshot-*.png`。人工查看桌面错误、手机错误、手机恢复三张，确认提示换行、旧图可见且不与图例重叠。截图使用合成数据，不含真实记忆正文。Ruff 两项在补测加入后已重新通过。

## 需求与规范自审

按用户要求自行完成两轴审查，没有派子代理。需求轴核对键盘、空态/错态、并行首屏、异步切页和真实浏览器覆盖；规范轴核对现有样式、原生 ES Modules、纯文本渲染、权限边界和临时库隔离。未发现需扩大改动范围的问题；没有新增被路由、worker 或 service 调用的存储/检索/决策核心函数。

本轮未运行全仓 pytest、遗忘质量或发布门禁，未进行合并、推送、打 tag 或发布。移动 viewport 不能代表真机；没有验证 Firefox、Safari、原生输入法或屏幕阅读器。回归期间护栏已逐例恢复 11 例 `_guard_instance` 残留，隔离卫生由另路 Stage 5-C 处理，本票未改其模块或护栏。
