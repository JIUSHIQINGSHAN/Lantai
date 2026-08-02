# ADR-0002: 零硬编码策略

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 13](../../.scratch/aidumem-port/issues/13-zero-hardcoding.md)

## 背景

审计发现 remembrance 存在多个硬编码问题：路径相对 CWD、VECTOR_DIMENSION 与 EMBED_MODEL 不匹配、promoter.py 引用不存在的 settings.DEFAULT_LANE（P0 bug）、.env 含真实 API Key。aiduMEM 有 32 个环境变量、零硬编码。

## 决策

### 1. 环境变量前缀：分层

- `settings.py` 内部配置：保持现状不加前缀（`.env` 文件 + Docker 容器已提供命名空间隔离）
- 外部脚本/工具变量：加 `REMEMBRANCE_` 前缀
  - `REMEMBRANCE_HOME` — 仓库根路径
  - `REMEMBRANCE_API_BASE` — API 地址

### 2. 密钥注入：保持 `.env` + `.gitignore`

不引入 aiduMEM 的 `.sf_key` / `.llm_key` 文件模式。remembrance 只有一个 provider，一个 key 够了。

### 3. 路径解析：`__file__` 自解析仓库根

```python
BASE_DIR = os.environ.get("REMEMBRANCE_HOME") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
```

`DATABASE_URL` 和 `CHROMADB_PATH` 默认值改为基于 `BASE_DIR`，不再相对 CWD。

### 4. 配置校验：轻量 `validate()`，只 warn 不 crash

- 补上 `DEFAULT_LANE: str = "general"`（修 P0 bug）
- 删除 `VECTOR_DIMENSION`（ChromaDB 自动推断）
- `OPENAI_API_KEY` 为空 → warn
- `API_KEY` 为空 → warn
- 不做路径可写性检查，不 crash（`/health` 必须能响应）

## 影响

- 服务从任何目录启动都能正确定位数据库和向量库
- 启动时有配置问题的清晰告警，不会静默失败
- P0 bug（`DEFAULT_LANE` 缺失）在实施阶段修复

## 相关

- [ADR-0001](0001-facade-rule.md) — 门面铁律（本 ADR 的路径默认值改动不改 setting 名称，符合门面铁律）
- [票据 13](../../.scratch/aidumem-port/issues/13-zero-hardcoding.md) — 完整决议
