# 七模块合流工程质量验收

日期：2026-11-25。执行者自行审查，未使用其他助手，未宣称独立双人审计。

## 验收范围与结论口径

固定主线基线为 `02fabb0107666cfbe07f25d188d34e73db2e545e`，覆盖校雠、底本、目次、
法门、拾遗、拟议、石室，以及樊篱、獬豸、守藏交界。主仓已有其他任务的未提交内容，
本轮不在主仓修改。基线与修复分别位于：

- 未修改检出：`D:/AI_Cache/lantai-audit-02fabb01`。
- 修复工作树：`D:/AI_Cache/lantai-quality-fix-02fabb01`。
- 修复分支：`fix/seven-module-quality-20261125`。

接力首轮完整全量为 4777 项通过、6 项跳过、2 项失败；该轮不得据此放行。
后续定点诊断与完整复跑分开记账，以本文末尾的最终验收证据为准；没有完整汇总的轮次
不算通过，也不据此确认间歇失败根因。
代码技术验收与发布仓库状态分别记账；独立分支与已有版本标签不能冒充可发布主线。
本轮不合并、不推送、不打标签或发布。

## 已复现的问题与修复

| 问题 | 失败证据 | 根因及最小处置 |
| --- | --- | --- |
| 拟议匿名读取私有记忆与提案 | 真实 SQLite + MCP 取得 Alice/tenant-a 的 `before_state.content` | service 将 None 原样交给内部全表引擎；在 service 按既有拾遗规则收敛为默认属主，保留合法属主与管理员语义。与合流前 `ad5df575` 对照为既有缺口。 |
| 围栏旁存在裸正文副本 | 五个工具的正文副本断言均先失败；逐字段复核又复现目录标签/主题、漂移说明、任务回显三项失败 | 七模块八个 MCP 工具按登记字段投影围栏；法门、目次已围栏正文不重复包裹。保留字段名与结构标识，出口契约变化写入 ADR-0071，不冒充完全兼容。 |
| 校雠扫描包含失效知识 | 四生命周期真实输入得到候选 4，预期 2 | SQL 同时限制 status 与 active/weakened 生命周期，已取代/退役行不再进配对或簇。 |
| 指定范围类型断点 | 一次性 mypy 初检 24 文件报 83 条错误 | SQLModel 字段使用 col 表达式，修正 Sequence/可变容器标注、变量复用及联合返回值收窄；未增加项目运行依赖。 |

新回归锁为 `tests/test_cognitive_quality_boundaries.py`，共 13 项。核心行为均经真实 SQLite 或实际
MCP `handle` 入口执行，未用 mock 跳过核心计算。沿用测试夹具隔离数据库与外部网络，
不把外部向量/LLM 替身说成真实在线模型质量测试。

闭合标签、大写空白标签及跨行残缺标签三组载荷均验证：正文仍位于唯一外层围栏内，
跨行普通段落保留，没有通过吞正文制造“安全”。新增 MCP 用例在调用结束前恢复循环守卫，
不把本轮指纹留给后续用例。

## 验证记录

