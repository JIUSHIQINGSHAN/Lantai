# Behavioral Learning Benchmark（BLB）

> **版本**：v0.3  
> **目标**：证明 Lantai 能够从 Agent 的失败中学习，并在后续任务中改变 Agent 的认知上下文（行为改变的代理指标）。

---

## 为什么需要 Behavioral Learning Benchmark

传统 Memory System Benchmark 只测「能否正确检索」。  
Lantai v0.3 的目标是证明：

`
Task A → 失败 → 学习 → Task B → 认知上下文改变 → 行为改变
`

如果 Task B 因 Task A 的经验而产生不同的认知上下文，Benchmark 才算通过。

---

## Benchmark 列表

### BL-01：失败到模式（Failure → Pattern）

**目标**：FailureRecord 应在 1 次 reflect 内被归纳为 CognitivePattern。

| 指标 | 通过标准 |
|------|---------|
| CognitivePattern.pattern_type | "failure_pattern" |
| CognitivePattern.confidence | >= 0.55 |
| 所需 FailureRecord 数量 | >= 2 条 lesson 相似 |

**对应测试**：	ests/cognitive/test_cognitive_loop.py::test_failure_to_pattern

---

### BL-02：模式到信念（Pattern → Belief）

**目标**：failure_pattern 应以低阈值（0.55）晋升为 BELIEF candidate，且晋升有可解释的 promotion_trace。

| 指标 | 通过标准 |
|------|---------|
| MemoryItem.role | CognitiveRole.BELIEF |
| MemoryItem.status | "candidate"（Candidate ≠ Knowledge） |
| MemoryItem.promotion_trace | 非空，包含 promoted_from="failure_pattern" |
| MemoryItem.promotion_trace["score"] | >= 0.55 |

**对应测试**：	ests/cognitive/test_cognitive_loop.py::test_pattern_to_belief

---

### BL-03：信念持久化（Belief Persistence）

**目标**：晋升的 BELIEF candidate 在 DB commit 后仍可查询。

| 指标 | 通过标准 |
|------|---------|
| 查询 MemoryItem WHERE role=BELIEF AND status=candidate | >= 1 条 |

**对应测试**：	ests/cognitive/test_cognitive_loop.py::test_belief_persists

---

### BL-04：上下文注入（Context Contains Lesson）

**目标**：Task B 的 CognitiveContext.to_prompt() 应包含 Task A 的 lesson 关键词。

| 指标 | 通过标准 |
|------|---------|
| lesson_keyword in ctx.to_prompt() | True |

**说明**：这是「行为改变」的直接代理指标——Agent 在 Task B 时能在上下文中看到 Task A 的经验教训。

**对应测试**：	ests/cognitive/test_cognitive_loop.py::test_context_contains_lesson

---

### BL-05：行为改变度量（Behavior Change）

**目标**：学习后的 cognitive_context 比学习前更丰富（failures 或 beliefs 切面增加）。

| 指标 | 通过标准 |
|------|---------|
| len(ctx_after.failures) > len(ctx_before.failures) OR len(ctx_after.beliefs) > len(ctx_before.beliefs) | True |

**说明**：这是「认知闭环」闭合的量化证明——同一 task query，学习后的上下文发生了可测量的变化。

**对应测试**：	ests/cognitive/test_cognitive_loop.py::test_behavior_change

---

## 运行方式

`'bash
# 运行全部 5 个 BL Benchmark
uv run pytest tests/cognitive/test_cognitive_loop.py -v

# 单独运行某个 Benchmark
uv run pytest tests/cognitive/test_cognitive_loop.py::test_failure_to_pattern -v
`'

---

## v0.3 通过状态

| Benchmark | 状态 |
|-----------|------|
| BL-01 失败到模式 | ✅ PASSED |
| BL-02 模式到信念 | ✅ PASSED |
| BL-03 信念持久化 | ✅ PASSED |
| BL-04 上下文注入 | ✅ PASSED |
| BL-05 行为改变度量 | ✅ PASSED |

> **结论**：Lantai v0.3 已通过全部 Behavioral Learning Benchmark，证明认知闭环生效：  
> Agent 第一次犯错 → Lantai 学到东西 → Agent 第二次遇到类似情况 → 认知上下文发生可验证的改变。
