# 01 - 记忆 Wiki：digest 升级为持续维护的知识库（LLM-Wiki）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory MemoryKnowledge `engines/wiki/ingest-v2/`（增量维护 +
`overview.md` 综述 + `[[wikilink]]` 下钻）的窄版落点：把一次性盘点（digest）升级为
持续维护的记忆 Wiki——场景/技能 → 页面，index 先看目录再钻取，overview 全局综述。

## 范围

- `lantai/services/wiki_service.py`：`slugify` / `render_scene_page` / `render_skill_page`
  / `render_wiki_index` / `render_overview_fallback`（纯函数）+ `run_wiki_update_once`
  （幂等增量维护：场景/技能 → 页面 → index → overview，过期页自动清理）+ `read_wiki_page`
- `lantai/core/settings.py`：`WIKI_ENABLED` / `WIKI_OUTPUT_DIR` / `WIKI_OVERVIEW_LLM` /
  `WIKI_PAGE_MAX_MEMBERS` / `WIKI_MEMBER_CHARS` / `WIKI_RELATED_TOP`
- `lantai/services/mem_command.py`：`mem_sync` 挂接 wiki 刷新（scene + digest + wiki 三件套）
- `scripts/run_wiki.py`：CLI（--no-llm / --json）
- `scripts/mcp_server.py`：MCP `wiki_read` 工具（工具数 20 → 21，wikilink 下钻取页）
- 文档：ADR-0017、MCP 客户端矩阵、借鉴报告落地顺序 7、CONTEXT 词汇表、CHANGELOG

## 验收

1. 纯函数（slugify / 三类渲染 / 索引 / 兜底综述）有不 mock 冒烟测试
2. 真实 SQLite + 真实 tmp_path：run_wiki_update_once 落页面 + index + overview
3. 场景删除 → 对应页自动清理（增量维护收敛）；LLM 综述失败 → 确定性兜底
4. mem_sync 返回含 wiki 结果；MCP wiki_read 取页、缺参 -32602
5. test_mcp 工具数 20 → 21

## 相关文件

lantai/services/wiki_service.py、lantai/core/settings.py、lantai/services/mem_command.py、
scripts/run_wiki.py、scripts/mcp_server.py、tests/test_wiki.py、tests/test_mcp.py、
tests/test_mem_command.py、docs/adr/0017-wiki.md、docs/mcp-client-matrix.md、
docs/research/tencentdb-agent-memory-borrow.md

## Answer（2026-08-11 已实现，test_wiki.py 11/11 + 全量回归绿）

实现内容：
- 页面 = 场景页（frontmatter: type/title/description/timestamp/member_count + 成员记忆
  + 相关场景 [[wikilink]]，按质心余弦取 top N）+ 技能页（Skill 资产步骤）。
- index.md 按类型分组（场景 → 技能）稳定排序；overview.md LLM 综述优先
  （chat_json，外部依赖可 mock），失败/关闭 → 确定性综述兜底（含 [[wikilink]]）。
- 增量维护：run_wiki_update_once 幂等重写 + 过期页清理（仅限 pages 目录内）；
  mem_sync = scene 增量聚类 + digest 重算 + wiki 刷新三件套；CLI scripts/run_wiki.py。
- MCP `wiki_read(slug)`：slug 白名单 + 目录内路径校验，下钻取页。

验收对照：
1. ✅ 纯函数冒烟 6 例（不 mock）
2. ✅ 真实 SQLite + tmp_path 集成（页面/index/overview 落盘断言）
3. ✅ 过期页清理 + LLM 失败兜底测试覆盖
4. ✅ mem_sync wiki 键 + MCP wiki_read（-32602/-32603）
5. ✅ 工具数 21