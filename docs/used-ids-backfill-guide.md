# Hermes 生成侧回填指南 — used_ids 弱标注通道

> 2026-08-08 | 通道已就绪（REST + MCP），本页告诉生成侧怎么用。
> 目标：Hermes 回答用户问题用了哪些记忆 → 写回 → dry-run 算 `weak_hit_rate`。

## 一、背景

`RetrievalEvent.used_ids` 记录"一次检索的 top-k 里，哪些真正被用进回答"。
Hermes 每次搜索记忆（MCP `search` / shell_hook 注入 / REST `/search`）都会记一条检索事件。
回答完成后，把**实际引用的记忆 id** 回填到该事件，评估管道就能算弱命中率。

**规则（铁律）**：
- 只回填"真正被用进回答/决策"的记忆；检索到但没用的一律不回填。
- 宁可少标，不虚标——污染弱标注比缺标注更伤评估。
- 回填失败零侵入，绝不影响主回答链路。

## 二、调用方式

### 方式 A：MCP（Hermes 主力通道）

1. 调用 `search {query, top_k}` → 响应含 `event_id`
2. 回答完成后，调用新工具：

```
tools/call: backfill {event_id, used_ids}
  event_id: search 返回的检索事件 id（字符串）
  used_ids: 实际用进回答的记忆 id 数组（如 ["mem_xxx", "mem_yyy"]）
```

返回 `{ok: true, event_id, used_count}`。

**示例**（JSON-RPC）：
```json
{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{
  "name":"backfill","arguments":{"event_id":"rev_xxx","used_ids":["mem_1","mem_2"]}}}
```

### 方式 B：REST API

```
POST /retrieval/backfill   （需 API Key 鉴权）
Body: {"event_id": "rev_xxx", "used_ids": ["mem_1", "mem_2"]}
```

### 方式 C：Shell Hook 注入路径

`pre_llm_call` 注入的响应现在带 `event_id`（与 `context` 并列）。
回答后若用了注入的记忆，用 MCP `backfill` 回填该 `event_id`。

## 三、拿不到 event_id 怎么办

- `search` 返回的 `event_id` 为 `null`（埋点失败/闸门拦截）→ 跳过回填，不 panic。
- 一次回答里多次搜索 → 回填**每次**搜索的 event_id 各自所用记忆。
- 记忆是从旧回答引用的、不是本次检索的 → 不回填（只回填本次事件）。

## 四、验证回填生效

```bash
# 查最近事件是否带 used_ids
C:/Users/Asus/Desktop/记忆/.venv-audit/Scripts/python.exe -c "
from remembrance.storage import db
from sqlmodel import select
from remembrance.models.tables import RetrievalEvent
with db.get_session() as s:
    for ev in s.exec(select(RetrievalEvent).order_by(RetrievalEvent.created_at.desc()).limit(5)):
        print(ev.id[:20], ev.query_text[:30], 'used=', ev.used_ids)
"
```

有 `used=[...]` 即回填成功。跑 dry-run 后 `weak_hit_rate` 不再是 `null`。

## 五、接入清单（Hermes）

- [ ] MCP 工具列表含 `search / add / feedback / backfill`（4 个）
- [ ] 回答用户问题前走 `search`，记下 `event_id`
- [ ] 回答用到的记忆 id 收集后调 `backfill`
- [ ] 自检：能查到 `used_ids` 非空的事件

## 六、一键自检

通道是否打通，一条命令验证（8 项检查）：

```bash
cd C:/Users/Asus/Desktop/记忆
.venv-audit/Scripts/python.exe scripts/verify_backfill.py
```

覆盖：backfill 工具注册 / search 带 event_id / backfill 处理器 /
表与字段 / 真实写读回填 / `_load_used_ids_map` 加载 / 生产回填率。

输出全 PASS = 通道就绪，只等 Hermes 生成侧回填。
输出 `生产回填状态` 的 0% 说明还没实际回填过——属预期，不是故障。
