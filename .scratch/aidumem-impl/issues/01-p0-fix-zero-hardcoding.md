# 01 — P0 修复 + 零硬编码

**What to build:** 修复 `settings.DEFAULT_LANE` 缺失导致的 P0 崩溃；删除 `VECTOR_DIMENSION` 让 ChromaDB 自推断维度；添加 `REMEMBRANCE_HOME` 环境变量和 `__file__` 自解析仓库根；`validate()` 改为只 warn 不 crash。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] `settings.DEFAULT_LANE = "general"` 存在且有默认值，`promoter.py` 不再报 `AttributeError`
- [x] `settings.VECTOR_DIMENSION` 被删除，ChromaDB 自推断维度
- [x] 路径通过 `REMEMBRANCE_HOME` 环境变量或 `__file__` 自解析，不硬编码绝对路径
- [x] `settings.validate_config()` 只 warn 不 crash，缺少可选配置不阻止启动
- [x] 14 个新测试全绿，63/69 全量测试通过（6 个预存 bug 非 T01 引入）
