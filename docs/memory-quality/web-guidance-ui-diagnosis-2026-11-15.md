# Web Guidance 控制台改进诊断记录

日期：2026-11-15。实施基线 `cf4804f0`；资料固定快照 `4dd968349e54ca45a05dd5c818fb035c9054b022`。范围和原始证据见 [评估报告](../research/modern-web-guidance-assessment.md)，执行票据在 `.scratch/modern-web-guidance-adoption/`。本记录只描述已观察到的现象，不代表发布门禁通过。

## 输入法提前提交

**复现**：新增 `tests/test_ui_ime_browser.py`，用真实 Chromium 加载产品 terminal/playground/vault 模块，调用真实 init 并分派组合态 Enter。外部 fetch 使用最小有效 JSON/SSE，产品 handler 和计算逻辑不替换。运行仓库 `.venv` 的 `python -m pytest tests/test_ui_ime_browser.py -q -p no:cacheprovider`，修复前 exit 1、1 failed，观察到 `/terminal/chat` 1 次、`/search` 2 次、`/probing/detect` 1 次；输入法事件本不应发业务请求。

**单一假设与验证**：三处 handler 仅检查 `key === 'Enter'`，未检查组合态。若此为根因，先排除 `event.isComposing` 和 `keyCode === 229`，同事件应变为零请求且文本不变。最小修复后该红测转绿；扩展为独立的 composing=true/keyCode=13、composing=false/keyCode=229、其他键、正常 Enter 与按钮场景，覆盖组合位与回退各自生效。

**修复**：三入口对组合/229 事件不动作，保留 IME 默认确认；只有普通 Enter 才 preventDefault 并调用既有动作。无 payload、服务端规则或浏览器支持政策变化。

**边界**：自动事件为合成 KeyboardEvent，不代表已验证 Windows/Safari 原生输入法。未用外部服务或真实用户记忆。

## 弹窗命名、焦点与结果

**现象与证据**：原 actionDialog/connectionDialog 的 Chrome accessibility tree role 均为 dialog、name 为空。当前实现通过 aria-labelledby 关联可见标题。新增产品整页 CDP 回归直接读取 AX tree，检查名称为“批准提案”和“连接设置”。

**焦点红测**：在具名实现上新增初始焦点断言，`python -m pytest tests/test_ui_dialog_browser.py -q -p no:cacheprovider` 曾 exit 1、2 failed：连接弹窗实际焦点是关闭按钮（无 id），操作弹窗是“×”，预期分别为 API Key 输入和明确取消。假设为缺少 autofocus；补原生 autofocus 后 2 passed。动态操作有字段时聚焦字段，无字段时取消，移除延时 focus，避免关闭后的定时器抢焦点。

**关闭结果**：ask/连接设置均捕获本轮 returnValue 后清空，再按捕获结果处理。实测 Escape、取消按钮、关闭按钮不发审批；确认一次只发一次请求；确认后再次 Escape 不增加请求；连接设置 save/clear 按当前选择更新隔离 storage，取消保留已存值。这里是防御性清理，原 Chrome 的实际 Escape 已能正确取消，不能声称它原来会误批准。

## 后续发现：必填理由弹窗取消失效

**红测**：在真实产品页面打开“拒绝”操作（理由 textarea required），保持空值，点击确认验证 required 阻断；再点击头部/底部取消，两个路径均保持 `dialog.open === true`，没有审批请求。命令：`python -m pytest tests/test_ui_dialog_browser.py -k required_reason -q -p no:cacheprovider`，修复前 exit 1。

**假设与修复**：取消按钮也以 `method=dialog` 表单提交，但没有 `formnovalidate`，因此被必填 constraint 阻止。只给取消 submitter（头部关闭、底部取消）加 `formnovalidate`；确认按钮仍执行原生必填校验。修复后同一红测通过，两个取消路径关闭且 POST 数为 0。

## 全量回归中与本范围无关的稳定失败

2026-11-16 全量命令 `python -m pytest tests/ -q --cov=lantai --cov-report=term-missing -p no:cacheprovider -o addopts=`，日志 `.scratch/modern-web-guidance-adoption/full-suite-final-20261116-v2.log`：**3929 passed / 16 failed / 13 warnings / 800.38s，exit 1**。

失败分解：14 项为当前工作树已知的票 83 评测隔离/硬编码残留测试；另两项为本次未触及区域，之后按准确节点各自单跑确认：

- `tests/test_canshang_engine_smoke.py::test_arbitrate_deep_conflict`：`old_policy` 与 `new_policy` 含明确前后 event_time，但仲裁结果仍为 `old_policy`。单跑 exit 1，2.66s。`lantai/cognition/canshang.py` 当前先按综合分排序、仅在胜者已晚于败者时写时效注记，因此晚近事件没有在败者已先胜出时取代分数排序。本次不改变该策略。
- `tests/test_gate_probe_encoding.py::TestStdioServicesPinStderr::test_mcp_configure_pins_all_three_streams`：单跑 exit 1，5.60s，输出 `utf-8 utf-8 gbk`，预期三项 `utf-8`。本次测试的环境 command 清空 `PYTHONPATH` 并设 UTF-8，但子进程 stderr 仍为控制台代码页；未修改 CLI/宿主编码。

