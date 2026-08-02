# ADR-0001: 兼容门面铁律

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 16](../../.scratch/aidumem-port/issues/16-compat-facade-strategy.md)

## 背景

aiduMEM 在多次迁移中形成了兼容门面模式——重构只搬家不改语义，旧 import 路径全绿。remembrance 即将进行大规模移植重构（A–F 六组特性），需要确定重构约束。

## 决策

确立「只搬家不改语义，旧 import 全绿」作为重构铁律：

- 所有 `from remembrance.xxx import yyy` 旧路径在重构后必须仍然可用
- 重构只搬代码位置，不改语义
- 路由 handler 业务逻辑下沉到 service 层时，`from remembrance.api.routes_xxx import router` 路径不变

## 理由

用户决定确立（grilling 中我推荐不确立，用户推翻）。理由：

- 移植过程中频繁搬家，门面铁律防止断链
- 单用户项目虽无外部消费者，但内部 import 路径的稳定性降低重构风险
- 与 aiduMEM 的架构纪律一致

## 影响

- 每次重构需要验证旧 import 路径仍然可用
- 如果需要改路径，必须提供兼容别名
- 增加少量维护成本，换取重构安全性

## 相关

- [票据 16](../../.scratch/aidumem-port/issues/16-compat-facade-strategy.md) — 完整决议
