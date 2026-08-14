# ADR-0019: 三态去重结构判别（校雠升级）——merge/update 不可由余弦单一阈值分离

**日期**: 2026-08-14
**状态**: Accepted
**决策者**: 大哥
**来源**: [prototype 实测报告](../../.scratch/dedup-threshold-calibration/report.md)、白皮书路线图「去重阈值实测校准」

## 背景

`lantai/gate/dedup.py` 的三态去重依赖两个余弦阈值：`DEDUP_MERGE_THRESHOLD=0.80`、
`DEDUP_UPDATE_THRESHOLD=0.65`。该默认值从未用中文样本实测过。

prototype 用真实 bge-m3（SiliconFlow）对 36 对中文记忆句（改写/同实体更新/不相关各 12 对）
实测余弦分布：

| 类 | min | mean | max | 当前阈值下误判 |
|---|---|---|---|---|
| 近义改写（应 merge） | 0.859 | 0.936 | 0.992 | 0/12 |
| 同实体更新（应 update） | 0.630 | 0.773 | 0.907 | **5/12 误判 merge，1/12 误判 insert** |
| 不相关（应 insert） | 0.362 | 0.503 | 0.757 | 2/12 误判 update |

**关键发现：单一余弦阈值无法分离 merge/update。** 改写与更新的相似度区间大面积重叠
（"周会时间改到周五" 0.86 vs "周会改期" 同为 0.86 量级；"手机号换成 139…" 0.891 被判 merge
→ 更新被静默吞掉，新值丢失）。判别信号在**结构**（哪些部分变了、值是否更新），不在相似度高低。
调参治标不治本（update 降到 0.60 能捞回个别全换词更新，但会把同主题异事实 hard case 拉进 update）。

## 决策

| 项 | 决策 |
|----|------|
| 两相位流程 | ① 余弦预筛（廉价，提取前）：提取路径 sim ≥ `DEDUP_PRESCREEN_MERGE`(0.95) 直合（真重复零 LLM）；sim < `DEDUP_UPDATE_THRESHOLD`(0.65) insert。② 中带 [0.65, 0.95) 提取后交**结构判别** |
| 结构判别 | `lantai/gate/relation.py::classify_relation` 纯函数：锚点（jieba 内容词，滤值/停用）+ 归一化值（日期/邮箱/域名/数字/地点表）。规则：新增值 & 锚点比 ≥ 0.6 → update；新增值 & 锚点比 < 0.3 → insert；无新增值 & 共享值非空或锚点比 ≥ 0.6 → merge；其余中带交 LLM judge |
| LLM 兜底 | 中带判不定 → `DEDUP_RELATION_*` prompt 判 merge/update/insert；**judge 缺席/异常/非法值一律 insert**（宁 miss 不脏写，`DEDUP_STRUCTURAL_LLM_ENABLED` 可关） |
| fastpath 路径 | 维持纯余弦（直书句型高频、真重复多），merge 阈值收紧 `DEDUP_MERGE_THRESHOLD` 0.80 → **0.90**（prototype：改写类 A.min=0.859 仍大部分覆盖；0.80 误吞更新风险真实存在） |
| verbatim 直存 | 不参与（sha256 幂等去重已覆盖） |
| 开关 | `DEDUP_STRUCTURAL_ENABLED`（默认 true；关掉时中带保守走 update 提案，不吞内容） |
| 命名 | 不新命名，归入「校雠」三态去重范畴（ADR-0013 名实相副） |
| 回归样本 | prototype 36 对升级为 `tests/test_dedup_relation.py` 正式回归集（规则层真实 jieba 不 mock；judge 桩仅替代外部 LLM） |

## 理由

- 实测证据驱动：0.80 对改写有效（12/12），但对更新类误吞 5/12 —— 默认值变更以数据为据；
- 规则优先、LLM 兜底与 ADR-0010 冲突消解层同构，复用既有确定性/降级设施；
- 值变更走 update 提案（有刹车），杜绝「merge 吞新值」——这是实测暴露的最大伤害；
- 中带 LLM 失败降级 insert：不吞、不误写、不积压（最轻副作用），既有校雠通道兜底。

## 影响

- `settings.py`：新增 `DEDUP_PRESCREEN_MERGE` / `DEDUP_STRUCTURAL_ENABLED` /
  `DEDUP_STRUCTURAL_LLM_ENABLED` / `DEDUP_ANCHOR_HIGH` / `DEDUP_ANCHOR_LOW`；
  `DEDUP_MERGE_THRESHOLD` 默认 0.80 → 0.90（语义改为 fastpath 路径阈值，参数注册表约束
  `merge > update + 0.10` 仍成立）。
- `memory_service.py`：`_apply_dedup` 拆为两相位（预筛 + 结构判类）；提取路径先提取后判类
  （真重复由预筛短路，不付提取费）。
- 既有 `test_dedup.py` 断言语义微调（0.9 中带改走结构判类）；`test_param_registry.py` /
  `test_param_v2.py` 默认值同步 0.9。
- 已知限制（诚实记录）：
  - 全换词更新（"我在腾讯工作"→"我跳槽去了字节跳动"，锚点零重合）规则无依据 → 交中带 LLM
    judge 裁决（可判 update），judge 缺席/失败降级 insert——新值作为新记忆可见、不吞不误写；
    实体级链接依赖未来实体提取赛道。
  - 锚点比为 `共享锚点 / 旧文本锚点`（非对称）：old ⊆ new 时比值 1.0（如
    "数据库使用SQLite" → "数据库使用SQLite并且迁移到了PostgreSQL" 判 merge，扩展事实被并入；
    PostgreSQL 非值类不入新增值）。此即 merge 语义固有取舍（并库不重写），后续若暴露误吞
    可加「新增实质词」信号或扩展值类，本轮不扩（宁 miss 不脏写优先，回归集未覆盖）。
  - 值正则 `\d{2,}` 会重复捕获日期/版本内的数字片段（"2026-08-14" 多 span、"v0.14.0" → "14"），
    36 对样本未见误判，仅健壮性注记。

## 相关

- [prototype 报告](../../.scratch/dedup-threshold-calibration/report.md)
- [ADR-0002](0002-zero-hardcoding.md) — 阈值入 settings
- [ADR-0010](0010-conflict-resolution-layer.md) — 规则优先 LLM 兜底同构
- [ADR-0013](0013-naming-system.md) — 校雠命名登记
- [测试纪律](../../AGENTS.md) — 36 对不 mock 冒烟测试
