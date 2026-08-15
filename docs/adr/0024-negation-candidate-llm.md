# ADR-0024: 参商单字否定对——候选探测 + LLM 裁决兜底

**日期**: 2026-08-15
**状态**: Accepted
**决策者**: 大哥
**来源**: v0.15 路线计划书 C2 项（ADR-0020 已知限制收口）

## 背景

ADR-0020 的反义词碰撞（`check_antonyms`）用 jieba 词级互斥，但单字否定对
（是/不是、会/不会、能/不能、有/没有、要/不要）因 jieba 并词（"我会"→一词、"我是"→一词）
词级匹配不可靠，默认从 `CONFLICT_ANTONYM_RULES` 排除——"我会游泳" vs "我不能游泳"
这类高频否定矛盾零 LLM 探测不到，只能走通用 LLM 兜底（全部候选都调 LLM，成本高且
无针对性）。

## 决策

| 项 | 决策 |
|----|------|
| 候选探测 | `conflict_rules.check_negation_pairs(new, existing)` 纯函数：`CONFLICT_NEGATION_PAIRS`（settings 可配，默认 是/不是、会/不会、能/不能、有/没有、要/不要）——**token 级子串探测**：new 任一 token 含 A 且 existing 任一 token 含 B（或反向）→ 候选命中。命中只是**候选**（不确定性），不落硬规则 |
| 裁决 | 候选命中 → 对该记忆调 `check_contradiction`（LLM 矛盾检测，既有通道）；LLM 判 contradicts → 硬冲突（archive_conflict）；LLM 失败/无矛盾 → 不冲突（宁 miss） |
| 与 ADR-0020 分层 | check_rules（硬互斥，子串）/ check_antonyms（硬反义，词级）/ check_negation_pairs（候选，LLM 裁决）——确定性递减、裁决权递增 |
| settings | `CONFLICT_NEGATION_ENABLED: bool = True`；`CONFLICT_NEGATION_PAIRS: list`（默认 5 对） |
| 成本 | 仅候选命中记忆才调 LLM（"开会"误候选 → LLM 判非矛盾，零冲突）；related[:10] 内 | 

## 理由

- 单字否定对的歧义本质：token 级子串探测会误候选（"开会"含"会"、"他是"含"是"），
  但**误候选交给 LLM 裁决**——既捕获"我会游泳 vs 我不能游泳"，又不让"开会 vs 不能缺席"
  误伤（LLM 判非矛盾）；
- 宁 miss：LLM 失败/超时 → 不冲突（维持现状，不因探测引入假冲突）；
- 复用既有 `check_contradiction`（CONTRADICTION_SYS prompt），零新 prompt。

## 影响

- `gate/conflict_rules.py`：`check_negation_pairs`（纯函数，不 mock 冒烟测试）。
- `gate/decision.py`：规则/反义词循环后，对否定候选目标记忆调 LLM 矛盾检测；
  命中并入 conflicts（archive_conflict）。
- 测试：探测纯函数（"开会"不误候选？否——探测命中但裁决不冲突；"我会游泳 vs
  我不能游泳"候选命中）+ 裁决接线（mock LLM：contradicts → archive；失败 → 不冲突）。

## 相关

- [ADR-0020](0020-salience-conflict-demotion.md) — 反义词碰撞与 salience 降权
- [ADR-0010](0010-conflict-resolution-layer.md) — 规则优先 LLM 兜底双通道
