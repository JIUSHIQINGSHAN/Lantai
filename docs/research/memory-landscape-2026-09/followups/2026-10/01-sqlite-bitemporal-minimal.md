# 01 · SQLite 双时间轴（bi-temporal）最小实现先例

调研日期：2026-09-25（所有来源均为当日检索）。目的：为兰台「可回滚、单文件、无图库」的记忆版本化寻找纯 SQLite 先例与能力边界。

## 一、SQLite 官方能力边界（全部一手：sqlite.org）

- **UPSERT**：3.24.0（2018-06-04）引入；3.35.0 起支持多个 ON CONFLICT 子句。冲突目标可带 `WHERE expr`，**可精确匹配 partial unique index**；仅对唯一约束生效，「UPSERT does not intervene for failed NOT NULL, CHECK, or foreign key constraints」；不适用于 virtual tables。来源：https://sqlite.org/lang_upsert.html（检索 2026-09-25）
- **生成列**：分 VIRTUAL（默认，读时计算）与 STORED（写时计算、占空间）；STORED 列不能用 ALTER TABLE ADD 添加；表达式「may only use scalar deterministic functions」（禁子查询/聚合/窗口函数）；不能作 PRIMARY KEY；需 SQLite ≥ 3.31.0（2020-01-22）。来源：https://sqlite.org/gencol.html（检索 2026-09-25）
- **触发器**：事件为 DELETE/INSERT/UPDATE（可 `UPDATE OF 列`）；「BEFORE and AFTER triggers work only on ordinary tables. INSTEAD OF triggers work only on views」（即**可对视图建 INSTEAD OF 写入触发器**）；仅支持 FOR EACH ROW；RAISE(ABORT/FAIL/IGNORE) 可中止；文档建议优先 AFTER 触发器。来源：https://sqlite.org/lang_createtrigger.html（检索 2026-09-25）
- **部分索引**：`CREATE INDEX ... WHERE expr` 只索引子集；WHERE 须确定性，禁子查询/跨表引用/非确定函数/绑定参数；查询要命中它须含与索引 WHERE **逐字匹配**的谓词（W⇒X 两条简单规则）；需 SQLite ≥ 3.8.0。来源：https://sqlite.org/partialindex.html（检索 2026-09-25）
- **CREATE VIEW**：受支持（官方语句表含 create-view-stmt / create-trigger-stmt 等）；官方 SQL 语句列表中**不存在**系统版本化表/PERIOD 相关语句——SQLite 无原生 SQL:2011 时间版本。来源：https://sqlite.org/lang.html（检索 2026-09-25）

## 二、SQL:2011 系统版本表语义（二手摘要，标准原文未直接核验）

- 维基百科（二手，2025-11-20 版）：SQL:2011 以 `PERIOD FOR SYSTEM_TIME` + `WITH SYSTEM VERSIONING` 定义系统版本表；以 `PERIOD FOR` 定义应用时间（valid time）表；查询用 `AS OF SYSTEM TIME` / `VERSIONS BETWEEN`；应用时间与系统版本**可组合为 bitemporal**；`WITHOUT OVERLAPS` 做时间主键。来源：https://en.wikipedia.org/wiki/SQL:2011（检索 2026-09-25）
- Microsoft Learn（SQL Server 对 SQL:2011 的厂商实现，二手）：`GENERATED ALWAYS AS ROW START/END` + `PERIOD FOR SYSTEM_TIME` + `SYSTEM_VERSIONING = ON`；UPDATE/DELETE 自动把旧值移入历史表并闭合 `ValidTo`；`AS OF t` 的行有效性判定为 **`ValidFrom <= t AND ValidTo > t`**。来源：https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal-tables?view=sql-server-ver17（检索 2026-09-25）
- 规范论文指针：Kulkarni & Michels, "Temporal features in SQL:2011", SIGMOD Record 41(3), 2012, DOI 10.1145/2380776.2380786。ACM DL 页面访问被拒（HTTP 403），全文未核验，仅作指针。

## 三、GitHub 真实实现先例

