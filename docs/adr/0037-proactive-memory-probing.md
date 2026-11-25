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

### 次轮消歧补充（2026-11-25）

- 冲突分支统一使用 `parse_user_resolution_intent`，先识别疑问、假设、双重否定与含糊表态，再识别肯定、否定和明确第三值修订。英文事实值及版本号保留原样；低置信度不猜测写入。模糊记忆和原子事实的确认语义沿用原实现。
- 既有 CJK 安全契约优先保留：冲突分支承认“请勿记录、不可采信、禁止改、并非如此、勿改、别记录”等明确拒绝；单独的“没有问题”“还是改成……”仍保持待审，不视作自动覆盖正文的授权。明确“改成……”第三值修订仍可经既有入口处理。
- `execute_tanyi_resolution` 与 `resolve_probe_response` 均先校验属主，再通过现有 `resolve_conflict_event(..., session=...)` 裁决；调用方会话统一提交正文版本 CAS、冲突终态 CAS、checkpoint 和 FTS。任一持久化步骤失败，整笔回滚；独立调用冲突服务仍自行提交。
- 向量同步沿用 SQL 提交后的既有入口，回执 `vector_synced` 如实标明结果，向量失败不冒充同步成功。
- 含糊或离题答复保持冲突 `open`，只原子递增 `ConflictEvent.detail.tanyi_observation.deferred_count` 并登记 `last_intent=ambiguous`。该计数是当前冲突的累计观测次数，不表示新增定时窗口或到期裁决；不存用户原话，不改正文与记忆版本，其他证据元数据保留。已裁决冲突不能被陈旧答复重新打开。
- 首次探颐写入若只有一条 checkpoint，可用其中相邻版本的 `before` 快照回滚；多快照回滚沿用既有选取规则。恢复保留记忆 ID 与治理字段，版本继续递增，并重建索引。
- REST `/probing/resolve`、MCP `probe_resolve` 和既有探针 ID 不变；回执仅新增修订动作 `applied_correction` 与含糊分支的 `observation_count`。不新增表、记忆实体或 `supersedes` 自连接。

---

## 影响

- 核心服务：`lantai/services/probing_service.py`
- 接口：新增 `routes_probing.py` 端点与 MCP 工具 `probe_detect` / `probe_resolve`（工具总数扩容至 **55**）
- 测试：`tests/test_probing.py`（真实不 mock 冒烟单测）
- 次轮回归：`tests/test_tanyi_followup.py`（真实 SQLite、FTS5、Chroma，外部 embedding 使用确定性替身；覆盖公开接口、多轮、并发、故障回滚与首次快照恢复）

---

## 相关

- [ADR-0013](0013-naming-system.md) — 探颐命名登记
- [CONTEXT.md](../../CONTEXT.md) — 探颐词汇定义
