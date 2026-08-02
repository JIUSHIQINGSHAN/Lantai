# ADR-0003: Coalesce 缓冲键与分档策略

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 02](../../.scratch/aidumem-port/issues/02-coalesce-buffer-key.md)

## 背景

aiduMEM 的 coalesce 按 `user+session+profile` 分组缓冲，三档策略（default/tech/intimate）控制不同敏感度的冲刷参数。remembrance 无 session/profile 概念，但有 `namespace`（数据隔离）和 `lane`（语义分轨）。

## 决策

### 1. 缓冲键：`user_id + lane`

```python
def _coalesce_key(user_id: str, lane: str = "general") -> str:
    return f"{user_id}::{lane}" if lane != "general" else user_id
```

- lane 替代 aiduMEM 的 profile 角色
- 不引入 session（单用户单实例）
- namespace 不做 buffer 键

### 2. 分档策略：lane 替代三档 profile

- 不照搬 default/tech/intimate
- 新增 `settings.LANE_COALESCE_PROFILES`，按 lane 定义冲刷参数
- lane 直接从 `AddMemoryReq.lane` 传入，不需要 profile 解析逻辑

### 3. 与 `/add` 的关系：开关切换，一个入口

- `COALESCE_ENABLED=false`（默认）→ 同步提取（当前行为不变）
- `COALESCE_ENABLED=true` → 系统自动判断缓冲还是同步
- 不新增写入端点，运维端点 `/coalesce/status` `/coalesce/flush` 可加

## 影响

- `/add` handler 变薄（符合 ADR-0001 service 层约定）
- `COALESCE_ENABLED=false` 时旧调用方零感知（门面铁律不破坏）
- 票据 03（冲刷参数校准）解锁——可基于 lane 体系做原型

## 相关

- [ADR-0001](0001-facade-rule.md) — 门面铁律（/add 路径不变，内部分流）
- [票据 02](../../.scratch/aidumem-port/issues/02-coalesce-buffer-key.md) — 完整决议
- [票据 03](../../.scratch/aidumem-port/issues/03-coalesce-flush-params.md) — 冲刷参数校准（现已解锁）
