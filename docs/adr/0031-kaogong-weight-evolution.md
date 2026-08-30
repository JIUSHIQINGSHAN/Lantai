# ADR-0031: 考功——长程反馈驱动的记忆价值演化与升降评定

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 路线图 v0.16.4 价值演化（EvolveMem 闭环）；基于长程反馈实现知识生命周期自动升降级。

---

## 背景

兰台记忆系统此前支持记录反馈（`MemoryUsageFeedback`）与单步调整 `importance`，但缺乏全局性的**知识生命周期自动化评定机制**：
- 经常被成功采纳、高价值的高频记忆（如大哥核心设备偏好、关键工程规范），仍受默认遗忘衰减影响；
- 误记或引起幻觉的低质记忆（已被标记未采纳或高幻觉风险），仍持续占据检索候选空间。

---

## 决策

确立**「考功」（Kaogong）** 记忆价值演化评定体系：

### 核心评定规则

| 评定等级 | 判定条件 | 处理动作 |
|---|---|---|
| **上考（晋升长期）** | `use_count >= 3` 且 `helpful_ratio >= 0.8` | `tier = "longterm"`, `decay_class = "semantic"`（免疫时间衰减，恒定保真） |
| **下考（降权淘汰）** | `use_count >= 3` 且 `helpful_ratio <= 0.2` | `importance` 降至 0.1，标记建议废弃或生成 deprecate 提案 |
| **中考（样本不足/平庸）** | `use_count < 3` 或采纳率在中带 | 保持原有 tier 与 decay_class（宁 miss 不脏写，不轻率定性） |

### 接口与工具

- REST：`POST /evolution/kaogong`（执行考功评定周期）、`GET /evolution/kaogong/report`（获取考功报告）
- MCP：`kaogong_eval`

---

## 理由

1. **名实相副**：「考功」出自唐代吏部考功司，掌管官吏功过品级考评，与记忆价值根据实战表现升降级名实完全相符。
2. **生命周期闭环**：使记忆系统具备自我提纯与演化能力，高价值知识沉淀为恒定资产，劣质知识自动淡出。

---

## 影响

- 服务：新增 `lantai/services/kaogong_service.py`。
- 路由与工具：更新 `lantai/api/routes_evolution.py`，新增 MCP `kaogong_eval`。
- 测试：`tests/test_kaogong.py`（真实 SQLite 数据库不 mock 冒烟）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 考功命名登记
- [ADR-0026](0026-candidate-admission-triage.md) — 沙汰候选信噪分离
- [CONTEXT.md](../../CONTEXT.md) — 考功词汇定义
