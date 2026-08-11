# 01 - Shell Hook 召回预算 + 记忆工具指南

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory auto-recall，给 Shell Hook 注入加两道闸：
单条记忆/总字符双预算，超预算截断或丢弃；有命中时附「记忆使用指南」，
防止大记忆撑爆上下文、Agent 陷入无限搜索。

## 范围

- `scripts/shell_hook.py`：码点安全截断、总预算分配、工具指南三个纯函数 + `build_context` 接入
- `lantai/core/settings.py`：`SHELL_HOOK_MAX_CHARS_PER_MEMORY` / `SHELL_HOOK_MAX_TOTAL_CHARS` / `SHELL_HOOK_TOOLS_GUIDE`
- 契约更新 ADR-0006；调研记录 `docs/research/tencentdb-agent-memory-borrow.md`

## 验收

1. 单条记忆超上限 → 按码点截断（emoji 不炸）并附后缀提示
2. 总预算不足 → 丢弃剩余行（evidence 与注入行同步收窄）
3. 有命中 → context 末尾附工具指南（search/add、每轮 ≤3 次检索）
4. 无命中/异常 → 仍返回 {} 零侵入降级
5. 核心纯函数有不 mock 的冒烟测试

## 相关文件

scripts/shell_hook.py、lantai/core/settings.py、docs/adr/0006-shell-hook-contract.md、
tests/test_shell_hook.py、docs/research/tencentdb-agent-memory-borrow.md

## Answer（2026-08-11 已实现，test_shell_hook.py 17/17 全绿）

实现内容：
- `_truncate_codepoints(text, max_chars, suffix)`：按码点截断，超长附
  「…（已截断；可用记忆工具查看详情）」，不会切开 emoji 代理对。
- `_apply_recall_budget(lines, max_total_chars)`：按序装入（含行间换行），
  超预算丢弃剩余，返回 (budgeted, dropped_count)。
- `_build_tools_guide(truncated)`：指南含「已截断可深挖 / 每轮 ≤3 次检索 /
  新事实用 add 回写」三段，truncated 时附截断提示。
- `build_context`：单条截断（配置替代硬编码 [:200]）→ 总预算 → evidence 与
  注入行同源截断 → 有命中附指南（`SHELL_HOOK_TOOLS_GUIDE` 可关）。
- 测试：纯函数冒烟 5 例 + 集成 1 例（真实内存 SQLite + FakeStore），
  核心计算不 mock。

验收对照：
1. ✅ 码点截断 + 后缀（emoji 测试覆盖）
2. ✅ 总预算丢弃（dropped 计数 + evidence 同步收窄）
3. ✅ 指南附于注入末尾（含 search/add、3 次上限）
4. ✅ 无命中/异常仍 {}（既有测试回归通过）
5. ✅ 纯函数不 mock 冒烟测试

备注：`test_main_timeout_returns_empty_json` 在本机 chromadb 冷启动慢时
偶发耗时超 3s 断言（与本次改动无关，重跑即绿）。