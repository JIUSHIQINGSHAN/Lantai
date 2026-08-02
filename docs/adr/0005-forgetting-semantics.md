# ADR-0005: 遗忘语义

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 08](../../.scratch/aidumem-port/issues/08-forgetting-semantics.md)

## 决策

- 记忆永不物理删除，只降权
- decay_score 降到极低时自动转 archived
- archived 记忆不参与检索（`WHERE status='active'`）
- 保持现有 `_lane_strength` 指数衰减不变
- 不做 GC（单用户 SQLite 存储量不是瓶颈）

## 理由

- 单用户场景 10 万条记忆 < 100MB，存储成本可忽略
- 只降权不删 = 记忆可恢复，误删风险为零
- archived 完全排除比降权保留更简单——搜索不需要处理极低分记忆的噪声

## 相关

- [票据 08](../../.scratch/aidumem-port/issues/08-forgetting-semantics.md)
