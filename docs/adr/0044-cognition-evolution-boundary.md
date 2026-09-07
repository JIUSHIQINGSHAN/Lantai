# ADR-0044: cognition/ 与 evolution/ 职责边界

**状态**：Accepted  
**日期**：2026-09-07  
**版本**：v0.3  

## 背景

v0.2 完成后，两套反思系统并存：

| 模块 | 触发方式 | 调用 LLM | 写入对象 |
|------|----------|----------|---------|
| volution/reflector.py | APScheduler 定时 + 水位触发 | 是（curator） | MemoryProposal → MemoryItem |
| cognition/reflection.py | eflect_worker 追加调用 | 否（纯规则） | CognitivePattern → MemoryItem(candidate) |

职责重叠导致：两套系统可能对同一 MemoryItem 表竞争写入，边界不清。

## 决策

三层职责划分，单向依赖：

`
cognition/evolution.py          ← 纯晋升计算器（无 LLM，无副作用）
       ↑ 复用
cognition/reflection.py         ← 规则引擎（FailureRecord→Pattern→Belief，无 LLM）
       ↑ 追加调用
evolution/reflector.py          ← LLM curator（健康扫描+蒸馏+自动提案，有 LLM）
`

**单向依赖约束**：
- cognition/ → volution.py：允许（复用晋升计算）
- volution/ ↛ cognition/：禁止（evolution 层不能 import cognition 层规则引擎）

**职责边界**：
- volution/reflector.py：面向 Proposal 层，处理候选记忆蒸馏与提案裁决，有 LLM 调用
- cognition/reflection.py：面向 FailureRecord/Pattern 链，纯确定性规则，不调 LLM
- cognition/evolution.py：纯计算器，calculate_promotion_score/detect_patterns/propose_*，无网络调用，无 DB 副作用（detect_patterns 除外，会写 CognitivePattern）

## 后果

- 两套系统可以独立测试，不互相依赖
- failure_pattern 路径（v0.3 新增）完全在 cognition/reflection.py 内，不影响 LLM curator
- eflect_worker.py 按顺序调用两者，先 LLM curator 后规则引擎
- v0.5 Cognitive Middleware 重构时，可以安全地将 cognition/reflection.py 升级为 Middleware 组件
