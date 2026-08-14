# ADR-0020: salience 冲突降权与反义词碰撞（contradiction gate 整合）

**日期**: 2026-08-14
**状态**: Accepted
**决策者**: 大哥
**来源**: 白皮书路线图「salience 冲突降权与 contradiction gate 整合」（Fog 项，v0.3 起保留）

## 背景

`gate/conflict_rules.py::check_rules` 用互斥词对（`CONFLICT_MUTEX_RULES`，子串匹配）做确定性
冲突检测；命中即 `ARCHIVE_CONFLICT`（候选进人工裁决）。两个缺口：

1. **反义词对碰撞缺失**：默认规则集只有 3 对（启用/禁用、已开启/已关闭、版本 1/版本 2）。
   "喜欢咖啡" vs "讨厌咖啡"、"支持 X" vs "反对 X" 这类高频矛盾走 LLM 兜底——延迟高、不可复现。
   子串匹配无法直接扩词表："是" 会命中 "不是"、"会" 命中 "不会"——单字反义词必须词级匹配。
2. **salience 无降权**：低重要度旧记忆（长期未用、importance 低）与新信息冲突时仍强制
   ARCHIVE_CONFLICT，把本可安全推进的更新卡在人工闸门；而高 salience 记忆的矛盾本就不该
   自动化解。缺一条「旧记忆弱 → 降权放行、旧记忆强 → 人工裁决」的分流。

## 决策

| 项 | 决策 |
|----|------|
| 反义词碰撞 | `conflict_rules.py` 新增纯函数 `check_antonyms(new, existing)`：jieba 词级匹配互斥反义词对（`CONFLICT_ANTONYM_RULES`，settings 可配），new 命中 A 且 existing 命中 B（或反向）→ 冲突。词级 token 集合比较，杜绝子串误伤（"是" 不命中 "不是"）。与 check_rules 并列，同受 `CONFLICT_RULES_ENABLED` 门控 + 独立 `CONFLICT_ANTONYM_ENABLED` |
| salience 降权 | 确定性冲突（规则/反义词，非 LLM）命中**低 salience** 旧记忆（`importance < CONFLICT_SALIENCE_MIN_IMPORTANCE`=0.4）且候选提取置信 ≥ 闸门下界时：① 旧记忆 importance 降 `CONFLICT_SALIENCE_DEMOTE_STEP`(0.2)（下限 0，写 MemoryCheckpoint 可回滚）；② ConflictEvent 记 `kind="salience_demote"`、status="resolved"（可溯源可复核）；③ 候选**不**进 ARCHIVE_CONFLICT，走正常决策流（WORKING_ONLY → 提案 update/merge，有刹车） |
| 高 salience | 保持不变：ARCHIVE_CONFLICT 人工裁决（宁 miss 不脏写：强记忆的矛盾不自动化解） |
| LLM 兜底 | 不改：规则/反义词均未命中才回落 LLM；LLM 命中不走降权（不确定性归人） |
| 默认词表 | `CONFLICT_ANTONYM_RULES` 默认 8 对（多字词对，jieba 稳定成词）：喜欢/讨厌、支持/反对、同意/拒绝、允许/禁止、在线/离线、免费/付费、公开/私密、开始/停止。**单字否定对（是/不是、会/不会、能/不能、有/没有、要/不要）默认不启用**——jieba 并词（"我会"→一词、"我是"→一词）使词级匹配不可靠（"会" 匹配不到 "我会"），宁 miss 不脏写；可在 settings 自行补充 |
| 命名 | 不新命名；术语沿用「冲突消解确定性层 / 账本」（ADR-0010）与路线图「salience 冲突降权」 |

## 理由

- 词级反义词 = 确定性、零 LLM、可复现，与 ADR-0010 规则优先原则同构；jieba 已在依赖树；
- 低 salience 旧记忆 = 弱证据：降权 + 提案刹车（新值仍走人工可审）比强制人工裁决更符合
  「遗忘是特性」——旧值降权不删（ADR-0005），新值经提案链可见；
- 一切动作落账本 + Checkpoint：可溯源、可回滚，无静默写（宁 miss 不脏写）；
- 高 salience 分支与 LLM 分支维持原语义，行为变化只发生在「确定性规则 × 弱旧记忆」交集。

## 影响

- `settings.py`：`CONFLICT_ANTONYM_ENABLED` / `CONFLICT_ANTONYM_RULES` /
  `CONFLICT_SALIENCE_MIN_IMPORTANCE` / `CONFLICT_SALIENCE_DEMOTE_STEP`。
- `gate/conflict_rules.py`：新增 `check_antonyms`（纯函数，不 mock 冒烟测试）。
- `gate/decision.py`：规则命中循环后按 salience 分流（低 → 降权 + resolved 账本 + 放行；
  高 → ARCHIVE_CONFLICT）；降权写 MemoryCheckpoint（复用 promoter._make_checkpoint）。
- 既有 `test_decide_rule_hit_short_circuits_llm`（旧记忆 importance=0.5 ≥ 0.4 → 高 salience
  分支）语义不变；新增低 salience 分流测试 + 反义词词级测试（含 "不是" 不误伤 "是"）。

## 相关

- [ADR-0010](0010-conflict-resolution-layer.md) — 冲突账本/规则优先
- [ADR-0005](0005-forgetting-semantics.md) — 只降权不删
- [ADR-0013](0013-naming-system.md) — 参商（矛盾检测）候选意象
