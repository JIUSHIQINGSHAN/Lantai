# coalesce 缓冲键设计：缓冲分组键与三档策略

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 的 coalesce 按 `user+session+profile` 分组缓冲，三档策略（default/tech/intimate）控制不同敏感度。remembrance 当前无 session/profile 概念——只有 `namespace` 和 `lane`。

需要决定：

1. **缓冲键**：用 `user_id`？`user_id + lane`？`namespace`？是否需要引入 session 概念？
2. **三档策略**：default/tech/intimate 的分档是否照搬？remembrance 已有 lane 分轨（fact/rule/experience/preference/chat/general），是用 lane 替代三档还是两者并存？
3. **与 `/add` 的关系**：coalesce 是替换 `/add` 的同步提取路径，还是作为前置缓冲层（`/add` 仍可用）？还是开关切换？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

三项决议（grilling 2026-08-02 与用户确认）：

### 1. 缓冲键 → `user_id + lane`，不引入 session

```python
def _coalesce_key(user_id: str, lane: str = "general") -> str:
    return f"{user_id}::{lane}" if lane != "general" else user_id
```

- lane 替代 aiduMEM 的 profile 角色，防止不同语义类型的消息混批
- session 不引入（remembrance 单用户单实例，v13 联邦已划 Out of scope）
- namespace 不做 buffer 键（是数据隔离层，不是消息分组维度）

### 2. 三档策略 → 用 lane 替代，不照搬 default/tech/intimate

- 不引入 profile 概念，lane 已覆盖同一语义
- 在 `settings.py` 新增 `LANE_COALESCE_PROFILES`，按 lane 定义冲刷参数（window/idle/max_parts/max_chars/max_single）
- `general` lane = aiduMEM 的 `default` profile（默认参数）
- 不需要 `resolve_coalesce_profile` 的复杂解析逻辑——lane 直接从 `AddMemoryReq.lane` 传入
- 具体参数数值留给票据 03 校准

### 3. 与 `/add` 的关系 → 开关切换，一个入口

```
POST /add
  ├─ COALESCE_ENABLED=false（默认）→ 同步提取（当前行为不变）
  └─ COALESCE_ENABLED=true
      ├─ 短消息 + 非 fastpath → 入缓冲，返回 {"buffered": true, "key": "..."}
      ├─ 长消息（> max_single）→ 同步提取
      ├─ fastpath 命中 → 直写
      └─ ?buffer=false → 同步提取
```

- 一个入口，系统自动判断缓冲还是同步
- `COALESCE_ENABLED=false` 时 `/add` 行为完全不变（向后兼容）
- `/add` handler 变薄：解析请求 → 调 service → service 内部决定路径
- 运维端点 `/coalesce/status` 和 `/coalesce/flush` 可加，但写入入口只有一个
