# 兰台改进：注入帧时间改按北京时间显示（巡检 2026-11-04 22:00）

> 本 PR 由 Maya（Sesame 的 AI 助手）自动巡检后生成，经 GitHub 小号 jiushiqingshand-dev 提交，未经人工逐行审阅，合并前请自行核对。

结论：兰台注入给 Hermes 的记忆日期原来按 UTC 渲染，北京时间 0 点到 8 点写下的记忆会显示成前一天，minute 粒度下每条都差 8 小时。本 PR 只改显示：存储和所有时间计算不动，注入帧默认换算到北京时间，可用新配置 `SHELL_HOOK_INJECT_TZ` 改回 UTC 或其他时区。

## 本轮巡检情况

- 走的分支：改进分支。`origin/master` 自上次巡检（`c6214c06`）以来没有新提交。
- 小号开着的 PR：#23（考功注入次数）、#24（隐私绊线 root 假红），共 2 个，未到 3 个的积压上限。
- 选题来源：前几轮报告列为 P1、一直没修的建议「注入时显示的日期改成按北京时间算」（第三轮方案审计 2026-10-31、巡检 2026-11-03 均列出）。
- 冲突检查：#23 改 `forgetting.py` / `tables.py` / `retrieval_log.py` / `migrations.py`，#24 改 `test_repo_privacy_tripwire.py`，与本 PR 的文件无交集。

## 问题

**位置**：`lantai/core/time.py` 的 `render_event_time`（master `c6214c06`，原第 87–90 行）。

库里所有时间按 UTC 存（`ensure_aware` 约定），这是对的；但 `render_event_time` 直接对 UTC 时间做 `strftime`，注入帧里给模型看的日期因此也是 UTC 日期。这个函数是注入时间的单一真源，两条链路都经过它：

- `scripts/shell_hook.py` 的 `_event_time_str`：Hermes 每轮 `pre_llm_call` 注入的「相关记忆」行，形如 `- [2026-11-04 | score 0.69] 正文`。
- `lantai/cognition/context.py` 的 `CognitiveContextBuilder.build`：认知上下文的 `record["event_time"]` 与 `to_prompt()` 行尾 `_(at: …)_`。

**复现**（master `c6214c06`，在仓库根目录用项目虚拟环境的 Python 运行）：

```python
from datetime import datetime, timezone, timedelta
from lantai.core.time import render_event_time
cst = timezone(timedelta(hours=8))
t = datetime(2026, 11, 4, 7, 30, tzinfo=cst)
print(render_event_time({"event_time": t}, "day"), render_event_time({"event_time": t}, "minute"))
```

| 北京时间写入 | 改前 day | 改前 minute | 改后 day | 改后 minute |
|---|---|---|---|---|
| 11-04 07:30 | 2026-11-03 | 2026-11-03 23:30 | 2026-11-04 | 2026-11-04 07:30 |
| 11-04 00:05 | 2026-11-03 | 2026-11-03 16:05 | 2026-11-04 | 2026-11-04 00:05 |
| 11-04 08:00 | 2026-11-04 | 2026-11-04 00:00 | 2026-11-04 | 2026-11-04 08:00 |
| 11-04 23:50 | 2026-11-04 | 2026-11-04 15:50 | 2026-11-04 | 2026-11-04 23:50 |

端到端（真 `build_context`，内存 SQLite）：一条北京时间 07:30 写入的「早上去医院复查了血常规」，改前注入行是 `- [2026-11-03 | score 0.69] 早上去医院复查了血常规`，Hermes 会把今天早上的事读成昨天；改后是 `- [2026-11-04 | score 0.69] …`。

顺带发现：`tests/test_inject_frame_time.py::test_shell_hook_inject_line_carries_event_time` 用 `et.strftime` 按 UTC 算期望日期。改成北京时间显示后，这个测试会在每天 UTC 16 点到 24 点（北京 0 点到 8 点）假红，所以一并改成按 +08:00 算期望值。

## 改法

