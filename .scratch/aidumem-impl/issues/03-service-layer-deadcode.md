# 03 — Service 层 + 死代码清理

**What to build:** 将路由 handler 中的业务逻辑下沉到 service 函数，handler 只做 HTTP 解析/返回；删除 `auth.py` 中三个未使用的死代码函数；重构后所有旧 import 路径保持可用（门面铁律）。

**Blocked by:** 01 — P0 修复 + 零硬编码

**Status:** ready-for-agent

- [ ] 路由 handler 只做 HTTP 解析/返回，业务逻辑在 service 函数
- [ ] `auth.py` 三个死代码函数删除
- [ ] 所有旧 import 路径保持可用（门面铁律）
- [ ] 所有现有测试通过
