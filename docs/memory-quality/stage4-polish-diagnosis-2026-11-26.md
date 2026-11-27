# Stage 4 浏览器与宿主入口收口

## 范围与实现

工作树为 `lantai-stage4-polish`，分支 `feat/stage4-polish`，基线
`b1e94086fb41414b00697ebf73b4c414139375a7`。仅本地提交，不推送、不发版。
本轮保留既有断言、跳过登记与发布判据；不扩大默认六工具档位。

真实 uvicorn 服务使用独立 SQLite，关闭生产 lifespan 调度器。Playwright 启动真实
Chromium，控制台经真实 REST 加载数据，MCP 资源运行在真实 sandbox iframe 中。
测试宿主经 postMessage 与 HTTP 将请求交给真实 MCP 分派器；只在宿主消息边界注入
错误、畸形响应及嵌套搜索结果，不替换浏览器 DOM。测试退出会关闭浏览器和服务线程。

## 根因与修复

| 复现 | 原因 | 修复与观察 |
| --- | --- | --- |
| 主控制台换分轨后仍显示旧节点详情；空图只显示数量 | 刷新没有重校选中项、重应用搜索或提供空态 | 按 ID 重校详情，保留搜索状态，空图明确提示；过期加载不覆盖新视图 |
| MCP 悬镜嵌套主题通知无效，深色背景仍是浅色 | 仅支持直接参数，且上一次宿主 CSS 变量残留 | 两种参数形状兼容，清理旧变量，补深色默认色 |
| 无宿主工具能力时仍能刷新或改服务端筛选 | 未读取初始化的 serverTools 能力 | 禁用服务端控件并拦截调用，仍允许宿主推送结果 |
| 核心面板搜索显示“未命名记录”且没有正文 | 实际检索结果为 results[].memory，渲染器只读顶层 | 同时接受嵌套记忆与既有平铺记录，按纯文本渲染 |
| 核心面板刷新失败无状态反馈 | 点击处理器放弃了可能拒绝的 Promise | 统一刷新入口显示失败并支持重试，不产生未捕获异常 |
| 核心面板直接主题通知无效、缺样式变量时浅色不可读 | 参数形状不兼容、CSS 只有深色回落 | 接受两种通知形状，补浅色前景、背景与控件色 |
| Windows Hook 配置接受 data(command) | CreateProcess 的 argv 引用规则不等于 cmd 解释规则 | 括号与其他解释器字符一起拒绝；MCP 实参数组不受限 |
| 独立 Node 测试无工装环境时失败 | 三个隐含环境变量只由 Python 父测试提供 | 独立命令自动准备标准库 NDJSON 测试端，和 Python 测试共用工装；部分覆盖明确报错 |

红测摘要：悬镜先后为 `2 failed, 3 passed`、`3 failed, 6 passed`；核心面板为
`5 failed, 4 passed`；括号路径专项为 `1 failed, 8 passed, 21 deselected`。
修复后所有本轮指定用例通过。原始输出留在本工作树 `.scratch/stage4-polish/`。

## 本轮验收

解释器：`D:\DevTemp\lantai-stage2-integrated-relay\venv\Scripts\python.exe`，
Python 3.14.7、Playwright 1.65.2、Node 24.22.1。所有手动 pytest 均显式使用
`--basetemp D:\DevTemp\pt-ascii`，未并行复用该目录。

指定 Python 命令：

```powershell
& 'D:\DevTemp\lantai-stage2-integrated-relay\venv\Scripts\python.exe' -m pytest tests/test_mcp_app_browser.py tests/test_xuanjing_ui_smoke.py tests/test_xuanjing_mcp_app.py tests/test_mcp_app_protocol.py tests/test_adapter_claude_codex_dsh.py tests/test_adapter_hermes_openclaw.py tests/test_dsh_core_release_gate.py tests/test_skip_mask_inventory.py tests/test_release_check.py -q --basetemp D:\DevTemp\pt-ascii
```

原样摘要：

```text
142 passed, 4 warnings in 226.36s (0:03:46)
```

