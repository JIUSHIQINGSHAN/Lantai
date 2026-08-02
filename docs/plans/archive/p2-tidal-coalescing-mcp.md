> **已归档**：2026-08-02 起由 aiduMEM 移植流程取代，见 docs/plans/aidumem-port-skill-workflow.md

# P2 实施计划：潮波并忆 + MCP 服务端

## 当前状态

**P0 已完成：**
- `gate/prefilter.py` - 启发式相关性闸门
- `storage/fts.py` - FTS5 + Trigram 全文搜索
- `models/tables.py` - Chronos 双时间感知
- `retrieval/hybrid.py` - 集成时间过滤

**待实现 P2：**
1. 潮波并忆 (Tidal Coalescing) - 异步缓冲，减少 LLM 调用
2. MCP 服务端 - 接入 Agent

---

## P2-A：潮波并忆 (Tidal Coalescing)

### 设计参考

aiduMEM `ducky/speed/coalesce.py` 核心设计：

```
短消息 → 缓冲队列 → 按 user+session+profile 分组
         → 触发条件：idle(4s) / window(12s) / max_parts(8) / max_chars(2000)
         → 后台工作线程定期冲刷
         → 一次 LLM 调用处理多条合并消息
```

### 核心组件

#### 1. `workers/coalesce_worker.py` - 合并工作线程

```python
"""潮波并读工作线程 - 异步缓冲短消息，减少 LLM 调用"""
import threading
import time
import logging

from remembrance.core.settings import settings
from remembrance.llm.client import chat_json

logger = logging.getLogger("remembrance.coalesce")

_buffer: dict[str, dict] = {}
_buffer_lock = threading.Lock()

def enqueue(user_id: str, message: str, metadata: dict = None):
    """将短消息放入合并缓冲"""
    ...

def flush_due() -> list[dict]:
    """冲刷到期缓冲，返回待处理的批次"""
    ...

def _worker_loop():
    """后台工作线程：定期检查并冲刷到期缓冲"""
    ...

def start_worker():
    """启动后台工作线程"""
    ...
```

#### 2. `api/routes_coalesce.py` - 合并端点

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/coalesce/status")
def get_status():
    """获取当前缓冲状态"""
    ...

@router.post("/coalesce/flush")
def flush():
    """手动触发冲刷"""
    ...
```

#### 3. 集成到 `api_server.py`

```python
from remembrance.api import routes_coalesce
app.include_router(routes_coalesce.router)

from remembrance.workers.coalesce_worker import start_worker
start_worker()
```

#### 4. 集成到 `settings.py`

```python
COALESCE_ENABLED: bool = True
COALESCE_WINDOW_SEC: float = 12.0
COALESCE_IDLE_SEC: float = 4.0
COALESCE_MAX_PARTS: int = 8
COALESCE_MAX_CHARS: int = 2000
COALESCE_MAX_SINGLE_CHARS: int = 500
COALESCE_TICK_SEC: float = 0.5
```

### 实施步骤

1. 创建 `workers/coalesce_worker.py`
2. 创建 `api/routes_coalesce.py`
3. 集成到 `api_server.py` 和 `settings.py`
4. 集成到 `/add` 端点
5. 编写测试 `tests/test_coalesce.py`
6. 运行测试，验证

---

## P2-B：MCP 服务端

### 设计参考

aiduMEM `integrations/INTEGRATION_GUIDE.md` 方案：
- **Shell Hook**（`pre_llm_call`）方案
- 不是独立 MCP 服务器，而是通过 Shell Hook 注入上下文
- 适用于 Hermes Agent

### 实现方案

#### 1. `integrations/mem0-inject.sh` - Shell Hook 脚本

```bash
#!/bin/bash
# mem0-inject.sh — pre_llm_call Shell Hook
API_BASE="http://localhost:8767"
TIMEOUT=2
INPUT=$(cat)
USER_MSG=$(echo "$INPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('extra', {}).get('user_message', ''))

SEARCH_RESP=$(curl -sf -X POST "$API_BASE/search" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"$USER_MSG\", \"top_k\": 5}" \
  --max-time $TIMEOUT 2>/dev/null || echo '{"results": []}')

CONTEXT=$(echo "$SEARCH_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
if not results:
    print('')
else:
    lines = []
    for r in results[:5]:
        content = r.get('memory', {}).get('content', '')
        score = r.get('score', 0)
        lines.append(f'- [{score:.2f}] {content}')
    print('\n'.join(lines))
")

if [ -n "$CONTEXT" ]; then
    echo "{\"context\": \"## 📚 相关记忆\n$CONTEXT\"}"
else
    echo "{}"
fi
```

#### 2. `mcp_server.py` - 独立 MCP 服务器（可选）

```python
from mcp.server import Server

server = Server("remembrance")

@server.tool()
def search_memory(query: str, top_k: int = 5) -> str:
    """搜索记忆"""
    ...
```

---

## 关键文件路径

```
Remembrance-System/
├── api_server.py              # 集成 coalesce 路由 + 启动 worker
├── remembrance/
│   ├── api/
│   │   ├── routes_coalesce.py  # 新增：合并状态/手动 flush端点
│   │   └── routes_search.py   # 修改：集成闸门预过滤
│   ├── core/
│   │   └── settings.py       # 新增：COALESCE_* 配置
│   ├── gate/
│   │   └── prefilter.py      # 已完成：启发式闸门
│   ├── storage/
│   │   ├── fts.py           # 已完成：FTS5 全文搜索
│   │   └── vector_store.py   # 已完成：ChromaDB
│   ├── workers/
│   │   ├── coalesce_worker.py # 新增：潮波并读工作线程
│   │   ├── ingest_worker.py  # 已完成
│   │   └── evolve_worker.py  # 已完成
│   └── retrieval/
│       ├── hybrid.py         # 已完成：混合检索
│       └── reranker.py      # 已完成：Reranker
├── integrations/
│   ├── mem0-inject.sh      # 新增：Shell Hook 脚本
│   └── INTEGRATION_GUIDE.md  # 新增：集成指南
├── mcp_server.py            # 新增：独立 MCP 服务器（可选）
├── tests/
│   ├── test_coalesce.py    # 新增：合并测试
│   └── test_prefilter.py    # 已完成：闸门测试
└── pyproject.toml
```

---

## 设计决策

### 潮波并读触发条件

| 条件 | 值 | 说明 |
|------|:---|:---|
| idle | 4s | 消息间隔超过 4 秒 → 冲刷 |
| window | 12s | 首条消息超过 12 秒 → 冲刷 |
| max_parts | 8 | 单批最多合并 8 条 |
| max_chars | 2000 | 单批最多 2000 字符 |
| max_single | 500 | 超过 500 字符不合并 |

### 为什么用 Shell Hook 而不是独立 MCP 服务器？

- **Shell Hook**：零依赖，适用于 Hermes Agent，配置简单
- **MCP 服务器**：标准协议，适用于其他 Agent（Claude Desktop 等），需要额外依赖

两种方案都提供，由部署方选择。
