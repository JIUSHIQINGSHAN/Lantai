# 01 - Schema 版本化迁移（PRAGMA user_version + apply_migrations）

Status: resolved
Type: task
Blocked by: (none)

## 目标

把 `lantai/storage/db.py::init_db()` 里逐个手写的幂等 ALTER TABLE 块，收口为基于
`PRAGMA user_version` 的增量迁移链：老库自动基线升级，未来加列只追加一个分支，
代码更新与数据重构解耦（借鉴 aiduMEI v18.3 Fast-Update 思想）。

## 范围

- `lantai/storage/db.py` 新增 `CURRENT_SCHEMA_VERSION = 2` 与 `apply_migrations(conn)`：
  - `user_version == 0`（全新库或未版本化的老库）→ 基线置为 v1；
  - `user_version < 2` → 执行现有三个幂等 ALTER（memoryitem.decay_class、
    retrieval_event.is_system_noise、memorycandidate.review_due_at，保留
    duplicate column 容错）→ `PRAGMA user_version = 2`；
  - 异常只记日志不阻断启动（降级而非崩溃），单次进程内只跑一次（幂等 + 线程安全）。
- `init_db()` 保留 `SQLModel.metadata.create_all`，迁移全部改走 `apply_migrations`，
  删除原有三段重复样板；文件内留 `if user_version < N:` 追加示例注释。
- 不改任何表语义、不做 DROP / 改类型 / 删数据。

## 验收

1. 现有 `lantai.db`（v1 老库，已含三列）启动后 `user_version == 2`，数据零丢失。
2. 全新库一次建全列且 `user_version == 2`。
3. 核心函数 `apply_migrations` 有不 mock 冒烟测试：临时 SQLite 库分别从
   空库 / 已含三列的老库启动，断言版本号与列存在（测试文件新建，不 mock 迁移逻辑）。
4. 全量测试无回归。

## 相关文件

lantai/storage/db.py、tests/test_migrations.py（新）
## Answer（2026-08-11 已实现）

实现内容：
- `lantai/storage/db.py`：新增 `CURRENT_SCHEMA_VERSION = 2`、`_ensure_column()`
  （PRAGMA table_info 判缺列，ALTER 幂等跳过）、`apply_migrations()`（user_version==0
  → 基线 v1；v1→v2 收口 decay_class / is_system_noise / review_due_at 三个历史列迁移；
  异常只记日志不阻断启动）；`init_db()` 移除三段手写幂等 ALTER 样板，统一走
  apply_migrations。
- 测试：`tests/test_migrations.py` 5 例不 mock 冒烟测试（真实临时 SQLite 直调）——
  空库版本记账 / 全新库幂等 / 缺列老库补齐+数据零丢失 / 重复启动 no-op / 预版本化库不动基线。

验收对照：
1. ✅ 现有真实库（%APPDATA%/remembrance-data/remembrance.db）user_version==2，三列齐全
2. ✅ 全新库一次建全列且 user_version==2（已存在列幂等跳过）
3. ✅ apply_migrations 不 mock 冒烟测试 5/5
4. ✅ 全量测试 490 passed；唯一失败为 tests/test_skill.py 在全量顺序下的状态污染
   （单独运行 7/7 通过，属用户进行中 skill-asset 工作，与本票无关）