| 检查 | 结果与口径 |
| --- | --- |
| 第一轮修复聚焦 | 31 项通过；拟议身份与五工具正文出口及相邻 API。 |
| 生命周期与目录/技能聚焦 | 65 项通过；含校雠、目次、法门真实引擎/API。 |
| 七模块与三防御交叉聚焦 | 最终版本 290 项通过，56.98 秒；含七模块引擎/API、石室并发锁、底本传承、拾遗分阶、拟议拓扑及围栏/注入/投毒裁决。 |
| 静态类型 | mypy 2.4.1，24 个指定文件零错误；目标 Python 3.11，实际解释器 Python 3.14.10。 |
| 公共 MCP 整文件类型复核 | 基线和修复树均有 25 条既有错误，逐条内容相同，仅行号平移；不能据此宣称全仓类型全绿。 |
| Ruff | `ruff check lantai/ tests/ scripts/` 与 `ruff format --check lantai/ tests/ scripts/` 通过，848 文件。 |
| 新库 Schema | 官方 `schema_drift_probe`：49 张模型表、55 张实表；缺表、列约束及索引漂移为空，幂等，版本 47，数据目录隔离。 |
| 老库 Schema | 官方回放 v0.25.0（30→47）、v0.65.0（35→47）；缺表、缺列、主键与索引漂移为空，NULL 方向差异仅在既有书面豁免内。 |
| 基线裸全量回归 | 已主动停止；最后约 42% 并有一个失败标记，没有最终失败堆栈，不计为通过。对应临时目录回收文件单独复跑 12 项通过，根因未定。 |
| 修复树完整发布口径（接力首轮） | 4777 通过、6 跳过、2 失败，2210 条警告尚未分类；pytest 耗时 4992.37 秒（1:23:12）。不带跳过旗标，含覆盖率、宿主目录绊线及 Node 契约。全门禁退出码 1，11/15 通过，4 条失败包含此全量失败及 3 条 Git 发布状态。 |
| 浏览器定点诊断 | 两个原始用例首轮通过；模块复跑在知识候选初始化等待 3 条记录时超时，完整堆栈已留存。短路径复跑两个完整模块 26 项通过，两个目标连续 30 轮通过；前 10 轮附加 CDP 网络观察，后 20 轮仅被动记录及失败快照，原断言和等待上限均未改。完整收集后定点运行 4785 收集、4782 未选、3 通过，含布局两档。根因未定，局部通过不覆盖全量失败。 |
| 接力第二轮完整复跑 | `full-suite-02.log` 于 15:06:58 停在 72%，进程已退出，没有全量汇总、XML 或最终退出码；不计为通过。 |
| 遗忘质量 | 基线与修复树分别通过六项确定性门槛；修复树陈旧命中率为 0，其余五项均为 1。接力首轮完整发布入口也独立通过同一项。 |

环境：`D:/AI_Cache/lantai-development/env-0691/Scripts/python.exe`，pytest 9.1.1、
Ruff 0.16.6、Chroma 1.5.9；Node 为 `D:/AI_Cache/node/node.exe`，24.22.1。
执行前清空 PYTHONPATH，测试使用假凭据和隔离目录，不迁移或重启实际服务。

完整发布口径入口（本轮实际命令）：

```powershell
$env:PYTHONPATH=''
$env:PYTHONIOENCODING='utf-8'
$env:LANTAI_TEST_NODE='D:/AI_Cache/node/node.exe'
& 'D:/AI_Cache/lantai-development/env-0691/Scripts/python.exe' -u scripts/release_check.py v0.69.1 --suite-timeout 10800
```

该入口真实执行 `pytest tests/ -q --cov=lantai --cov-report=term-missing -o addopts=
-p no:cacheprovider -p real_home_tripwire`，由门禁独占的 ASCII 临时目录承载覆盖率、
pytest 与绊线报告。`--suite-timeout 10800` 仅把全量等待上限延长为三小时，未跳过测试或
放宽断言。裸 `pytest tests/ -q` 只用于基线诊断，不替代放行口径。

接力首轮全量失败清单：

```text
tests/test_knowledge_review_ui_browser.py::test_knowledge_candidate_list_evidence_and_batch
tests/test_studio_ui.py::test_studio_layout_in_real_chromium[320-1]
```

门禁入口仅输出上述失败清单和计数，未留完整 pytest 堆栈。后续定点诊断在新的 ASCII
任务临时目录中运行原始断言，完整输出另存日志；不排查并发配置、不操作其他工作树。

类型复检入口：

