# fastpath 白名单直写：绕过 LLM 提取的句型与验收标准

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 有 fastpath 白名单：某些句型（如简单事实陈述、用户偏好）绕过 LLM 提取直接写入，原则是「宁 miss 不脏写」——宁可漏掉不入库，也不要用低质量提取污染记忆库。

需要决定：

1. **白名单句型**：哪些句型走 fastpath？（如 "我叫X"、"我喜欢X"、"记住：X" 等模式匹配？）
2. **实现方式**：正则匹配？关键词触发？还是规则引擎？
3. **验收标准**：「宁 miss 不脏写」的量化标准是什么？fastpath 的 precision/recall 目标？
4. **与 coalesce 的关系**：fastpath 是在 coalesce 缓冲前还是后判断？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

四项决议（grilling 2026-08-02 与用户确认）：

### 1. 白名单句型 → 三类

- **自我声明**：「我叫X」「我是X」
- **偏好表达**：「我喜欢X」「我不喜欢X」
- **显式指令**：「记住：X」「记一下X」
- 其余一律走 LLM 提取

### 2. 实现方式 → 正则匹配

3-5 条正则覆盖三类句型，放 `parsing/fastpath.py`。不需要规则引擎。

### 3. 验收标准 → precision ≥ 95%，不设 recall

- 「宁 miss 不脏写」= 命中 fastpath 的句型确实应直写（precision ≥ 95%）
- miss 可接受（不设 recall 目标）
- 手工标注 50 条测试集验证

### 4. 与 coalesce 关系 → 缓冲前判断

- fastpath 命中直接返回 `fastpath_candidate`，不入缓冲（和 aiduMEM 一致）
- 省一次入队+出队