这两项在目标路径仅改 `lantai/api/ui/{app,index,terminal,playground,vault}`、浏览器测试/夹具、文档和 workflow 环境变量；产品逻辑无交集。独立复现确认它们是现有故障，但没有在修改前为这两项跑全量基线，故不声称已证明全量失败相对 HEAD 新增或绝对基线数量。针对本次变更的46项定向套件通过，随后必填取消修复加一新例形成终版47项，再由下一终版全量日志验证。原始16F不是全属票83：准确为14票83 + 上述两项。

## 新增测试自身的故障与修正

- 把 `dialog.open=false` 当 close listener 已结算会产生测试竞态；UI 状态等待补至排队渲染事件后，再检查返回值和请求完成提示。没有放宽业务请求次数断言。
- 文字放大设置 `style.fontSize=32px` 后在同一求值脚本立即读 computed style，曾仍得16；改为等待浏览器实际报告32，再检查宽度。当前只验证长标题的200%文字放大，不声称整页浏览器zoom、OS文本缩放或所有控件均已验收。
- CDP Shift 修饰位应为8（1为Alt）；修正后整圈Tab仍可到BODY，未观察到背景控件被聚焦。最终自动测试验证从输入字段向前/向后的导航落在dialog内，以及Escape回到触发器；不称整圈Tab始终锁在页面内部，更不加自制焦点陷阱覆盖原生dialog。
- 有一轮 Chrome 启动20秒超时，发生于同时运行全量/浏览器和修改测试期间；后续串行定向轮成功。夹具结束时先 Browser.close，异常时清理自己启动的进程。未将启动失败改成skip。
- 安装 Playwright 时官方PyPI/CDN多次TLS失败；曾通过镜像安装到本地venv，但最终产品/开发依赖与锁文件没有保留它。正式回归复用已有Chrome和已锁定websockets/CDP；本机额外安装事实与项目依赖分开记录。
- wheel测试首次因PyPI setuptools下载TLS失败，不是UI资产缺失证据；全量最终轮用会话级 `UV_DEFAULT_INDEX` 指向可访问镜像，不写入项目配置，结果另记。

## 已完成的定向验证

冻结实现后运行：

```text
python -m pytest tests/test_ui_ime_browser.py tests/test_ui_dialog_browser.py tests/test_ui_interaction_browser.py tests/test_studio_ui.py tests/test_monitor_ui.py tests/test_release_check_ci_fidelity.py -q -p no:cacheprovider
```

最终结果：exit 0，**47 passed，24.24s**。新增12例浏览器行为加既有布局、监控、门禁对账；浏览器为本机Chrome157，DPR=1、viewport320/1440。Ruff check通过，`ruff format --check lantai/ tests/ scripts/` 显示769 files already formatted；`git diff --check` 通过；pyproject.toml/uv.lock相对HEAD无差异。

之后单独验证 `tests/test_wheel_ships_ui.py`：exit 0，1 passed，4.27s。`uv build --offline --wheel --out-dir .scratch/modern-web-guidance-adoption/wheel-check` 成功；解包 wheel 后 13 个 `lantai/api/ui/*` 静态资源齐全，包含 index.html、app.js、IME修复三模块。构建与验证产物仅在被忽略的 `.scratch` 临时目录。

最终冻结树全量命令 `python -m pytest tests/ -q --cov=lantai --cov-report=term-missing -p no:cacheprovider -o addopts=` 被运行约32%后外部停止，退出255，没有有效最终摘要；不得引用上一轮不同树的3929P/16F结果为最终结果。此前另一次完整覆盖率轮为3929 passed/16 failed/13 warnings/800.38s：14票83评测隔离/硬编码测试，另有 `test_canshang_engine_smoke.py::test_arbitrate_deep_conflict`（单跑稳定：实际 old_policy、预期 new_policy）与 `test_gate_probe_encoding.py::TestStdioServicesPinStderr::test_mcp_configure_pins_all_three_streams`（单跑稳定：实际 utf-8/utf-8/gbk，预期三路utf-8）。当前树再跑了票83两文件定向确认 14 failed/12 passed；它们与本次UI改动无调用路径。不能声称全量通过，也不能声称当前冻结树全量的精确失败总数。待有稳定完整测试会话后重跑全量，并另行处理三组既有失败。

遗忘质量 `python scripts/run_forgetting_quality.py --check` exit 0，门禁结果 PASS。其回答层 paraphrase hit rate 为0.3333（3/9），此项不属于该脚本硬门槛，照实保留。终树未运行release_check完整上传门禁；未提交/推送/打tag，也未执行远端GitHub Actions。
