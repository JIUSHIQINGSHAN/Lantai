# 兰台月度工作汇报与下月计划（呈维护者）

> 汇报人：兰台 Agent（员工视角）｜日期：2026-09-25｜周期：2026-09-26 → 10-24
> 性质：全部为文档/票据/调研产出，未改产品代码；发布与 tag 未触碰（人工闸门）。
> 本汇报可整体推翻：任何一条不同意，指出即改。

## 一、结论先行（TL;DR）

1. **家底已清**：门禁真实全绿（今日实测 **1097 passed / 0 failed，294s**，含遗忘质量六指标 PASS 口径的同类基线）；下月要做的 **11 张票全部 ready-for-agent、零阻塞**——上期复核标记的三处矛盾（I4 两说/I3 拒写死锁/迁移落点两说）经本轮 grill-me 审计确认**均已在 spec 与 ADR-0048 定死唯一口径**，证据行号已落档票 05 Comments（spec §八）。
2. **下月计划已定**：四周清完 11 票（P0×4 + 更漏 P1-1 五票 A–E + 巩固过审 + 宿主矩阵），关键路径 = **05 更漏迁移 → {08,09,10} → 11 时间用例 ≥90%**，甘特图见 `charts/06_monthly_gantt_2026-10.png`。
3. **本月调研增量**：4 份一手来源短报（`docs/research/memory-landscape-2026-09/followups/2026-10/`），两条直接影响实施口径：**HaluMem 为 CC-BY-NC-ND 许可——数据不可搬用，票 03 只取方法论**；**Cursor 无命令钩子入口——票 06 的 Cursor 接入走 rules/AGENTS.md 兜底**。

## 二、上期盘点（已完成的事实，全部可复核）

| 项 | 证据 |
|---|---|
| 15 轮全景调研 + 路线图 v2 | `docs/research/memory-landscape-2026-09/`（synthesis/roadmap-v2/20 系统/32 论文） |
| 11 张执行票（spec + 01–11） | `.scratch/roadmap-v2-execution/`，全部 `Status: ready-for-agent` |
| ADR-0048 更漏双时间轴 + P1 Schema 规范 | `docs/adr/0048-*.md`、`docs/plans/p1-event-time-schema-spec.md`；「更漏」已登记 CONTEXT.md:73 |
| 笔削确定性测试 | `tests/test_bixiao_deterministic.py` 5 passed（撤回后 hybrid/FTS/真 Chroma 三面 0 命中） |
| E1 阶段化评测规范 | `docs/benchmarks/staged-eval-e1-spec.md`（六阶段指标+失败归因树） |
| 今日门禁基线 | `PYTHONPATH=. API_KEY= .venv/Scripts/python.exe -m pytest tests/ -q` → **1097 passed / 294.26s / 0 failed**（2026-09-25 实测；日志中一处 `llm down` 为测试内模拟故障路径，非失败） |

## 三、下月四周计划（与 spec §七一致）

| 周 | 票据 | 验收口径（DoD 摘要） |
|---|---|---|
| W1（9/26–10/2） | 01 可靠性收口；02 笔削验收收口；03/04 启动 | 基线冻结入档；撤回后禁用命中=0 三面实测 |
| W2（10/3–10/9） | **05 更漏 Schema+迁移 v20→v21（关键路径起点）**；07 巩固过审；03/04 收口 | 迁移可重放、U5 回填等价、I1/I2/I5 落锚、零回归 |
| W3（10/10–10/16） | 08 写入提取纪律；09 检索双视图；10 迟到更正闭环 | I4 落锚（NULL 永不硬排除）；U1/U2/U4 用例；`/search` 缺省逐字节不变 |
| W4（10/17–10/24） | 06 宿主矩阵；11 E2 时间用例+收口；月度复盘 | ≥3 宿主端到端冒烟（不满则如实报「2 落地+1 在途」，不凑数）；30+ 时间用例正确率 ≥90% |

节奏：每票合并前过全量门禁两命令（约 5.5 分钟/次，成本可忽略）；**每周五向您交一行周报**（进展/风险/下周）。06 排 W4 的理由：依赖 04 回执契约一次定型，且与 11 分属钩子/评测不同文件，可并行。

## 四、本月调研增量对实施的影响（4 份短报摘要）

1. **`01-sqlite-bitemporal-minimal.md`**：SQLite 官方文档确认 UPSERT/生成列/触发器/部分索引的能力边界；**未找到纯 SQLite 完整双时间轴的高星先例**（最接近：sequel_bitemporal 的 sqlite 适配、simonw/sqlite-history 触发器审计）——更漏方案自研的正当性与「最小要素清单（列/索引/视图/触发器）」获得支撑，票 05 无需改设计。
2. **`02-host-hook-integrations.md`**：Claude Code hooks 事件与配置格式齐全（stdin JSON/exit 2/决策 JSON）；**Cursor 无命令钩子入口**（负向断言，官方文档确认只有 rules/AGENTS.md）；Codex CLI 有 hooks.json 但阻塞语义官方页未载（未确认）。→ 票 06 首批宿主建议 **Claude Code + Codex 双钩子 + AGENTS.md 兜底**，Cursor 走 rules 注入。
3. **`03-operational-eval-halumem.md`**：HaluMem 全部指标公式已抽出（阶段级幻觉归因可直接映射 E1 六阶段）；**许可 CC-BY-NC-ND-4.0——数据集不可复用为回归集**，票 03/11 只取方法论与指标定义，自建样本。
4. **`04-landscape-delta-2026-09.md`**：mem0 v2.2.0（User Profiles）、OpenViking v0.4.21（MCP search 参数严格化，**破坏性变更**——票 06 接 MCP 时注意版本锚定）；其余无可见变化。不改变任何方向判断。

## 五、风险与依赖

- **工作区在途改动**：spec §二.8 记录的未提交文件（auth/settings/vector_store 等）归属未定，W1 开工前需先确认归宿，否则基线冻结口径含糊（本汇报不代决）。
- **11 票正确率 ≥90% 是硬口径**：若 W3 的 09/10 任一延期，W4 顺延并在周报如实标注，不降口径交差。
- **发布纪律不碰**：版本/tag 全程人工闸门；本月无发布计划。

## 六、请您拍板的 3 件事

1. **本月冻结新功能**（建议：是——11 票未清前不铺新面）；
2. **四周排序认可**（建议：如上表；若您希望宿主矩阵提前，则 06 与 09/10 并行、风险是 tables.py 之外的 review 带宽）；
3. **票 07 巩固过审是否要求影子对照先行**（建议：要——先影子跑一周对照数据再切换，符合「宁 miss 不脏写」）。

## 附：证据索引

- 票据：`.scratch/roadmap-v2-execution/spec.md`（§七执行序/§八审计）、`issues/01..11`
- 调研：`docs/research/memory-landscape-2026-09/followups/2026-10/01..04`
- 图表：`docs/research/memory-landscape-2026-09/charts/06_monthly_gantt_2026-10.png`
- 门禁：本文 §二基线行（命令可复制重放）
