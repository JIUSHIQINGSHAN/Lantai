# ADR-0056: episode credit 影子接线——默认关闭的检索加权信号

**日期**: 2026-10-25
**状态**: Accepted（影子模式，默认 `EPISODE_CREDIT_ENABLED=False`）
**决策者**: 维护者（授权自主开发；票据 `.scratch/v0.24-next-window-2026-10/issues/07-p1-7-tech-debt-i-j-d.md` 票 D，承十五天计划原票 D 与 2026-09 调研报告 episode 影子纪律）
**编号说明**: 实现注释初稿误写 ADR-0055（已被同日本票的 PBKDF2 备忘占用），本 ADR 定编 0056，代码注释已同步改指。

---

## 背景

episode 信用（`episode_record`/`episode_step`，经 `/evolve/episode/feedback` 落库）此前**只写不读**：credit 从不进入检索打分（routes_evolution.py 明注「察窗纪律」）。本 ADR 把它接成**检索加权信号**，但默认关闭——dogfooding 水源未解、有效样本不足，按 2026-09 报告纪律「不足则继续观察，不自动接分」。

## 决策

1. **开关**：`EPISODE_CREDIT_ENABLED: bool = False`（settings 默认关）；系数 `EPISODE_CREDIT_WEIGHT: float = 0.1`，经 `RetrievalParams._fail_closed` 越界/非法回默认 0.1。
2. **接线点**：`_hybrid_search_impl` 打分段。关闭时取数前短路，episode 表一次都不碰（零额外查询开销）。
3. **加权公式**：`score × (1 + EPISODE_CREDIT_WEIGHT × tanh(credit))`——tanh 有界压缩防极端 credit 主导排序；credit=0 时乘子恒 1。explain 输出 `episode_credit` 字段（关/开两态都在，关态恒 0.0）。
4. **逐位一致回归**：默认关（显式构造）时，episode 表有/无 credit 数据，检索输出（排序+分数）逐位一致；时钟冻结（`lantai.core.time.utcnow` patch，冻结点须在写库后——`valid_from` 晚于冻结点会被时间视图滤掉）消除衰减乘子的毫秒级时间漂移。
5. **变异门禁（2026-10-25 实跑，三变异全红）**：M1 翻默认 true → `test_default_off` 红；M2 改公式系数 0.5 → 公式精确校验红；M3 关闭态仍读 episode 表 → 零查询断言红。

## 开启条件（承 2026-09 报告 episode 影子纪律）

- 累计 ≥100 个来源完整、人工可判定的有效 episode（按任务类型分层）；
- 离线消融比较有/无 credit 加权的检索质量与安全退化，报告不确定性；
- 即使有收益，也先人工审核、可回滚小范围启用。

## 回滚

开关置 False 即回到现状（逐位一致回归钉死）；彻底拆除只需删打分段的 credit 块与两个设置项。

## 影响

- 检索主路径在关闭态行为逐字节不变；开启态属实验特性，无默认用户受影响。
- 不做清单遵守：水源解决前不做消融/调权（本 ADR 只接信号通路，不调生产参数）。
