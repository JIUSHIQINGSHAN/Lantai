# Docker 与 GH Actions 发布流

Type: grilling
Status: resolved
Blocked by: 01

## Question

remembrance 当前无 Dockerfile、无 CI/CD。需要决定：

1. **基础镜像**：python:3.11-slim？是否需要 SQLite + ChromaDB 的特殊处理？
2. **多阶段构建**：builder 阶段装依赖 + 编译，runtime 阶段只复制 wheel？
3. **GH Actions 发布流**：tag → build wheel → push to GHCR？是否需要多架构（amd64/arm64）？
4. **数据库持久化**：SQLite + ChromaDB 数据如何挂载？volume 策略？
5. **健康检查**：Docker HEALTHCHECK 指向哪个端点？

依赖票据 01 的技术栈结论——如果换 Qdrant，Dockerfile 需要包含 Qdrant 服务或 docker-compose。

## Answer

五项决议（grilling 2026-08-02 与用户确认，阻塞已随 01 清除）：

### 1. 基础镜像 → python:3.11-slim

SQLite 和 ChromaDB 都是纯 Python，无特殊系统依赖。01 确定保留 ChromaDB，无外部进程。

### 2. 多阶段构建 → 是

builder 装依赖编译 wheel，runtime 只复制 wheel + 代码。镜像更小。

### 3. GH Actions → tag → wheel → Docker → GHCR

- tag → build wheel → build Docker image → push to GHCR
- 单架构 amd64 起步，arm64 后续加

### 4. 数据持久化 → volume 挂载

- volume 挂载 `/data` 目录，内含 `remembrance.db` + `.chromadb/`
- 票据 13 的 `BASE_DIR` 在 Docker 内指向 `/data`，或用 `REMEMBRANCE_HOME` 覆盖

### 5. 健康检查 → /health

```dockerfile
HEALTHCHECK CMD curl -f http://localhost:8767/health || exit 1
```

用简单存活探针，不用 deep。