- **TalentBox/sequel_bitemporal**（Ruby/Sequel，双时间轴，CI 含 SQLite）：版本表四时间列 `valid_from / valid_to`（应用时间）+ `created_at / expired_at`（事务时间）；as-of 判定代码为 `(created_at <= t) & (valid_from <= n) & (valid_to > n)`，过期行判定含 `valid_from != valid_to`。实测源码：https://github.com/TalentBox/sequel_bitemporal/blob/master/lib/sequel/plugins/bitemporal.rb；CI 矩阵含 `TEST_ADAPTER: sqlite`：https://github.com/TalentBox/sequel_bitemporal/blob/master/.github/workflows/ci.yml（均检索 2026-09-25）
- **simonw/sqlite-history**（纯 SQLite，触发器维护历史）：由触发器把每张表的变更写入 `_people_history`，列为 `_rowid/_version/_updated/_mask`（_version 单调递增、_updated 毫秒时间戳、_mask 位掩码标记变更列、-1 表示删除）——**单时间轴（事务时间）审计**先例。来源 README：https://github.com/simonw/sqlite-history（检索 2026-09-25）
- **evalapply.org 实现文章**（非 GitHub，附完整代码的博客）：append-only 事实表，用 UUIDv7 的 STORED 生成列派生 `txn_time`/`valid_time`，窗口函数做 as-of-now 视图，自称「half of a bitemporal database」；不依赖触发器/存储过程，防护靠应用层。来源：https://www.evalapply.org/posts/poor-mans-time-oriented-data-system/index.html（检索 2026-09-25）

## 四、兰台最小要素清单（bi-temporal，单文件、无图库；逐项依据见上）

1. **版本表**：业务列 + `valid_from/valid_to`（应用时间）+ `txn_from/txn_to`（事务时间，闭合行用哨兵值如 '9999-12-31'）。列语义依据：sequel_bitemporal 源码 + SQL:2011 摘要（本文件二、三节）。
2. **CHECK 约束**：`CHECK (valid_from < valid_to)`（普通表约束，lang.html 语句范围内）。
3. **当前行部分索引**：`CREATE INDEX ... ON mem(entity, valid_from) WHERE txn_to = '9999-12-31'`——加速「现在」视角扫描。依据：partialindex.html。
4. **as-of 视图**：`CREATE VIEW mem_as_of AS SELECT ... WHERE :t BETWEEN valid_from AND valid_to AND :now BETWEEN txn_from AND txn_to`；行有效性判定 `start <= t AND end > t` 依 MS Learn 的 AS OF 语义（本文件二节）。
5. **写入触发器**：BEFORE UPDATE/DELETE 在版本表上把旧行 `txn_to` 闭合为新事务时间并追加新版本（或对统一视图建 INSTEAD OF 触发器，让上层对视图直接 UPDATE）。依据：lang_createtrigger.html（INSTEAD OF 仅视图可用；官方建议优先 AFTER，闭合旧行用 BEFORE 或应用层均可，但需明确选择）。
6. **UPSERT 恢复/重开**：`INSERT ... ON CONFLICT(实体列) WHERE txn_to='9999-12-31' DO UPDATE ...`——conflict target 的 WHERE 可锚定「当前行」部分唯一索引。依据：lang_upsert.html。
7. **生成列（可选）**：从 UUIDv7/时间戳 ID 派生 `txn_time` 生成列（STORED）供排序与索引。依据：gencol.html（≥3.31.0）。
8. **回滚性**：append-only 版本表 + 单文件 SQLite，恢复 = 恢复文件本身（兰台自身约束；上述要素均不引入外部存储）。

## 五、已知空白（如实标注）

- 未找到「SQLite + 完整双时间轴 + as-of 双轴查询」的高星独立仓库；sequel_bitemporal 是最接近的**在 SQLite 上跑通**的先例，evalapply 文章是纯 SQLite 但只做了「半个」bi-temporal（作者自述）。
- SQLite 对 `UPDATE OF` 触发器中未识别列名会静默忽略（legacy 行为），列名拼写错误不会报错。来源：lang_createtrigger.html。
