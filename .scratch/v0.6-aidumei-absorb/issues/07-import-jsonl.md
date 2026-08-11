# 07 - 冷启动导入（历史会话 JSONL 批量导入，保留原始时间戳）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory 冷启动导入（导入仓库/文档/历史 Session，保留原始
时间戳）与 direction 调研「批量导入历史会话 JSONL」：给兰台加一次性的历史数据
导入通道——JSONL 逐行原文直存（verbatim 零 LLM，复用 Raw Drawer 语义），
created_at/updated_at 保留原始时间戳，重复导入靠内容 sha256 幂等。

## 范围

- `lantai/services/import_service.py`（新）：
  - `parse_import_lines(text)` 纯函数——逐行解析 JSON 对象，合法行归一化为
    {content, created_at, updated_at, lane, tags}，非法行返回 {line, reason}；
    空行跳过；content 缺失/为空、时间戳不可解析、tags 非字符串数组 → 记非法
    不静默修正（宁 miss 不脏写）。
  - `_store_imported_memory(s, ...)`——verbatim 直存：sha256 幂等去重（重复返回
    duplicate），created_at/updated_at 用原始时间戳，embedding/向量索引失败
    不阻断落库（FTS 仍可检索）。
  - `import_memory_lines(lines)` / `run_jsonl_import(text)`——汇总报告
    {imported, duplicates, invalid, errors}，按文件顺序落库。
- REST：`POST /import/jsonl`（受保护，body {text}，空文本 422）——
  `lantai/api/routes_import.py`（新），注册进 api_server protected_routers。
- CLI：`scripts/import_jsonl.py <file>`——读本地文件 POST 到服务，输出报告；
  支持 --host/--port/--key。
- 文档：spec.md 票据清单、CHANGELOG、CONTEXT 词汇表（冷启动导入）、
  tencentdb-agent-memory-borrow.md 表行更新。

## 验收

1. `parse_import_lines` 不 mock 冒烟：合法/非法/空行/时间戳/字段类型，行号正确。
2. 真实临时 SQLite 直调：导入计数、重复导入幂等（duplicate 不重复写）、
   created_at/updated_at 与原值一致（含时区）、lane/tags 生效、非法行不计入导入。
3. REST 200 报告结构；空文本 422。
4. 全量测试无回归。

## 相关文件

lantai/services/import_service.py（新）、lantai/api/routes_import.py（新）、
scripts/import_jsonl.py（新）、tests/test_import_jsonl.py（新）、
lantai/models/schemas.py、api_server.py、lantai/api/__init__.py、
docs/research/tencentdb-agent-memory-borrow.md


## Answer（2026-08-11 已实现，test_import_jsonl.py 6/6 + 全量无回归）

实现内容：
- `import_service.parse_import_lines(text)` 纯函数：逐行解析 JSON 对象，合法行归一化
  {content, created_at, updated_at, lane, tags}；非法行记 {line, reason}（JSON 解析失败/
  content 缺失或为空/时间戳不可解析/lane 非字符串/tags 非字符串数组），空行跳过——
  宁 miss 不脏写，不静默修正。
- `_store_imported_memory`：verbatim 直存（memory_type=verbatim、decay_class=semantic），
  内容 sha256 幂等去重（重复返回 duplicate），created_at/updated_at 保留原始时间戳
  （updated_at 缺省取 created_at），embedding/向量索引失败不阻断（FTS 仍可检索）。
- `run_jsonl_import(text)`：解析 + 按文件顺序落库 + 汇总 {ok, imported, duplicates,
  invalid, errors}；单行落库异常记 errors 不中断。
- REST `POST /import/jsonl`（受保护，ImportJsonlReq.text min_length=1 → 空文本 422）；
  `scripts/import_jsonl.py` CLI（--host/--port/--key，输出报告）。
- 测试：解析 3 例（纯函数不 mock，行号正确）+ 落库 2 例（真实临时 SQLite + FTS，
  仅 mock embed/向量库：时间戳保留、幂等去重、非法行不导入）+ 端点 1 例（200/422）。

验收对照：
1. ✅ parse_import_lines 不 mock 冒烟（合法/非法/空行/时间戳/字段类型/行号）
2. ✅ 真实 SQLite 直调：计数、重复幂等、created_at/updated_at 保留、lane/tags 生效、
   非法行不计入
3. ✅ REST 200 报告结构；空文本 422
4. ✅ 全量测试无回归
