# 项目审计提示词

将以下提示词完整粘贴给你的 AI Agent：

---

## 审计任务

你是一名独立审计员，负责审计 Remembrance-System v0.3.0 的 aiduMEM 移植工作。你的任务是验证代码实现是否与设计决策、规格书和 ADR 一致，并出具审计报告。

## 审计范围

项目根目录：当前工作区
审计依据：
- 目标文档：`.scratch/aidumem-port/spec.md`
- 结果文档：`docs/aidumem-port-results.md`
- 决策地图：`.scratch/aidumem-port/map.md`
- ADR 0001-0007：`docs/adr/`
- 词汇表：`CONTEXT.md`
- 实施票据：`.scratch/aidumem-impl/issues/01-14`

## 审计维度

请逐条检查以下 7 个维度，每个维度给出 ✅ 通过 / ⚠️ 部分通过 / ❌ 不通过，并附证据（文件路径 + 行号）。

### 1. ADR 合规性（7 份 ADR 逐条）

对每份 ADR，检查代码是否遵循其决策：

- **ADR-0001 门面铁律**：重构后旧 import 路径是否保持可用？路由 handler 是否只做 HTTP 解析/返回？业务逻辑是否在 service 层？
- **ADR-0002 零硬编码**：`settings.py` 是否有 `REMEMBRANCE_HOME`？路径是否通过 `__file__` 自解析？`validate_config()` 是否只 warn 不 crash？`VECTOR_DIMENSION` 是否已删除？
- **ADR-0003 coalesce 缓冲键**：缓冲键是否 = `user_id + lane`？`LANE_COALESCE_PROFILES` 是否存在？`COALESCE_ENABLED` 默认是否 false？
- **ADR-0004 基础设施栈**：`MemoryItem.embedding` 列是否已删除？BM25 是否用 `jieba.lcut()`？`EMBED_MODEL` 默认是否 `BAAI/bge-m3`？ChromaDB collection 是否用 cosine 距离？是否未引入 mem0/Qdrant？
- **ADR-0005 遗忘语义**：decay 低于阈值是否自动转 archived？搜索是否 `WHERE status='active'`？是否不做 GC 物理删除？
- **ADR-0006 Shell Hook 契约**：`scripts/shell_hook.py` 是否 stdin JSON → stdout `{context}` 或 `{}`？2s 超时？≤3 字符不注入？top_k=5 无 rerank？Markdown 列表带分数？
- **ADR-0007 集成形态**：Shell Hook（读）和 MCP server（写）是否并存？MCP 是否提供 search/add/feedback 三 tool？

### 2. Spec User Story 覆盖

读取 `spec.md` 中的 47 条 User Story，逐条检查是否有对应代码实现。对每条标注：
- ✅ 已实现（指出文件路径）
- ⚠️ 部分实现（说明缺什么）
- ❌ 未实现

### 3. 实施票据验收

读取 `.scratch/aidumem-impl/issues/` 下的 14 张票据，检查每张的验收条件是否全部满足：
- 对照 acceptance criteria 逐条验证
- 特别关注 T01 的 P0 修复（`promoter.py` 不再报 `AttributeError`）
- 特别关注 T03 的门面铁律（旧 import 路径可用）

### 4. 代码质量

- 检查 `services/` 目录下的 service 函数是否只包含业务逻辑（无 HTTP 解析）
- 检查路由 handler（`routes_*.py`）是否只做 HTTP 解析和调用 service
- 检查 `auth.py` 死代码是否已清理（`is_public_path` 和 `PUBLIC_PATHS` 是否已删除）
- 检查是否有残留的 `import numpy` 等不再使用的导入
- 检查 `Dockerfile` 是否为多阶段构建、`HEALTHCHECK` 是否指向 `/health`
- 检查 `.github/workflows/ci.yml` 是否 tag → wheel → Docker → GHCR 流程

### 5. 测试质量

- 运行 `python -m pytest tests/ -v --tb=short` 并记录结果
- 检查 6 个预存 bug 是否确实非本次引入（`test_p0.py` 的 `resp.json` 缺括号、FTS5 未 mock、prefilter 行为变化）
- 检查新测试是否真正验证了功能（不是空壳测试）
- 检查 `test_features.py` 的 fixture mock 是否合理

### 6. 架构一致性

对照 `CONTEXT.md` 词汇表，检查代码中使用的术语是否与词汇表一致：
- `lane`、`tier`、`gate`、`coalesce`、`fastpath`、`candidate`、`archived`、`decay_score`
- `Shell Hook`、`search_trace`
- 是否有词汇表中未定义的新术语出现在代码中

### 7. 结果文档准确性

对照 `docs/aidumem-port-results.md`，检查：
- 表格中的数字是否与实际一致（测试数、模块数、commit 数）
- 架构图是否与实际目录结构一致
- ADR 索引是否完整
- "保留事项"是否确实是可选增强（不阻塞实施）

## 输出格式

请按以下结构输出审计报告：

```
# 审计报告

## 总体评级
[✅ 通过 / ⚠️ 有条件通过 / ❌ 不通过]

## 维度 1: ADR 合规性
[逐条列出 7 份 ADR 的合规状态 + 证据]

## 维度 2: Spec 覆盖率
[47 条 User Story 的覆盖统计: X/47 已实现, Y 部分实现, Z 未实现]
[列出未实现或部分实现的条目]

## 维度 3: 票据验收
[14 张票据的验收条件满足统计: X/14 全部满足]
[列出未满足的条目]

## 维度 4: 代码质量
[发现的问题列表]

## 维度 5: 测试质量
[测试运行结果 + 质量评估]

## 维度 6: 架构一致性
[术语一致性检查结果]

## 维度 7: 结果文档准确性
[与实际的差异列表]

## 发现的问题
[按严重程度排序: P0/P1/P2]

## 建议
[修复建议列表]
```

## 执行步骤

1. 先读取 `CONTEXT.md` 和 `docs/aidumem-port-results.md` 了解项目全貌
2. 读取 `docs/adr/` 下全部 7 份 ADR
3. 读取 `.scratch/aidumem-port/spec.md` 获取 47 条 User Story
4. 读取 `.scratch/aidumem-impl/issues/` 下 14 张票据的验收条件
5. 逐个检查 `remembrance/` 下的源码模块
6. 运行测试套件
7. 检查 `scripts/`、`Dockerfile`、`.github/workflows/`
8. 按上述格式输出审计报告
