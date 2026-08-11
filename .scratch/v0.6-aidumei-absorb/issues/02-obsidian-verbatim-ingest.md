# 02 - Obsidian 双链 + 原文直存通道（verbatim tier）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 aiduMEI v18.0 Raw Drawer（零 LLM 原文直存）与 v18.3 Obsidian 双链联动：
给 lantai 增加「原文直存」旁路通道，并打通 Obsidian 笔记同步——笔记原文存
RawDocument（可 FTS/向量检索），`[[Wikilink]]` 双链词沉淀为实体与边（MemoryEdge）。
与现有对话提炼链（dialogue ingest）互不干扰：原文直存不经过 LLM 提炼。

## 范围

- 原文直存 service：`lantai/ingestion/verbatim.py::store_verbatim(content, source_type, source_id, meta)`：
  - 写 RawDocument（content_hash 去重幂等，重复返回既有 id）+ FTS 索引（复用
    `lantai/storage/fts.py`）；向量化可选走异步 embedding 队列，失败不阻断落库。
- REST：`POST /verbatim`（挂 protected_routers，沿用 X-API-Key 鉴权）；
  `GET /verbatim/search`（FTS+向量专用检索，默认不进混合召回）。
- Obsidian 同步：`lantai/api/routes_obsidian.py`（或并入 verbatim 路由）：
  - `POST /obsidian/sync`（title/content/tags/metadata）→ `store_verbatim(source_type='obsidian')`；
  - `extract_wikilinks()`：正则解析 `[[页面]]` / `[[页面|别名]]`，忽略 `[[#锚点]]`；
  - 双链词与笔记标题沉淀为实体，并用 MemoryEdge（relation='links'，若加列走 Ticket 01
    迁移）互连；重复推送靠 content_hash + 实体名去重。
- MCP：`add_verbatim`（scripts/mcp_server.py），与现有 8 工具并列。
- 配置零硬编码：settings 加 `VERBATIM_IN_RECALL`（默认 false，true 时 verbatim 原文
  参与混合召回）。

## 验收

1. `store_verbatim` 相同 content 幂等：第二次返回既有 id，不产生重复记忆。
2. verbatim 原文 FTS 可检索；默认不进混合召回，`GET /verbatim/search` 可查。
3. `POST /obsidian/sync` 解析双链并沉淀实体边；重复推送不产生重复记忆。
4. 核心函数（store_verbatim / extract_wikilinks）有不 mock 冒烟测试（真实内存
   SQLite 直调；仅外部 LLM/embedding 可 mock）。
5. 全量测试无回归。

## 相关文件

lantai/ingestion/verbatim.py（新）、lantai/api/routes_obsidian.py（新）、
lantai/storage/fts.py、lantai/models/tables.py（如加列）、lantai/core/settings.py、
scripts/mcp_server.py、tests/test_verbatim_obsidian.py（新）

## Answer（2026-08-11 已实现）

实现内容：
- `lantai/services/obsidian_service.py`（新）：`extract_wikilinks()` 纯函数
  （[[页面]] / [[页面|别名]] → 页面名，[[#锚点]] 忽略，保序去重）；
  `sync_obsidian_note()`——笔记原文复用 P0-1 `add_raw_memory`（零 LLM 直存，
  content_hash 幂等），双链词与笔记标题沉淀为实体
  （memory_type="entity"，不建 FTS/向量索引，图谱节点不参与召回），
  笔记↔实体建 MemoryEdge(relation="links")，重复推送实体名/边幂等。
- `lantai/api/routes_obsidian.py`（新）：`POST /obsidian/sync` +
  `GET /verbatim/search`（memory_types=["verbatim"] 专用检索）。
- `lantai/core/settings.py`：`VERBATIM_IN_RECALL: bool = False`——
  verbatim 默认不进混合召回（hybrid.py 向量路径与 FTS 兜底路径两处过滤），
  置 true 后参与。
- MCP：`obsidian_sync` 工具（16→19 共 19 个，含用户并发新增 3 个）。
- 测试：`tests/test_verbatim_obsidian.py` 5 例不 mock 冒烟测试（实体/边落库、
  幂等、默认召回排除+专用通道可查、REST 接线）；test_raw_memory 的 FTS 兜底
  测试改为显式 memory_types=["verbatim"]；test_mcp 计数同步。

与票据原文的偏差（设计兑现时决定）：
- verbatim 通道复用 P0-1 `add_raw_memory`（写 MemoryItem 而非 RawDocument），
  不另起 `store_verbatim`，避免双通道重复实现。
- MCP 工具为 `obsidian_sync` 而非 `add_verbatim`（raw_add 已覆盖原文直存）。
- 实体用 memory_type="entity" 的 MemoryItem 承载（无独立实体表），靠不建索引
  隔离出召回；边关系沿用 MemoryEdge。

验收对照：
1. ✅ content_hash 幂等（重复推送返回同一 note_id，dedup=True）
2. ✅ verbatim FTS 可检索；默认排除混合召回，GET /verbatim/search 可查
3. ✅ POST /obsidian/sync 双链实体/边沉淀，重复推送不重复
4. ✅ extract_wikilinks / sync_obsidian_note 不 mock 冒烟测试 5/5
5. ✅ 全量 559 passed 无回归
