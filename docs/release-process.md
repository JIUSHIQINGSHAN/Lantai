# 版本上传规范流程

> 适用范围：兰台记忆（Lantai）发布新版本并上传到 GitHub / GHCR 的完整流程。
> 原则：**发布是人工闸门**。脚本和 Agent 只做检查与准备，最终 `push tag` 由维护者确认后执行。

## 术语

- **版本上传**：把已收口的版本以 `vX.Y.Z` tag 推送到 `origin/master`，触发 GitHub Actions 构建并推送 `ghcr.io/<owner>/remembrance:vX.Y.Z` 镜像。
- **发布版本**：`pyproject.toml` 中的 `version`，遵循 Semantic Versioning。
- **发布门禁**：`scripts/release_check.py`，只读检查，不修改任何文件、不推送。

## 上传前门禁

以下检查全部通过才允许进入上传步骤：

1. 全量测试通过：`python -m pytest tests/ -q`，0 failed。
2. 发布门禁通过：`python scripts/release_check.py vX.Y.Z`（上传前可加 `--online` 检查远程 tag），0 FAIL。
3. CHANGELOG 已收口：`## [Unreleased]` 内容迁移到 `## [X.Y.Z] - YYYY-MM-DD`，顶部保留新的 `## [Unreleased]`。
4. 版本代号（若该版本需要）已按 [ADR-0013](adr/0013-naming-system.md) R7 登记到 `CONTEXT.md` 词汇表与 ADR-0013 映射表。
5. 文档同步：README 徽章 / README Docker 示例 / `api_server.py` FastAPI version / `scripts/mcp_server.py` serverInfo version / CHANGELOG 最新发布段一致（门禁自动核对）。
6. Git 状态干净：当前分支为 `master`，除待提交的发布变更外无修改或未跟踪文件，`vX.Y.Z` tag 本地不存在；加 `--online` 时同时确认远程不存在，远程 `origin` 存在。

## 上传步骤

### 1. 更新版本号

`pyproject.toml` 是版本事实来源，其余四处同步：

- `pyproject.toml`：`version = "X.Y.Z"`
- `README.md`：版本徽章 + Docker 示例（`lantai:X.Y.Z`）
- `api_server.py`：`FastAPI(..., version="X.Y.Z", ...)`
- `scripts/mcp_server.py`：`serverInfo` 的 `version`

> `BACKUP_MANIFEST_VERSION` 是备份清单格式版本，不随发布版本递增；只有备份格式变更时才单独提升。

### 2. 收口 CHANGELOG

- 把 `## [Unreleased]` 下的条目移到 `## [X.Y.Z] - YYYY-MM-DD`。
- 顶部保留新的 `## [Unreleased]`。
- 提交信息遵循 Conventional Commits。

### 3. 复跑门禁

```bash
python scripts/release_check.py vX.Y.Z --online
```

### 4. 提交并打 tag

```bash
git add -A
git commit -m "chore(release): vX.Y.Z"
git tag vX.Y.Z
```

### 5. 上传

```bash
git push origin master --tags
```

### 6. 验证 CI 产物

- 在 GitHub Actions 确认 `Build and Push Docker Image` 成功。
- 拉取镜像并冒烟：

```bash
docker pull ghcr.io/JIUSHIQINGSHAN/remembrance:vX.Y.Z
docker run -d -p 8767:8767 \
  -e API_KEY=your-admin-key \
  -e OPENAI_API_KEY=sk-xxx \
  ghcr.io/JIUSHIQINGSHAN/remembrance:vX.Y.Z
curl http://127.0.0.1:8767/health
```

### 7. 发布后记录

- 更新 README 里程碑 / 文档索引（如需要）。
- 在结果或交接文档中补充本次发布（如 `docs/aidumem-port-results.md`）。
- 若本次发布登记了新正式中文名，确认 `CONTEXT.md` 词汇表与 ADR-0013 已同步。

## 故障与回滚

- **门禁失败**：禁止上传。修复后重新跑门禁，不带病发版。
- **tag 打错 / 提交需修正**：删除本地与远程 tag 后修正再重新打：

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
```

- **CI 失败**：保留既有 tag / 镜像，修复后以新 PATCH 版本发布；不重写已推送的 `latest`。
- **镜像异常**：旧 tag 镜像不会被覆盖，可继续使用上一版本；先确认根因再发新版本。

## 自动化边界

- `scripts/release_check.py` 只读：不改文件、不提交、不打 tag、不推送；`--online` 也只查询远程，不写任何内容。
- Agent 可执行第 1-3 步与全部检查；第 4-5 步（提交 / tag / 推送）由维护者确认后执行，或由维护者显式授权后执行。
