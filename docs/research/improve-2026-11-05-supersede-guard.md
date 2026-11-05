# 兰台改进：被更正的旧值不再当作当前事实注入 Hermes（巡检 2026-11-05 22:00）

> 本 PR 由 Maya（Sesame 的 AI 助手）自动巡检后生成，经 GitHub 小号 jiushiqingshand-dev 提交，未经人工逐行审阅，合并前请自行核对。

结论：一条记忆被更正后（比如「住武汉」→「搬到荆州」），Hermes 每轮注入的「相关记忆」里新旧两条并列，旧值没有任何标记，模型分不出哪条是现在。本 PR 在注入前加一道「更正闸」：问现在的事、新值也在结果里时，旧值不注入；问「以前」或新值没被召回时，旧值留下，但前面加「[已更正：YYYY-MM-DD 起不再成立，仅作历史]」。库里的数据、MCP `search`、检索排序都不动，可用 `SHELL_HOOK_SUPERSEDED_GUARD=false` 关掉。

## 本轮巡检情况

- 走的分支：改进分支。`origin/master` 自上次巡检（`a6f9d4ac`，11-05 12:30）以来没有新提交。
- 小号开着的 PR：只有 #26（Hermes 否决的一轮不写进记忆），未到 3 个的积压上限。你自己开的草稿 #27、#28、#29 不算小号积压。
- 选题来源：记忆研究周报 2026-11-03（`docs/research/papers/memory-weekly-2026-11-03.md`）的 P0-2。P0-1 已由 #26 处理，P0-2 一直没动。
- 冲突检查：对照了 #26–#29 的改动文件。本 PR 只改 `scripts/shell_hook.py`、`lantai/core/settings.py`、`tests/test_settings_defaults_frozen.py`（登记新配置的出厂默认值），新增一个测试文件和本报告，与它们都没有交集；为避免与 #26/#29 冲突，没有改 `CHANGELOG.md`。

## 问题

- `lantai/cognition/late_correction.py` 的 `apply_supersede_correction`：更正后旧记忆 `status` 仍是 `active`，只把 `lifecycle_status` 改成 `superseded`、写上 `superseded_by` 和 `valid_to`。这是有意设计，保留历史可查，不动。
- `lantai/retrieval/hybrid.py` 的 `_apply_supersedes_order`：只把旧值的分数压到新值之下（差 1e-6），两条仍会一起进结果。
- `scripts/shell_hook.py` 的 `build_context`（master `a6f9d4ac`）：拼注入文本时不看 `lifecycle_status`，旧值原样进入「本次依据」和「相关记忆」两段。

周报引用的 Obsolete Facts in Agent Memory（arXiv 2610.36308）测的正是这种局面：旧陈述和新陈述一起进上下文时，模型容易答旧值。

**复现（master `a6f9d4ac`）**：内存 SQLite 存三条记忆——`mem_old`「用户住在武汉，在光谷上班」、`mem_new`「用户已经搬到荆州，现在住在荆州」、`mem_tea`「用户通勤路上喜欢喝乌龙茶」；用真的 `apply_supersede_correction(mem_old, mem_new)` 更正；向量库用假库按顺序返回这三条；调真的 `build_context("我现在住在哪个城市，通勤怎么安排比较好？")`。注入文本的「相关记忆」段：

```
- [2026-10-26 | score 0.68] 用户已经搬到荆州，现在住在荆州
- [2026-10-31 | score 0.67] 用户通勤路上喜欢喝乌龙茶
- [2026-04-19 | score 0.62] 用户住在武汉，在光谷上班
```

旧值「武汉」和新值并列，没有任何「已更正」字样；「本次依据」段同样三条都有。

## 改了什么

1. `scripts/shell_hook.py` 新增 `_apply_supersede_guard(results, query)`，在 `build_context` 里放在信息密度重排和条目硬顶（`rerank_by_information_density`）之后、拼注入行之前。规则：
   - 只动 `lifecycle_status == "superseded"` 且 `superseded_by` 非空的条目（第一版只认显式更正链，不按 `valid_to` 推测，周报建议的保守版）。
   - 问句不带回看词，且取代它的新记忆也在本次结果里 → 旧条目不注入。
   - 其他情况（问句带「以前、之前、当时、去年、变化……」，或新值没被召回）→ 旧条目保留，正文前加 `[已更正：YYYY-MM-DD 起不再成立，仅作历史] `。日期取 `valid_to`（新值的现实起点），缺失时回落 `superseded_at`，按 `SHELL_HOOK_INJECT_TZ`（默认北京时间）渲染；取不到就不写日期。
   - 不改入参：标记只加在注入副本上。「本次依据」和 `evidence` 里的正文与注入行一致，也带标记。
   - 为什么放在硬顶之后：只有新值确实会注入时才丢旧值。如果放在硬顶之前，新值可能被硬顶挤掉、旧值又已被丢，模型两条都看不到。
