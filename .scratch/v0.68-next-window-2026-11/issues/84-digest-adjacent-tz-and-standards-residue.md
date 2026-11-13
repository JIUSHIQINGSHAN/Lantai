# 84-digest-adjacent-tz-and-standards-residue.md — P2：票 31 相邻残留——跨时区部署语义未裁决 + reflect minute 死配置 + 时面 Standards 轴三残点

- **Status**: resolved
- **Type**: task
- **Priority**: P2
- **Window**: v0.68-next-window-2026-11
- **Source**: 票 31（每日盘点时间窗差一天，案 A 落地轮）分诊与收口产出，HEAD `5822840f` 基线；与本票同面但出票 31 范围，逐一登记不静默

## 病灶与证据

1. **跨时区部署语义未裁决（票 31 Answer 剩余风险 1）**：日窗真源 `_local_day_window_utc`（`lantai/workers/digest_worker.py` ~`:183`）锚 `datetime.now().astimezone()` 系统本地日；案 A 触发点 UTC 15:55 只在 UTC+8 宿主与日界对齐。UTC 宿主（如 CI）触发距其本地日界 8 小时尾巴——`docs/daily-digest.md` 与 `settings.py` 注释均以 Asia/Shanghai 为隐含部署前提。需裁决：钉部署时区旋钮（如 `DEPLOY_TZ`，窗口/触发统一锚定）还是声明仅支持宿主时区部署（文档显式化）。
2. **`_REFLECT_CRON_MINUTE=1` 同样漏注册 minute=**（`lantai/core/scheduler.py` cron 注册面）：票 31 分诊实证 digest/reflect 两处注册都只传 `hour=`——reflect 的分钟位常量对注册面同为死配置（APScheduler minute 默认 0）。reflect 无日窗语义不受差一天病影响（票 31 不扩面），但死配置应与 digest 同款收口（补 `minute=` + 哨兵）。
3. **`qijuzhu.py:230-231` UTC 日界编年**：起居注按 UTC 日取「今日」，北京 08:00 前访问「今日」拿到前一 UTC 日的编年——与 digest 同型时面病（窗口锚定错位），用户可感（P2~P1 间，triage 定）。
4. **conf-bucket 标签串耦合（`lantai/workers/digest_worker.py:139`）**：置信度分桶用字符串字面量标签拼接（Primitive Obsession + ADR-0002 零硬编码精神），改文案/分桶边界需散改。
5. **scheduler 整点硬编码（`lantai/core/scheduler.py:361-368` consolidate 面）**：小时数硬编码不在 settings 旋钮面，与 ADR-0002 旋钮化纪律出入（triage 时核定影响面再定级）。
6. **`digest_worker._local_day_window_utc` 委托收敛（票 32 收口轮追加）**：票 32 在 `core/time.py` 新立共享真源 `local_day_window_utc`（与 digest 私有版逐行同语义），digest 私有版因其红测锁面（票 31）新鲜而暂不动——后续一行委托重构（`return local_day_window_utc(day)`），消除两份同语义实现。

## 修复方向

①先裁决项 1 的口径（钉旋钮 or 文档声明边界），后续项跟随该口径；②项 2 照抄票 31 digest 同款（minute= + AST 哨兵臂）；③④⑤逐项 triage 定级。

## Answer（2026-11-13，84-TZ-RESIDUE-EXEC）

