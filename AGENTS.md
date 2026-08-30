# AGENTS.md

## Agent skills

### Issue tracker

Issues 以本地 markdown 文件形式存放在 `.scratch/<feature>/` 目录下。参见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个标准分诊标签：`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`。参见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：一个根目录 `CONTEXT.md` + `docs/adr/`。参见 `docs/agents/domain.md`。

### Naming discipline（命名纪律）

新功能/新概念的正式中文名遵循 `docs/adr/0013-naming-system.md`：2–4 字、出自传统意象（官职/典籍/器物）、名实相副；**必须先登记 `CONTEXT.md` 词汇表再使用**，未登记不命名。既有术语不强制追溯；改名必须走安全迁移（宁 miss 不脏写）。

### Testing discipline（测试纪律）

**每个核心函数必须至少有一个不 mock 的冒烟测试**（真实构造最小输入直调该函数，验证主路径不炸）。

- 背景：v0.3.2 修复中暴露的 FTS schema、Chronos 时区、BM25 `ptp()` 三个真实 bug，全部因为既有测试 mock 了外部依赖、产品代码从未被真实执行到。
- 判定"核心函数"：任何被 API 路由、worker、service 层调用的存储/检索/决策函数（如 `hybrid_search`、`apply_forgetting`、`search_fts`、`apply_proposal`）。
- mock 允许用于：外部网络（LLM/embedding/rerank）、文件系统副作用；**不允许用于**：让被测函数"跳过"其内部计算逻辑。
- 「宁 miss 不脏写」补充（v0.5 Ticket 02）：校验失败（如低置信度提取）**不静默丢弃、不自动修正**——候选进待审队列（pending_review）交用户裁决，超龄（CANDIDATE_TTL_DAYS）自动归档为 rejected；裁决入口见 `GET /candidates/pending`。
- 新增或修改核心逻辑时，若该函数没有不 mock 的冒烟测试，测试必须补上，否则不得提交。

### Release discipline（版本上传纪律）

版本上传遵循 `docs/release-process.md`：发布前必须通过 `scripts/release_check.py` 门禁（版本一致性 + Git 干净 + tag 不重复），全量测试全绿，CHANGELOG 收口；上传（push tag）是人工闸门，Agent 只检查/准备，不代替维护者确认。

### Development workflow（开发工作流）

端到端开发遵循 `docs/development-workflow.md` 六阶段标准（需求票据化 → 5 步根因诊断 → 架构与命名治理 → TDD 先导与不 mock 冒烟 → 代码审查门禁 → 发布闸门）。

