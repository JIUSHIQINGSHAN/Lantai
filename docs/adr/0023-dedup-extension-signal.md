# ADR-0023: 校雠去重锚点比不对称修复——实质新词扩展信号

**日期**: 2026-08-15
**状态**: Accepted
**决策者**: 大哥
**来源**: v0.15 路线计划书 C1 项（ADR-0019 已知限制收口）

## 背景

ADR-0019 的锚点比 = `共享锚点 / 旧文本锚点`（非对称）：`old ⊆ new` 时比值恒为 1.0。
「数据库使用SQLite」→「数据库使用SQLite并且迁移到了PostgreSQL」——旧锚点全部保留、
新增实质词（迁移/PostgreSQL，非值类不入新增值），规则判 merge，扩展事实被并入吞掉
（merge 只 bump 不重写，新内容丢失）。

## 决策

| 项 | 决策 |
|----|------|
| 信号定义 | **实质新词信号**：`dropped = 旧锚点 - 新锚点`（旧词被替换的数量）；`extra = 新锚点 - 旧锚点`（新增实质词，已滤停用/值）。**扩展事实 ⟺ dropped 为空（旧词零丢失）且 len(extra) ≥ `DEDUP_EXTRA_ANCHOR_LIMIT`(2)** |
| 语义 | 无新增值分支中，扩展信号命中 → 判 **update 提案**（有刹车，不吞内容；人工可审可拒） |
| 区分改写 | 改写 = 替换（使用→用、开发→写，dropped 非空）——不触发扩展信号，维持 merge/中带语义（36 对回归不回归） |
| 值类 | **不扩技术名值类**（PostgreSQL/Go 等列表会漂移；宁 miss：错判最坏进提案由人工裁决） |
| settings | `DEDUP_EXTRA_ANCHOR_LIMIT: int = 2`（零硬编码 ADR-0002） |

## 理由

- 扩展与改写在结构上可区分：扩展保留全部旧锚点（内容真增量），改写替换旧锚点（表述重组）——dropped 空 + extra 足量是扩展的确定性特征；
- 扩展 → 提案而非直合：宁 miss 不脏写（不吞新内容，人工有刹车）；
- 36 对回归集全部保持（改写对 dropped 非空，不触发）。

## 影响

- `lantai/gate/relation.py::classify_relation`：无新增值分支加扩展信号检查（dropped 空 + extra ≥ 阈值 → update）。
- 测试：36 对回归全绿 + 新增扩展对（old⊆new 型）断言 update；`test_dedup.py` 中带 merge 用例改为纯改写对（扩展对走提案语义）。

## 相关

- [ADR-0019](0019-dedup-structural-relation.md) — 锚点比非对称已知限制
- [ADR-0002](0002-zero-hardcoding.md) — 阈值入 settings
