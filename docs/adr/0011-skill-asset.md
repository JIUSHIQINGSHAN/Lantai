# ADR-0011: Skill 资产化——procedural 记忆结构化注入

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/skill-assets/issues/01-skill-asset-pipeline.md)

## 背景

腾讯 TencentDB Agent Memory 把「可复用工作流」作为 Skill 资产（名称 + 触发边界 + 步骤 + 验证），
Agent 做完复杂工作后可沉淀为可注入的 Skill。兰台已有 `procedural` 衰减类（永不衰减）与
LLM 提取的 `actions`（步骤列表），但**提案落库时步骤丢失**（`propose_from_candidate` 的
`proposed_patch` 不含 actions，`apply_proposal` 不写 structure）——procedural 记忆退化为
平铺文本，Agent 无法按步骤执行。调研见 `docs/research/tencentdb-agent-memory-borrow.md`。

## 决策

| 项 | 决策 |
|----|------|
| 结构载体 | `MemoryItem.structure`（JSON）：`{"name", "description", "steps"}`；steps 来自提取 actions |
| 写入链路 | `proposer` 把 `cand.actions` 沉淀为 `proposed_patch.structure` → `promoter` 落库到 `MemoryItem.structure` |
| 衰减类 | 落库时 `structure.steps` 非空 → 强制 `procedural`（永不衰减，铁律天然浮顶） |
| 注入形态 | Shell Hook 对 procedural + steps 记忆注入 Skill 块（`## Skill: 名称` + 描述 + 编号步骤），普通记忆保持平铺 |
| 预算兼容 | Skill 块同样受单条/总字符预算约束（复用 `_apply_recall_budget`） |

## 理由

- 复用既有 `structure` JSON 字段，零 schema 迁移
- 步骤提取（actions）已存在，只补沉淀链路（proposer → promoter）
- Skill 块多行注入与现有预算机制天然兼容（entry 按字符串长度计）

## 后续（不在本 ADR）

- 触发边界（triggers）提取：需扩展 `EXTRACT_SYS` prompt
- Skill 版本化、审核与团队分享：对应腾讯 Memory Hub 能力，另行决策