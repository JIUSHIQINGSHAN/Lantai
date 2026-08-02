# ADR-0008: FTS5 并列接入混合检索（trigram 子串召回）

**日期**: 2026-08-03
**状态**: Accepted
**决策者**: 大哥
**来源**: [决策分析](docs/plans/fts5-decision-analysis.md)

## 决策

FTS5 作为混合检索的**并列补充召回源**接入（分析文档方案 C），要点：

1. **关键词层双轨并行**：jieba BM25（词级）保留 + FTS5 trigram（子串级）追加，二者**取并集**后融合打分，不替代任何一方。
2. **打分公式**：`score = 0.6 * 向量相似度 + 0.25 * BM25_norm + 0.05 * FTS_hit + 0.1 * 衰减`，乘 lane_boost 不变。FTS 权重起点 0.05，先保守后按实测校准。
3. **索引同步策略**：**同事务强一致**——MemoryItem 的创建/更新/回滚/删除 4 个写入点与 FTS 索引同库同事务提交，不做异步补写（弱一致多一个失败点，否决）。
4. **实施时机**：排在 v0.3.3 P1 安全修复（SSRF/备份加固/MCP 校验）**之后**。安全是必修，本项是功能增强。

## 理由

- **价值真实**：中文记忆检索中错字/子串是高频现实（用户输入不可能永远精准），trigram 子串匹配对错字/插入/删除有天然容错，是 SQLite 白送的能力。
- **不牺牲现有能力**：BM25 保留，双字中文词（"学习""看书"）不退化——这是否决"FTS 替代 BM25"（方案 D）的根本原因：trigram 最少 3 字符，双字词会查不到。
- **成本可控**：MemoryItem 唯一创建点在 `promoter.py:48`，加上更新/回滚/删除共 4 个同步点；FTS 表与记忆同库，事务内原子一致天然成立；`tests/test_p0.py` 的 4 个 FTS 测试（v0.3.2 已重写为直测 fts 层）可直接复用。
- **零新依赖**：FTS5 是 SQLite 内置能力。
- **消死代码**：FTS 由"建表未通电"的死代码变为真实功能，消除误导。

## 影响面（实施清单）

- `remembrance/evolution/promoter.py`：apply_proposal add/update 分支、rollback、delete_memory 共 4 处同步 `index_fts`（或删除索引）。
- `remembrance/retrieval/hybrid.py`：检索时对候选 `search_fts` 取命中集合加权进打分；对候选外的 FTS 命中追加召回。
- `tests/`：复用 test_p0 TestFTS5；新增 1 个"写入→检索含 FTS 命中"集成断言。
- **明确不做**：不删 BM25；不把 FTS 作为唯一关键词层；不引入异步同步机制。

## 已知限制

- FTS5 MATCH 语法对复杂布尔/短语支持弱（记忆检索场景够用）。
- 双索引一致性依赖 4 个写入点纪律——同库同事务使其可原子，但新增 MemoryItem 写入路径时必须同步补 FTS 操作。

## 相关

- [决策分析](docs/plans/fts5-decision-analysis.md)
- [ADR-0004](0004-infra-stack.md) — 基础设施栈（jieba BM25 / bge-m3 / ChromaDB cosine）
- [ADR-0006](0006-shell-hook-contract.md) — Shell Hook 契约
- 审计报告 M4 — BM25 每次全量重建（性能项，FTS 接入不自动消除，需另修语料缓存）
