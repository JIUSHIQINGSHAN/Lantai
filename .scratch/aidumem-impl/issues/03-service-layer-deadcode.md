# 03 — Service 层 + 死代码清理

**What to build:** 将路由 handler 中的业务逻辑下沉到 service 函数，handler 只做 HTTP 解析/返回；删除 `auth.py` 中三个未使用的死代码函数；重构后所有旧 import 路径保持可用（门面铁律）。

**Blocked by:** 01 — P0 修复 + 零硬编码

**Status:** resolved

- [x] 路由 handler 只做 HTTP 解析/返回，业务逻辑在 service 函数
- [x] `auth.py` 死代码函数删除（`is_public_path` + `PUBLIC_PATHS`）
- [x] 所有旧 import 路径保持可用（门面铁律）
- [x] 43/43 关键测试通过（E2E + auth + settings + infra），零回归
