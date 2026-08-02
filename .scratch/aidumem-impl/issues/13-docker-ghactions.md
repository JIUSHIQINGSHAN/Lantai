# 13 — Docker + GH Actions

**What to build:** `python:3.11-slim` 多阶段构建 Dockerfile（builder 编译 wheel，runtime 只复制 wheel + 代码）。volume 挂载 `/data` 目录（`remembrance.db` + `.chromadb/`），`REMEMBRANCE_HOME` 指向 `/data`。GH Actions：tag → build wheel → build Docker → push to GHCR（amd64 起步）。`HEALTHCHECK` 指向 `/health`。

**Blocked by:** 07 — Health + stats 端点

**Status:** ready-for-agent

- [ ] `Dockerfile` 存在，多阶段构建，基于 `python:3.11-slim`
- [ ] volume 挂载 `/data`，包含 db + chromadb
- [ ] `REMEMBRANCE_HOME` 在容器内指向 `/data`
- [ ] `HEALTHCHECK CMD curl -f http://localhost:8767/health || exit 1`
- [ ] GH Actions workflow：tag → wheel → Docker → GHCR
- [ ] 单架构 amd64 起步
