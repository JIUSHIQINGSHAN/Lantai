# 零硬编码：环境变量清单与密钥注入

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 有 32 个环境变量、零硬编码。审计发现 remembrance 存在多个硬编码：

- `settings.VECTOR_DIMENSION = 1024`（与默认 `EMBED_MODEL` 维度不一致）
- `.env` 含真实 API Key（安全隐患）
- 路径硬编码（如 `./.chromadb`、`./remembrance.db`）
- `promoter.py` 引用不存在的 `settings.DEFAULT_LANE`

需要决定：

1. **`REMEMBRANCE_*` 环境变量清单**：完整列出所有可配置项，统一前缀？还是保持现状（无前缀）？
2. **密钥文件注入**：aiduMEM 用 `.sf_key` 文件模式注入密钥，是否照搬？还是用 `.env` + `.gitignore`？
3. **`__file__` 自解析仓库根**：路径配置是否改为相对代码位置自动解析，而非相对 CWD？
4. **配置校验**：启动时校验维度一致性、必填项、路径可写性？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

四项决议（grilling 2026-08-02 与用户确认）：

### 1. 环境变量前缀 → 分层

- **`settings.py` 内部配置**：保持现状，不加前缀。`.env` 文件已是命名空间，Docker 容器也是隔离层
- **外部脚本/工具变量**：加 `REMEMBRANCE_` 前缀
  - `REMEMBRANCE_HOME` — 仓库根路径（`__file__` 自解析的 fallback）
  - `REMEMBRANCE_API_BASE` — API 地址（供 perf_baseline 等外部工具用）

### 2. 密钥注入 → 保持 `.env` + `.gitignore`

不引入 `.sf_key` / `.llm_key` 文件模式。理由：remembrance 只有一个 provider（硅基流），一个 key 够了；pydantic-settings 原生支持 `.env`；Docker 部署用 `-e` 或 `--env-file` 天然适配。

待办：轮换 `.env` 中已暴露的 API Key（审计 P3），更新 `.env.example` 确保无真实密钥。

### 3. `__file__` 自解析仓库根 → 采纳

`settings.py` 头部加：
```python
import os
BASE_DIR = os.environ.get("REMEMBRANCE_HOME") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
```
路径默认值改为：
```python
DATABASE_URL: str = f"sqlite:///{BASE_DIR}/remembrance.db"
CHROMADB_PATH: str = os.path.join(BASE_DIR, ".chromadb")
```
不改 setting 名称，只改默认值——门面铁律不破坏。

### 4. 配置校验 → 轻量 `validate()`，只 warn 不 crash

| 校验 | 行为 |
|------|--------|
| `DEFAULT_LANE` 缺失 | **直接补上** `DEFAULT_LANE: str = "general"` —— 修 P0 bug |
| `OPENAI_API_KEY` 为空 | `logger.warning("OPENAI_API_KEY not set — LLM features will fail")` |
| `API_KEY` 为空 | `logger.warning("API_KEY not set — authentication disabled")` |
| `VECTOR_DIMENSION` 与 `EMBED_MODEL` 不匹配 | **删掉 `VECTOR_DIMENSION`** —— ChromaDB 自动推断维度 |

不做路径可写性检查（SQLite/ChromaDB 自身报错够清晰），不 crash（`/health` 必须能响应）。

启动时在 `lifespan` 中调用 `validate()`。