1. `lantai/core/settings.py`：新增 `SHELL_HOOK_INJECT_TZ: str = "Asia/Shanghai"`。
2. `lantai/core/time.py`：
   - 新增 `resolve_display_tz(name)`（带 `lru_cache`）。认 IANA 名、`UTC`、`local`（本机系统时区）、固定偏移（`+08:00`、`UTC+8`、`-03:00`）。空值或写错值回落 `Asia/Shanghai` 并只告警一次，不抛异常（与粒度写错回落 day 的既有裁决一致）。
   - Windows 的 Python 不自带 IANA 时区库，`zoneinfo` 要靠 `tzdata` 包，而 `tzdata` 不是兰台的直接依赖。取不到时，`Asia/Shanghai` 等 1991 年后无夏令时的时区退回固定偏移（+08:00），结果与 `zoneinfo` 一致；你的 Windows 机器上不会因为缺包而退回 UTC。
   - `render_event_time(item, granularity="day", tz=None)`：新增 `tz` 参数，渲染前 `astimezone(resolve_display_tz(tz))`。只改显示，返回值仍是字符串，双轴优先级（event_time 先于 valid_from）与解析失败返回空串的行为不变。
3. `scripts/shell_hook.py`、`lantai/cognition/context.py`：两处调用传入 `settings.SHELL_HOOK_INJECT_TZ`。
4. 测试：
   - 新增 `tests/test_inject_frame_tz.py`（18 例）：北京早晨记忆默认显示当天；`UTC` 设置逐字恢复旧行为；ISO 串（Z 后缀）与 naive 输入同样换算；8 种写法参数化；写错名回落并告警；模拟无 tzdata 时 `Asia/Shanghai` 仍是 +08:00；`local` 与 `datetime.astimezone()` 一致（不用 skip，过得了 `test_skip_mask_inventory` 的零弃权门禁）；默认值；`shell_hook` 与认知上下文两条链路端到端。
   - `tests/test_inject_frame_time.py`：只钉双轴优先级和 ISO 解析的 3 处断言改为显式 `tz="UTC"`；minute 端到端期望从 14:30 改为 22:30（存 UTC 14:30，默认按北京时间显示）；上面说的假红断言改为按 +08:00 算。
   - `tests/test_settings_defaults_frozen.py`：登记新字段默认值。

没改 CHANGELOG，避免和 #23 冲突；合并时你可以顺手补一行。

## 验收

- **变异自证**：把 `render_event_time` 里的 `astimezone(...)` 去掉（等于退回改前行为），新测试 18 例里 14 例失败；剩下 4 例本来就该在 UTC 下通过（UTC 设置、`utc` 写法、默认值字段、`local` 在 UTC 机器上）。
- **全量 pytest（项目 .venv，Python 3.13）**：2902 passed、0 failed、5 skipped（5 个 skip 是仓库原有申报项）。上一轮巡检 master 为 2884 passed，差值 18 正好是新增用例。
- **Python 3.11**（venv311，与 CI 同版本）：相关 6 个测试文件 57 passed。
- `ruff check` 与 `ruff format --check`（lantai/ scripts/ tests/）：全部通过。
- 自己验收：在 Hermes 里问一句会召回今天早上 8 点前写入记忆的问题，看注入行日期是不是今天；想恢复旧行为，在 `.env` 写 `SHELL_HOOK_INJECT_TZ=UTC`。

## 没验证的

- 没在 Windows 上实跑；无 tzdata 的分支只用 mock 验证过。
- 没用真的 Hermes 跑完整一轮对话，端到端只到 `build_context` 的返回文本。
- Python 3.11 只跑了相关文件，没跑全量；GitHub CI 结果见 PR 页面。
- 只改了注入帧。MCP 工具返回的时间字段、摘要 digest 等其他输出没动，它们有各自的时区处理。

## 仍待做的旧建议（未在本 PR 处理）

- P1：入库时把「昨天」「上周」这类相对说法换成具体日期（约 1.5 到 2 天）。本 PR 修好显示日期后，这条的收益更明显。
- P1：两套衰减规则合成一套（约 1 天）。
- P2：探颐纠正记忆的入口补注入检查；精排分数和融合分数分开设阈值。
