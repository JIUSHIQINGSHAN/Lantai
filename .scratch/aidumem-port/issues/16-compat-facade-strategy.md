# 兼容门面策略：只搬家不改语义

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 的架构纪律：兼容门面模式（重构只搬家不改语义，旧 import 全绿），`api_server` 只做组装不做业务逻辑。

审计发现 remembrance 的 `api_server.py` 偏厚（直接 import 业务模块、组装 router），且存在死代码（`auth.py` 的 `PUBLIC_PATHS`/`is_public_path` 未使用）。

需要决定：

1. **门面铁律**：是否确立「只搬家不改语义，旧 import 全绿」作为重构约束？即所有 `from remembrance.xxx import yyy` 的旧路径在重构后仍然可用？
2. **api_server 瘦身边界**：`api_server.py` 只做什么？（创建 app、挂载 router、lifespan）哪些逻辑应该下沉到 `core/` 或各模块？
3. **auth.py 清理**：删除 `PUBLIC_PATHS`/`is_public_path` 死代码？`Depends` 未使用 import？
4. **是否引入 /improve-codebase-architecture**：用该技能扫描深化机会，HTML 报告后逐个 grill？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。建议作为地基票据优先解决。

## Answer

四项决议（grilling 2026-08-02 与用户确认）：

### 1. 门面铁律 → 确立

用户决定确立「只搬家不改语义，旧 import 全绿」作为重构约束。（我推荐不确立，用户推翻。）所有 `from remembrance.xxx import yyy` 旧路径在重构后必须仍然可用。后续重构只搬代码位置，不改语义。

ADR：`docs/adr/0001-facade-rule.md`

### 2. api_server 边界 → 三层定义

- **api_server.py**：只做组装（创建 app、lifespan、挂载 router、uvicorn 入口）——保持现状，52 行已够瘦
- **路由 handler**：只做 HTTP（解析请求 → 调 service → 返回响应）——当前 `routes_memory.py` 的 `add_memory()` 等 28 行业务逻辑需下沉
- **service 层**（下沉到各域模块）：业务逻辑——hash 去重、LLM 调用、DB 读写

具体 service 放在哪个模块，留给后续实施票据决定。

### 3. auth.py 清理 → 三个全删

删除 `PUBLIC_PATHS`、`is_public_path()`、未使用的 `Depends` import。零外部引用，删除不改语义。清理后 `auth.py` 只剩 `verify_api_key()` 一个函数。

### 4. /improve-codebase-architecture → 跳过

审计已覆盖同一地面，且当前代码一半即将被移植重构改写。实施中如发现新问题可随时临时调用。
