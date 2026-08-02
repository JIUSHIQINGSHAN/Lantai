# AGENTS.md

## Agent skills

### Issue tracker

Issues 以本地 markdown 文件形式存放在 `.scratch/<feature>/` 目录下。参见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个标准分诊标签：`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`。参见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：一个根目录 `CONTEXT.md` + `docs/adr/`。参见 `docs/agents/domain.md`。

### Testing discipline（测试纪律）

**每个核心函数必须至少有一个不 mock 的冒烟测试**（真实构造最小输入直调该函数，验证主路径不炸）。

- 背景：v0.3.2 修复中暴露的 FTS schema、Chronos 时区、BM25 `ptp()` 三个真实 bug，全部因为既有测试 mock 了外部依赖、产品代码从未被真实执行到。
- 判定"核心函数"：任何被 API 路由、worker、service 层调用的存储/检索/决策函数（如 `hybrid_search`、`apply_forgetting`、`search_fts`、`apply_proposal`）。
- mock 允许用于：外部网络（LLM/embedding/rerank）、文件系统副作用；**不允许用于**：让被测函数"跳过"其内部计算逻辑。
- 新增或修改核心逻辑时，若该函数没有不 mock 的冒烟测试，测试必须补上，否则不得提交。