- 基线 `1265591c`，分支 `chore/v0.68-inspection`；开工唯一未跟踪文件 `tests/test_ticket83_eval_isolation.py` 属票 83，未修改、未暂存。票 83 已提交源码哨兵 `1265591c` 尚红，必须与票 84 全量结果分开归因。
- 裁决：保留库存 UTC、报表宿主本地日、cron UTC 口径；不新增 DEPLOY_TZ、不改线上全局时区。默认 UTC 15:55 仅在 UTC+8 对应 23:55；UTC 宿主尾部为 8 小时 5 分钟。文档明确触发后尾部不自动回补、今日文件正文缓存、夏令时历史日界未完整保证，禁止据此宣称任意时区自动对齐或完整上一日报告。
- 子项 2 勘误：reflect 的 `minute=_REFLECT_CRON_MINUTE` 在票面基线 `5822840f` 就存在（`git show` 实核，`git blame` 指 `291587b7`）；当前 `scheduler.py:382-383` 已接线，不重复实现。
- 子项 3：票 32 `9984bae8` 已修，`qijuzhu.py:234-245` 默认本地日 + 共享 UTC 查询窗，现有真实数据库测试复验覆盖。
- 子项 5：票 34 `fcdd58d4` 已修，`scheduler.py:400-410` 走 settings 的 ENABLED/HOUR/MINUTE；`settings.py:435-441`（本轮注释新增行后随行平移）已有配置与冻结锁，不重复实现。
- 子项 4：按本实施令在本地当前树修复（远端已有票 86 相同领域工作，不能当成本地已具备）。调用时读取 `settings.DIGEST_CONF_BUCKETS`；顶端用最大数值 hi 的闭区间右端判定，标签仅展示，正常五桶和“其他”输出键兼容。真 SQLite 的 `collect_digest_stats` 与 `collect_calibration_stats` 统计及两种渲染覆盖 0.9/1.0/-0.1/1.1。
- 子项 6：digest 私有入口保留签名，仅委托 `core.time.local_day_window_utc`，删除重复实现；四组 UTC/UTC+8、默认/显式日期测试只替换时钟输入，调用见证继续执行真实共享计算。票 53 精确 date.isoformat 白名单按实际三处调用更新（322/600/606），无扩大豁免。

### 红绿与提交

