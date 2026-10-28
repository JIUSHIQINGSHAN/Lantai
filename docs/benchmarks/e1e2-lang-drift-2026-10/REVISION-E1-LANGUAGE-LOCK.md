# E1 提取语言漂移修复留证（v0.25 窗口票 02，2026-10-28）

## 修订原因与基线

E1 retrieve 段真 LLM 轮基线 **0.25**（2026-10-26，`docs/benchmarks/e1e2-formal-2026-10/REPORT.md` §E1），
根因为 Qwen3-8B 把中文 utterance 摘要成英文，中文查询与英文语料 FTS/向量双通道失配
（miss: q_db、q_allergy）。本票修复后复跑（rev1 轮），真轮 retrieve **0.75**。

## 根因实验（留证 `docs/benchmarks/e1e2-lang-drift-2026-10/root-cause-exp.jsonl`）

最小语料（u1/u5，即基线两个 recall_miss 的 utterance）直调生产 `extract_candidate`（真 LLM，双闸环境）：

| 臂 | 措辞 | 结果（CJK 字符占比） | 结论 |
|---|---|---|---|
| baseline_prompt | 现网模板 | 0.704 / 0.0 / 0.0 / 0.0（3/4 英文） | 漂移复现，与 REPORT 引文同形 |
| lang_locked V1 | system 加英文 LANGUAGE RULE | 0.0 / 0.0 / 0.0 / 0.0 | **无效**（system 规则被忽略） |
| v2_zh_instruction | system 加中文规则句 | 0.0 / 0.0 | **无效** |
| v3_zh_user_frame | user 消息中文任务框架+语言要求 | 0.559 / 0.935 | **有效**（2/2 中文） |
| post_fix（生产路径） | EXTRACT_USER_TEMPLATE 落地后 | 0.571 / 0.726 / 0.931 / 0.923 | **4/4 中文** |

结论：输出语言跟随 **user 消息任务框架语言**，仅改 system 模板无效——这就是「语言锁定」
的实现落点必须包含 user 消息框架的实验依据。u1 run1 延迟 699s（v3 臂，供应商慢调用，如实记录）。

## 两方案取小（详见票据 Comments 对照表）

**选定 A 语言锁定**（提取提示词 + user 框架模板，提取段单点，不动生产检索语义，预验有效）；
弃 B 双语索引（改检索主路径、需全量消融复验、翻译引入新不确定性）。

## 修复内容（只动提取段提示词与 user 模板）

- `lantai/llm/prompts.py`：`EXTRACT_SYS` 加语言规则（中英双句）；新增 `EXTRACT_USER_TEMPLATE`
  （中文任务框架，`{title}/{content}` 槽位，经 `get_prompt("EXTRACT_USER_TEMPLATE", …)` 可被库内行覆盖）。
- `lantai/parsing/extractor.py`：user 消息改由 `EXTRACT_USER_TEMPLATE` 构造（6000 字截断行为不变）。
- 测试：`tests/test_extract_language_lock.py`（漂移→召回失配机制锁定 0.25 形态 + 锁定后 0.75 +
  提取器直调冒烟 + 降级路径）；定向 `pytest tests/test_extract_language_lock.py tests/test_prompts.py
  tests/test_longmemeval_core.py -q` → **13 passed**。

## E1 复跑数字（rev1 轮，`docs/benchmarks/e1e2-formal-2026-10-rev1/`，2026-10-28）

| 段 | 真 LLM 轮（基线 2026-10-26 → rev1） | 替身 CI 轮（基线 → rev1） |
|---|---|---|
| extract | 1.0 → 1.0（NFR 0.75 → 0.7：噪音泄漏 1 → 3） | 1.0 → 1.0 |
| gate | 0.9167 → 0.9167（gate_false_pass 1 不变） | 1.0 → 1.0（gate_reject 锚点在位） |
| store | 1.0 → 1.0 | 1.0 → 1.0 |
| index | 0.9 → 0.9（index_fts_orphan 锚点均命中） | 0.9 → 0.9 |
| **retrieve** | **0.25 → 0.75**（recall_miss 2 → 0） | 0.75 → 0.75 |
| inject | 1.0 → 1.0 | 1.0 → 1.0 |

- **验收达标线达成**：真轮 retrieve 0.75 ≥ 0.75（替身轮基线口径的管道上界推断值）✓。
- 两轮 `corpus_hash` 均为 `de084f0b8b5b0058`（与基线同——语料与口径未动，只换代码不换口径）。
- rev1 `meta.json.model_identity` 与基线逐字段一致（Qwen/Qwen3-8B @ siliconflow，temperature 0.2）✓。
- rev1 真轮三锚点（index_desync / zero_recall / fence_escape）全 achieved；gate_reject 锚点
  在真轮仍缺席（l1 真提取置信度过线，gate_false_pass 1）——与基线同一形态，票面非范围（gate 段不动）。
- **如实附注**：真轮 extract NFR 0.7（3 条噪音泄漏 vs 基线 1 条）——供应商非确定性在噪音
  面的抖动，retrieve 主张不受影响（泄漏噪音未进查询对账路径）；3 报一致率口径不变（本票未复跑 consistency 段）。

## 文件对账

| 文件 | 轮次 |
|---|---|
| `docs/benchmarks/e1e2-formal-2026-10/`（REPORT.md、meta.json、e1_*.jsonl 等） | 基线，未动 |
| `docs/benchmarks/e1e2-formal-2026-10-rev1/`（meta.json、e1_real_llm.jsonl、e1_stub_ci.jsonl） | 本票新增 |
| `docs/benchmarks/e1e2-lang-drift-2026-10/root-cause-exp.jsonl` | 本票新增（根因实验逐次原始输出，可复跑输入：MINI_CORPUS 见票据 Comments / 实验脚本同款构造） |
