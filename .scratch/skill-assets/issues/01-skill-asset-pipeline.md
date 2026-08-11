# 01 - Skill 资产化——procedural 记忆结构化注入

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory 的 Skill 资产（SKILL.md frontmatter + 执行步骤），
把兰台 procedural 记忆从「平铺文本」升级为「可注入 Skill」：提案落库时保留
提取的 steps，Shell Hook 注入结构化 Skill 块，Agent 可照步骤执行。

## 范围

- `lantai/evolution/proposer.py`：actions 沉淀为 `proposed_patch["structure"]`（name/description/steps）
- `lantai/evolution/promoter.py`：写入 `MemoryItem.structure`；steps 非空 → 强制 `decay_class="procedural"`
- `scripts/shell_hook.py`：`_is_skill_item` / `_format_skill_entry`，`build_context` 按类型分流注入 Skill 块
- 契约决策 ADR-0011；调研记录 `docs/research/tencentdb-agent-memory-borrow.md`

## 验收

1. 带 actions 的候选提案落库后 `MemoryItem.structure.steps` 非空
2. structure.steps 非空 → 记忆强制 procedural（永不衰减）
3. procedural + steps 记忆在 shell_hook 注入为 `## Skill: 名称` + 描述 + 编号步骤
4. 普通记忆（无 steps）保持平铺文本，不受影响
5. Skill 块仍受召回双预算约束（单条/总字符）
6. 核心纯函数有不 mock 的冒烟测试

## 相关文件

lantai/evolution/proposer.py、lantai/evolution/promoter.py、scripts/shell_hook.py、
tests/test_skill.py、docs/adr/0011-skill-asset.md、docs/research/tencentdb-agent-memory-borrow.md

## Answer（2026-08-11 已实现，test_skill.py 7/7 全绿）

实现内容：
- `proposer.propose_from_candidate`：`proposed_patch["structure"] = {"name", "description", "steps"}`，steps 来自 `cand.actions`（LLM 提取的步骤列表）。
- `promoter.apply_proposal`：structure 落库到 `MemoryItem.structure`；`structure.steps` 非空 → 强制 `decay_class="procedural"`（永不衰减，铁律天然浮顶）。
- `shell_hook._is_skill_item(item)`：判定 procedural + structure.steps 非空；`_format_skill_entry` 生成 `## Skill: 名称（score）` + 描述 + 编号步骤；`build_context` 按类型分流（Skill 块 / 平铺记忆）。
- Skill 块复用 `_apply_recall_budget`，与普通记忆同受 `SHELL_HOOK_MAX_CHARS_PER_MEMORY` / `SHELL_HOOK_MAX_TOTAL_CHARS` 约束。
- 测试：核心纯函数不 mock 冒烟测试（proposer/promoter/shell_hook 分流与格式化），集成用真实内存 SQLite + FakeStore。

验收对照：
1. ✅ `proposed_patch["structure"]` 携带 steps，落库后 `MemoryItem.structure.steps` 非空
2. ✅ steps 非空 → decay_class 强制 procedural
3. ✅ Skill 块注入（名称 + 描述 + 编号步骤）
4. ✅ 无 steps 记忆保持平铺（回归既有 test_shell_hook）
5. ✅ 双预算复用（_apply_recall_budget 同一入口）
6. ✅ test_skill.py 7/7 全绿，无 mock 核心计算

备注：触发边界（triggers）提取、Skill 版本化/团队分享（腾讯 Memory Hub 能力）留作后续，见 ADR-0011。