四条警告来自既有测试主动传入 safe / none / typo-value / all，验证未知档位保持
核心集，未放宽门禁。测试护栏另报告并逐例回滚 MCP guard 的模块级缓存残留；
该提示不代表生产数据写入或未处理的测试失败。

| 命令 / 运行方式 | 原样计数摘要 |
| --- | --- |
| `node --test tests/test_adapter_hermes_openclaw_node.mjs` | tests 40; pass 40; fail 0; skipped 0 |
| `node --test integrations/dsh-core-memory/test/*.test.mjs` | tests 129; pass 129; fail 0; skipped 0 |
| Electron 运行 `integrations/dsh-lantai/tests/run-installed.cjs` | tests 5; pass 5; fail 0; skipped 0 |
| Electron `--test` 运行核心插件三个 runtime.test.cjs 文件 | tests 46; pass 46; fail 0; skipped 0 |

Electron 为本机 DSH 0.2.2-rc.5 的 44.7.0 / Node 24.21.0，使用合成 profile、
凭据与本地响应；不安装、启用或重启日常宿主。Python 套件另覆盖生成入口经已安装
DSH 注册器与 SDK 的调用。最终日志分别为 `targeted-final-diagnostic.log`、`node-final.log`、
`dsh-core-node.log`、`dsh-electron.log`、`dsh-core-electron.log`。

Playwright 已加入 dev 依赖并更新锁文件，仅增加它及 greenlet；生产依赖版本保持。
Ruff 无参数检查及项目既有作用域检查均通过。无参数格式检查还发现既有
`docs/tongpao-workspace.md` 一处三行示例排版，本轮仅收为一行以通过检查。

wheel 构建通过，四份浏览器资源与工作树源码逐字节相同；安装入口仍准确为三个。
`uv lock --check --offline` 通过；锁文件重新生成时 revision 从 5 变为当前本机 uv
输出的 3，原有全部包版本核对未变。

## 偶发超时与残余风险

首轮指定套件为 `142 passed, 4 warnings in 232.24s (0:03:52)`。后续复跑曾出现
`1 failed, 141 passed, 4 warnings in 974.58s (0:16:14)`，原因是既有 MCP stdio
子进程超过 60 秒预算；单独协议套件又在另一条用例超时（`1 failed, 6 passed`）。
带完整测试隔离与定期堆栈采样的连续 12 次同接口探针通过
（`12 passed in 50.66s`），未捕获到卡顿堆栈，不能据此判定根因或声称已修复。

本轮在原协议测试保留周期性 faulthandler 采样，超时会输出子进程堆栈。原 60 秒
预算、资源与 CSP 断言全部保留；最终串行重跑上述九文件取得本节顶部的 142 项通过。
这是可追溯的偶发风险记录，不将一次重跑通过当成性能问题已解决。未终止其他用户
进程、调整系统防护或修改宿主配置来换取通过。

## 指导与审查

MWG 采用 GoogleChrome/modern-web-guidance 固定提交
`64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65` 的
`security/sanitize-untrusted-html.md` 与 `visual-design/dark-mode.md`。
规则映射为纯文本 DOM、根主题、宿主显式消息同步和原生控件色系；未采用富 HTML
或 Sanitizer，因为产品契约只展示文本；未引入 CDN、构建链或新的前端框架。

按 CONTRIBUTING 与本轮规格自行审查：改动集中在浏览器渲染/容错、宿主路径校验、
测试工装与接入文档；无存储 schema、工具签名、默认权限或发布判据变动。
因用户指定自行执行，未启用审查子代理。宿主矩阵已纠正 Hermes 插件不创建 MCP
配置的旧描述，并列明六宿主入口及 MCP Apps 的能力边界。

未覆盖 Safari、Firefox、实体手机、原生输入法和六宿主实际 GUI 登录态。
搜索结果嵌套结构通过合成宿主帧验证，不据此宣称已测外部模型服务或整条语义检索链。
没有运行新的全仓全量或完整发布门禁，本轮证据只覆盖上述指定套件及已安装运行时。
