# ADR-0037: 探颐——记忆主动探针与自然交互消歧机制

**日期**: 2026-08-31
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴苏格拉底式发问与主动澄清（Proactive Clarification / Socratic Memory Resolution）思想。

---

## 背景

传统 AI 记忆系统的冲突处理完全依赖被动人工裁决：
1. 当检测到记忆冲突（例如旧记忆记录“常驻上海”，新对话提及“北京租房”）或低置信度候选时，仅将冲突记录沉淀在 `ConflictEvent` 账本中；
2. 若用户不主动在管理面板裁决，冲突事实将长期悬决；
3. Agent 缺乏在自然对话中主动向用户求证的能力。

---

## 决策

引入**「探颐」（Tanyi）** 记忆主动探针与自然交互消歧机制：

### 核心机制

1. **探针触发与生成 (`detect_memory_probes`)**：
   - 检索时若命中未解决的 `ConflictEvent(status="open")` 或置信度处于临界模糊带（0.3~0.6）的事实；
   - 自动生成 1 条温和的自然语言探针建议（例如：*“顺便向您确认下：您目前的常住地是否已变更为北京？”*）；
2. **上下文协同注入 (`format_probing_context`)**：
   - 将探针注入会话 Prompt 的 `【探颐·待求证事项】` 区域，供 Agent 决策是否在回复中附带发问；
3. **次轮自然答复闭环 (`resolve_probe_response`)**：
   - 用户下一轮做出肯定回复（如“是的/对的/没错”）时，自动将待审冲突裁决为 `resolved`，更新旧记忆版本，生成快照；
   - 用户否定或拒绝时，按规则废弃或保留原状；
4. **安全底线（宁 miss 不脏写）**：
   - 用户未正面回应或转移话题时，不强行修改，保持冲突待审状态。

---

## 影响

- 核心服务：`lantai/services/probing_service.py`
- 接口：新增 `routes_probing.py` 端点与 MCP 工具 `probe_detect` / `probe_resolve`（工具总数扩容至 **55**）
- 测试：`tests/test_probing.py`（真实不 mock 冒烟单测）

---

## 相关

- [ADR-0013](0013-naming-system.md) — 探颐命名登记
- [CONTEXT.md](../../CONTEXT.md) — 探颐词汇定义
