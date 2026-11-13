# 票 84：日报时区边界与置信桶诊断（2026-11-13）

基线 `1265591c`，实施令 `84-TZ-RESIDUE-EXEC`。本轮只改日报分桶、日窗委托和部署边界说明；保留票 83 的在途测试，不改线上服务、版本或宿主时区。

## 复现与单一假设

`_aggregate_reflection` 在模块导入时冻结 `DIGEST_CONF_BUCKETS`，并用顶桶的展示标签识别 `confidence=1.0`。假设：修改标签会先遇到配置快照不更新；即使消除快照，端点仍因展示标签改变而错误归入兜底桶。

使用真实内存 SQLite 建表，播种四条反思提案，置信度为 `0.9`、`1.0`、`-0.1`、`1.1`。通过 `collect_digest_stats` 和 `collect_calibration_stats` 读取真实聚合，并验证日报与校准报告渲染。默认顶桶和改名顶桶使用同样的数值边界。

## 隔离求证

1. 基线新增两例真实测试：默认桶通过、改名桶失败（1F/1P，exit 1）。实际输出仍含旧顶桶键，确认模块级快照问题。
2. 仅改为运行时读取 settings 后，改名桶仍失败（1F/1P，exit 1）：顶桶只收到一条，兜底桶收到三条；预期分别为二条、二条。确认 `1.0` 的归属受展示标签控制。
3. 日窗委托测试在 UTC/UTC+8、默认/显式日期四组输入下观察共享函数调用，同时保留真实计算。基线四例都因共享函数未被调用失败（4F，exit 1），没有导入或环境失败。

日志位于本地工单的 `watch/logs/`：`ticket84-buckets-red.log`、`ticket84-buckets-endpoint-red.log`、`ticket84-window-red.log`。这些草稿日志不进入版本库；可复核的测试与实现分别提交。

## 最小修复

- 运行时读取 `settings.DIGEST_CONF_BUCKETS`，最高数值上界所在桶闭合右端点；展示标签只作为输出键。保留默认五桶、“其他”兜底和现有 JSON/Markdown 结构。
- 保留 digest 私有日窗签名，只委托 `core.time.local_day_window_utc`；删除复制的计算逻辑。更新三处合法 date 序列化的精确行号登记，不扩大豁免。
- 保留 UTC 存储、宿主本地日报、UTC cron。文档明确默认只在 UTC+8 对应 23:55，不新增部署时区旋钮。

## 回归与既有项校准

修复后 digest 文件 13P；日窗及票 31/32/34/53 家族 43P；完整受影响家族（含 scheduler、qijuzhu、usage 和设置冻结锁）72P，均 exit 0。普通合并远端候选分支为 `ec1bbafd` 后，保留票 84/85/86 双方测试，扩展家族 96P、exit 0；后续文档边界锁 `8ebce157` 对应文件 11P、exit 0。全仓 Ruff check 与 format --check 通过（761 个 Python 文件），`git diff --check` 通过。离线遗忘质量门禁六项均 PASS、exit 0。

最终 `8ebce157` 的顺序覆盖率全量命令为 `PYTHONPATH=tests/support PYTHONIOENCODING=utf-8 API_KEY=test-key .venv/Scripts/python.exe -m pytest tests/ -q --cov=lantai --cov-report=term-missing -o addopts= -p no:cacheprovider -p real_home_tripwire`。结果 **3885 passed / 14 failed / 13 warnings，731.57s，exit 1**，全部失败在票 83 两个测试文件（12 个运行时契约失败、2 个源码哨兵失败），日志 `ticket84-ci-suite-sequential.log`。覆盖率总计 92%，digest_worker 为 100%；这些数字不能替代验收。

该顺序命令虽加载绊线插件，但**没有传入 `LANTAI_GATE_REPORT`**，插件按现有契约未武装，因此没有本次全量的宿主目录隔离证明。另一份 `ticket84-ci-tripwire.txt` 来自已中止的运行，只有 ARMED、没有 FINISH，不能与本次全量拼接使用。完整 `release_check.py` 同样 exit 1（11 条 PASS / 5 条 FAIL，含任务分支、票 83 未跟踪文件、既有 tag、套件收尾和绊线证据缺失），日志 `ticket84-merged-release-check.log`；不构成发布许可。

过程偏差留存：曾有后台超时、插件路径漏配及两棵本轮 pytest 重叠；这些运行均不计作有效全量证据，发现后仅停止本轮启动的测试进程。之后取得上述单棵顺序覆盖率全量的完整摘要，未删除、修改或跳过票 83 测试。实现已推送候选分支，工单保留 `claimed` 待整仓验收；不标 resolved、不改版本、不打 tag、不发布。

reflect 分钟位在工单原基线已经接线，属于票面误报；qijuzhu 本地日和 consolidation 调度设置分别由票 32、34 覆盖，本轮不重复实现。置信桶与远端票 86 属同族；本轮按明确实施令修本地仍存在的缺陷，合流时需保留两边测试和既有契约。

## 残留限制

报告是截至触发时刻的当日快照；日末触发后尾部不自动补回，跨午夜读取的“今日”不指前一天，已生成正文仍有缓存。固定 UTC cron 不自动随夏令时调整；共享日窗目前取运行时宿主偏移，跨夏令时日期的历史日界未作完整保证。以上限制已写入部署文档，本轮不宣称完整上一日报告或任意时区自动对齐。