- `4c6a7a9f` 红测：`PYTHONPATH= PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/test_digest.py::TestConfidenceBucketBoundaries -q`，exit 1、1F/1P。日志 `watch/logs/ticket84-buckets-red.log`：改名后输出旧标签（导入期快照）。动态读取配置后同测试仍 1F/1P，`watch/logs/ticket84-buckets-endpoint-red.log`：1.0 错落“其他”（3≠2、顶桶1≠2），证明目标端点缺陷。
- `d466b26d` 绿修：`pytest tests/test_digest.py -q`，exit 0、13P；`watch/logs/ticket84-buckets-green.log`。
- `3dbd90ae` 日窗红测：`pytest tests/test_ticket84_digest_local_day.py -q`，exit 1、4F，全部因共享调用见证为空，非导入/环境错误；`watch/logs/ticket84-window-red.log`。
- `b52b7556` 委托收敛：六文件家族 exit 0、43P；首次 42P/1F 为精确行号哨兵平移，按调用点更新后全绿，`watch/logs/ticket84-window-family.log`。
- `dd5efd42` 部署边界文档与 Unreleased 摘要（不 bump 版本）。
- 完整相关家族：`pytest tests/test_ticket84_digest_local_day.py tests/test_digest.py tests/test_digest_window_anchor.py tests/test_readside_time_semantics.py tests/test_scheduler_config_gaps.py tests/test_scheduler.py tests/test_qijuzhu_engine_smoke.py tests/test_qijuzhu_api_smoke.py tests/test_usage.py tests/test_datetime_serialization_forms.py tests/test_settings_defaults_frozen.py -q`，exit 0、72P；`watch/logs/ticket84-family.log`。全仓 Ruff check 全过，format --check 为 756 files already formatted；git diff --check 通过。
- 独立双轴审查（基线 `1265591c`，实现 `dd5efd42`）：Spec 0 发现，独立复验 72P；Standards 1 项流程缺口（development-workflow.md:52 要求诊断归档）已由 `77ed4fbb` 补齐，对单一诊断报告增加精确 .gitignore 白名单，不放开生成产物目录。可选观察 1 项为既存两种渲染的标签排序重复，非本轮新增且不影响正确性，本轮不扩面重构。诊断归档 `docs/memory-quality/digest-boundaries-diagnosis-2026-11-13.md`。
- 离线遗忘质量门禁：`PYTHONPATH= PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/run_forgetting_quality.py --check`，exit 0、PASS（六项 GATES 全 PASS），日志 `watch/logs/ticket84-forgetting.log`。
- 全量第一跑因后台 600000ms 时限被终止，没有 pytest 汇总行，作废不算通过；保留日志 `watch/logs/ticket84-full.log`。7200000ms 重跑 `PYTHONPATH= PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/ -q -o addopts=""` 得 **3810P/14F，13 warnings，775.54s，exit 1**；`watch/logs/ticket84-full-rerun.log`。失败全部属于票 83：未跟踪 isolation 测试 12F、已提交源码哨兵 2F。整仓保持红灯，未修改、跳过任何票 83 测试，不能声明全量通过。
- 推送前发现远端已分叉：本地 ahead 27/behind 34（`origin/chore/v0.68-inspection=f7f7cb2f`）。`git fetch origin chore/v0.68-inspection` 后普通 merge（无 rebase/强推），逐处解决 10 冲突，以 `ec1bbafd` 提交，父提交为 `77ed4fbb` / `f7f7cb2f`。保留双方票 84/85/86 测试及既有 data_home 单一真源，合入票 86 的 `DIGEST_CONF_FALLBACK_LABEL`；私有日窗委托与本票部署说明完整保留。去除自动合并产生的重复 data_home 定义。
- 合流后相关回归（上述 11 文件 + `tests/test_write_face_data_home.py` + `tests/test_ticket85_wheel_write_dirs_home_anchor.py`）**96P，exit 0**；全仓 Ruff check 通过，format --check **761 files already formatted**，无冲突标记，git diff --check 通过。前次重定向运行 exit 4 且日志为空，不计为测试结果；随后直接命令完整运行 96P 留工具回执。
- 合流后二次独立审查已完成：Standards 必修 0、可选既存渲染排序重复 1；Spec 0 发现，独立定向复验 23P。确认诊断归档、单个 data_home、双方测试、三个 date 序列化精确登记和日窗委托完整。
- `8ebce157` 增加时区文档回归锁（仅测试），`pytest tests/test_write_face_data_home.py -q` 得 11P、exit 0；输出目录、宿主本地日、UTC 调度与夏令时/尾部说明均锁定。
- 完整 `PYTHONPATH= PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/release_check.py` 已结束，**exit 1，11 条 PASS / 5 条 FAIL**；`watch/logs/ticket84-merged-release-check.log`。失败包括任务分支不是 master、保留的票 83 未跟踪文件、既有 v0.66.0 tag，以及覆盖率套件无可解析汇总、绊线报告缺失。wheel、lint、版本一致性、命名空间、迁移、保留对称性、老库与离线遗忘检查通过；不得将整条发布门禁标绿。
- 最终单棵顺序覆盖率全量：`PYTHONPATH=tests/support PYTHONIOENCODING=utf-8 API_KEY=test-key .venv/Scripts/python.exe -m pytest tests/ -q --cov=lantai --cov-report=term-missing -o addopts= -p no:cacheprovider -p real_home_tripwire`，**3885P/14F，13 warnings，731.57s，exit 1**；`watch/logs/ticket84-ci-suite-sequential.log`。14F 全属票 83 两文件（isolation 12F + 源码哨兵2F），没有票84失败；总覆盖率92%，digest_worker100%。
- 证据纠正：上述顺序命令只加载插件，未设置 `LANTAI_GATE_REPORT`，故绊线未武装；不能声称取得本次全量的宿主目录隔离证明。`watch/logs/ticket84-ci-tripwire.txt` 来自另一条被中止的诊断运行，只有 ARMED、没有 FINISH，不能与最终摘要拼接。此前插件路径漏配、后台超时、两棵本轮 pytest 重叠与 exit255 均不计作通过证据；发现后停止本轮启动的进程，再取得单棵完整摘要。没有修改或跳过票83来换绿。
- 推送：普通 `git push origin chore/v0.68-inspection` 成功，远端由 `f7f7cb2f` 快进到 `8ebce157`；终账诊断回执单独提交 `a17dec48` 后再次普通推送成功。最终本地 HEAD、origin 跟踪引用与 `git ls-remote` 三方均为 `a17dec48c0bedfc57f224e5ca7accee06c1a5268`。推送的是候选代码，不是发布；整仓红灯和绊线证据缺失仍阻塞验收，工单保持 `claimed`，不标 resolved。最终跟踪工作树干净，唯一未跟踪文件仍为票 83 测试，未修改或暂存。
- 保护文件 `tests/test_ticket83_eval_isolation.py` 内容指纹 `1c65b8db6042f539b396e7da86574e9008785eaa`，始终未改、未纳入 index；最终验证进程已全部退出。当前符号：分桶 `digest_worker.py:131-143`、日窗委托 `:191-193`、共享真源 `core/time.py:66`、部署边界 `docs/daily-digest.md:28`、consolidation settings `:441-443`。

## 关联

- 票 31（案 A 落地轮剩余风险如实登记）；ADR-0002（旋钮化）；ADR-0027（观察窗口径）；票 32/34 已覆盖子项；票 86 同族置信桶修复（远端待同步）。