```powershell
uv tool run --from mypy==2.4.1 mypy `
  --python-executable D:/AI_Cache/lantai-development/env-0691/Scripts/python.exe `
  --python-version 3.11 --ignore-missing-imports --follow-imports=silent `
  --check-untyped-defs --no-incremental `
  lantai/cognition/jiaochou.py lantai/cognition/diben.py lantai/cognition/muci.py `
  lantai/cognition/famen.py lantai/cognition/shiyi.py lantai/cognition/niyi.py `
  lantai/cognition/shishi.py lantai/llm/fence.py lantai/security/injection_guard.py `
  lantai/security/trust_tier.py lantai/services/jiaochou_service.py `
  lantai/services/diben_service.py lantai/services/muci_service.py `
  lantai/services/famen_service.py lantai/services/shiyi_service.py `
  lantai/services/niyi_service.py lantai/services/shishi_service.py `
  lantai/api/routes_jiaochou.py lantai/api/routes_diben.py lantai/api/routes_famen.py `
  lantai/api/routes_catalog.py lantai/api/routes_niyi.py lantai/api/routes_shishi.py `
  lantai/api/routes_shiyi.py
```

本次不是全仓严格类型验收：跳过三方缺失类型并静默跟随导入。公共 MCP 大模块不在
这 24 文件内；另对其执行相同参数的整文件检查，基线和修复树各有 25 条错误。
错误分布为循环守卫 Optional 状态 12 条、旧检索联合返回值 5 条、归因/参数字典
传递 6 条、可空返回值 1 条及标准输出 reconfigure 1 条。逐条对照没有新增错误，
本轮未扩展修复这些既有问题。MCP 改动的运行行为由真实传输契约与全量测试验证，
不能用运行测试替代类型检查的遗留结论。mypy 通过一次性工具环境运行，未增加项目依赖。

## 防御与兼容边界

- 樊篱是数据结构隔离手段，不是绝对防注入保证；本轮 MCP 投影不改写任意字典键或
  所有用户可控 ID。`DATA_FENCE_ENABLED=False` 的既有关闭语义保留。
- 獬豸仍只判定、不改写；外部命令内容的来源裁决由守藏负责，不能把“检测未命中”
  解释成“可信”。守藏缺文档/来源时未命中的路径有明确既有测试与 docstring，本轮
  未擅自改变这一历史兼容策略。
- 各引擎的缺租户与公共共享旧策略存在差异，未将其悄悄统一为新 ACL。当前 MCP
  `_principal_from_params` 只接收非空 user_id，未透传 tenant_id；本轮验收不证明
  不完整身份下的全系统强租户隔离。该范围限制不能由已有跨租户正反例替代。
- 石室保留既有不可变写锁和摘要验真规则；摘要不具独立密钥或外部锚点，不能证明
  拥有数据库任意写权限者无法同步伪造正文、摘要或回滚历史。
- MCP 文本表示有明确兼容成本：正文及派生说明、目次标签/主题、法门任务回显改为
  围栏文本。目次查询仍接收原始标签，自动转发客户端需要适配；REST 主题值与查询不变。
  底本 active_intent 和目次 topic 的既有 MCP 精确断言已同步为完整围栏值，未删除断言。

## 发布状态与证据

完整门禁已明确拦截发布 Git 状态：修复分支不是 master、工作区含待审修改，且
v0.69.1 标签已存在。本轮不改门禁、不删除既有标签，也不通过跳过旗标制造全绿。
技术结果以完整套件收尾为准；进入发布仍须合并、选定新版本并通过维护者发布闸门。

日志保存在本机：

- `D:/AI_Cache/lantai-pytest-audit-02fabb01-final.log`
- `.scratch/seven-module-quality-20261125/release-check-handoff.log`（接力首轮完整门禁，退出码 1）
- `.scratch/seven-module-quality-20261125/targeted-repro-01.log`（原始两项首轮定点通过）
- `.scratch/seven-module-quality-20261125/browser-modules-repro-1.log`（知识候选完整失败堆栈；另含本次临时路径过长造成的两项额外失败）
- `.scratch/seven-module-quality-20261125/browser-modules-diagnostic.log`（短路径两个完整浏览器模块 26 项通过）
- `.scratch/seven-module-quality-20261125/diagnostic-repro-1.log` 至 `diagnostic-repro-30.log`（两个目标连续 30 轮通过）
- `.scratch/seven-module-quality-20261125/full-collection-targeted.log`（完整收集后定点 3 项通过）
- `.scratch/seven-module-quality-20261125/full-suite-02.log`（第二轮停在 72%，没有最终汇总）
- `.scratch/qg-20261125-151700/`（第三轮由 Windows 系统托管进程独立启动；pytest 日志、XML、结构化结果及完整门禁分别落盘）
- `D:/AI_Cache/lantai-quality-release-verified-02fabb01.log`（旧入口停在测试启动处，没有完整收尾摘要）
- `D:/AI_Cache/lantai-quality-types-verified-02fabb01.log`（24 文件通过）
- `D:/AI_Cache/lantai-quality-cross-defenses-verified.log`（290 项通过）
- `D:/AI_Cache/lantai-forgetting-final-02fabb01.log`（修复树单独遗忘门禁通过）
- `D:/AI_Cache/lantai-quality-mcp-types-02fabb01.log`（基线 MCP 的 25 条错误）
- `D:/AI_Cache/lantai-audit-mcp-types-02fabb01.log`（修复树 MCP 的同 25 条错误；文件名不代表检出身份）

第一轮完整发布套件在逐字段复核发现派生文本缺口后主动中止，日志
`D:/AI_Cache/lantai-quality-release-02fabb01.log` 仅保留过程证据，不计为全量通过。
第二轮在同步目次 MCP 围栏契约精确断言后主动中止，日志
`D:/AI_Cache/lantai-quality-release-final-02fabb01.log` 同样不计为全量通过。
接力首轮以 `release-check-handoff.log` 的完整失败摘要为准，不合并多轮局部结果凑全量通过。

基线失败标记按当次收集顺序定位为
`tests/test_eval_temp_dir_cleanup.py::test_run_offline_eval_reclaims_its_own_temp_dir`；
该文件单独复跑 12 项通过。由于原跑测中止、未取到失败堆栈，“并发临时目录争用”
只能是待证假设，前期进度中把它说成定因的口径在此纠正。

早期一次 `legacy_parity_probe.py --help` 被当成目录参数，未进入任何快照回放建库循环；
随后已纠正为空锚点误判，正式探针真实完成两份老库回放。不把误调用记作有效验证，
也不把它描述为已写宿主数据库。

## 最终本地技术验收（独立后台）

完成时间：2026-11-25T15:47:38.781327+08:00。Windows 系统托管进程独立启动，启动工具退出后仍继续执行。
完整 pytest：4779 项通过、6 项跳过、0 失败、0 错误；退出码 0。
XML 共 4785 个用例，两个指定浏览器用例均真实通过，没有放宽断言、超时或跳过用例。
CI 同口径的覆盖率、宿主真实库绊线、Node 契约与其余技术门禁全部通过，共 12 项。
官方发布门禁退出码仍为 1，结果 12/15：仅独立分支、未提交工作区、已有 v0.69.1
标签三条发布仓库条件不满足，未跳过或改写这些判据；此结果不代表允许发布。

知识候选初始化的间歇超时根因仍未确认，320 布局失败未再次复现。完整复跑通过
不等于证明原始失败由环境或端口造成，本轮没有凭猜测修改业务代码或原浏览器测试。

证据目录：`.scratch/qg-20261125-151700/`。`pytest.log` 为完整输出，`pytest.xml` 为逐例结果，
`result.json` 保存真实退出码与逐项门禁，`tripwire.txt` 和 `coverage.dat` 在临时目录
清理前留存；`local-commit-result.json` 保存后续本地提交回执。本地提交不推送、不合并、
不打标签，不改分支或删除已有标签。