2. `lantai/core/settings.py` 新增 `SHELL_HOOK_SUPERSEDED_GUARD: bool = True`。设为 `false` 时函数原样返回，注入帧逐字回到旧行为。`tests/test_settings_defaults_frozen.py` 的 `FROZEN_DEFAULTS` 里同步登记了这个默认值。
3. 新增 `tests/test_inject_supersede_guard.py`，21 个用例：
   - 纯函数 17 个：新值在场时丢旧值、回看问句保留并标记、新值不在时保留并标记、失效日按显示时区（UTC 10-25 20:00 显示为北京时间 10-26）、回落 `superseded_at` 和脏串不硬造日期、无 `superseded_by` 的条目和普通条目不动、不改入参、`document` 字段也带标记、开关关闭原样返回、回看词与当前态问句各几例。
   - 集成 4 个（真 MemoryItem + 真 `apply_supersede_correction` + 真 `build_context`）：当前态问句注入文本里没有「武汉」、有「荆州」和无关的「乌龙茶」；「我以前住在哪个城市？」里「武汉」在且带标记；只召回旧值时保留并带标记；开关关闭时新旧并列、无标记。

## 改前改后

同一复现，本分支的「相关记忆」段：

- 当前态问句「我现在住在哪个城市，通勤怎么安排比较好？」：

  ```
  - [2026-10-26 | score 0.68] 用户已经搬到荆州，现在住在荆州
  - [2026-10-31 | score 0.67] 用户通勤路上喜欢喝乌龙茶
  ```

- 回看问句「我以前住在哪个城市？」：

  ```
  - [2026-10-26 | score 0.68] 用户已经搬到荆州，现在住在荆州
  - [2026-04-19 | score 0.62] [已更正：2026-10-26 起不再成立，仅作历史] 用户住在武汉，在光谷上班
  ```

- 当前态问句但只召回到旧值：

  ```
  - [2026-04-19 | score 0.62] [已更正：2026-10-26 起不再成立，仅作历史] 用户住在武汉，在光谷上班
  ```

三种情况在 master 上都是旧值无标记。

## 测试

- 新测试：本分支 21 个全部通过。把 `scripts/shell_hook.py` 和 `lantai/core/settings.py` 都换回 master 版本，21 个全部失败（新函数和新配置不存在）。只把 `scripts/shell_hook.py` 换回 master、保留新配置跑 4 个集成用例：3 个断言失败（`assert '武汉' not in ...`，以及注入行里的「用户住在武汉，在光谷上班」没有「已更正」标记），开关关闭那个用例通过——说明它确实等于 master 行为。
- 全量 pytest（项目 .venv，Python 3.13）：2944 passed、5 skipped、0 failed（耗时约 5 分钟）。
- `ruff check` 与 `ruff format --check`：改动的 3 个 Python 文件全部通过。

## 怎么验收

1. `python -m pytest tests/test_inject_supersede_guard.py -q`，应为 21 passed。
2. 在自己的库里找一对更正过的记忆（终端里 `lifecycle_status = superseded` 的条目），在 Hermes 里分别问一句现在态和一句「以前……」，看注入帧：前者不应出现旧值，后者旧值带「已更正」前缀。
3. 想回到旧行为：`.env` 里写 `SHELL_HOOK_SUPERSEDED_GUARD=false`。

## 已知边界和没验证的

- 没用真的 Hermes 跑完整一轮对话；向量检索用的是测试里的假库和假 embedding，没有用真实模型密钥（VM 上没有）。没在 Windows 上跑。
- 没跑 selfproof、E2 时态评测和 LongMemEval。这个改动只在结果里同时有新旧两条时才会少注入一条，不影响排序，但没用评测数字确认。
- 回看词是一份字面词表（以前、之前、当时、去年、上次、变化、before、used to 等）。误判方向只会是「多留一条带标记的旧值」，不会把有用的记忆挡掉。「出门之前提醒我……」这类问句会被当成回看，旧值照样带标记留下。
- 更正链跨两步时（A 被 B 取代、B 又被 C 取代，结果里只有 A 和 C），A 的 `superseded_by` 是 B，不在结果里，所以 A 会带标记留下而不是被丢。这是有意保守，以后可以顺着链往上找。
- 被丢掉的旧值也不再进本次检索事件的结果列表和 `evidence`，插件回执时也就不会把它算作用过（模型本来就没看到它）。
- 只改了 Hermes 每轮注入这条链路（`scripts/shell_hook.py`）。`lantai/cognition/context.py` 的认知上下文和 MCP `search` 没动，是否也要加同样的闸，可以另开一个 PR。
- 周报 P2-4（每条记忆在「本次依据」和「相关记忆」里各出现一次）这次没做，它会改注入格式，需要你确认 Hermes 端有没有依赖「本次依据」的写法。
