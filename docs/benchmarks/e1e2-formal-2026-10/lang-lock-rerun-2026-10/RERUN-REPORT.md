# E1 复跑留证（提取语言漂移修复后，v0.25 窗口票 02）

- 复跑日期：2026-10-28 00:21–00:44（本地）。命令：`PYTHONPATH=. python scripts/run_e1_e2_formal.py --out-dir docs/benchmarks/e1e2-formal-2026-10/lang-lock-rerun-2026-10 --phase e1 --skip-consistency`
- **结论：真 LLM 轮 retrieve 0.75（3/4）对基线 0.25，+0.50，达到票面达标线（真轮 retrieve ≥ 0.75，替身轮基线口径）——语言失配在管道层面消除至替身轮同值的管道上界（q_anchor_zero 预埋零召回按设计不计成功，0.75 即上界）。**

## 结果对照（与 `../` 基线同口径逐段）

| 段 | 基线真轮 | 复跑真轮 | 基线替身 | 复跑替身 |
|---|---|---|---|---|
| extract | 1.0（NFR 0.75） | 1.0（NFR 0.0） | 1.0 | 1.0 |
| gate | 0.9167 | 0.9167 | 1.0 | 1.0 |
| store | 1.0 | 1.0 | 1.0 | 1.0 |
| index | 0.9 | 0.9 | 0.9 | 0.9 |
| **retrieve** | **0.25** | **0.75** | 0.75 | 0.75 |
| inject | 1.0 | 1.0 | 1.0 | 1.0 |

- 复跑真轮 retrieve 桶：仅 `recall_zero_recall:1`（预埋零召回锚点，按设计）；`recall_miss` 为 **0**——基线的 miss（q_db、q_allergy）全部转为命中，q_editor 保持命中（`root_cause_chains` 中 retrieve 首错记录为空，见 `e1_real_llm.jsonl`）。
- 锚点：真轮 index_desync / zero_recall / fence_escape 全 achieved；gate_reject 缺席（l1 真提取置信度未过拒收线 → gate_false_pass 1，与基线真轮同形态，属真 LLM 行为与标签口径的实测分歧，票面非范围）。替身轮 4 锚点全 achieved。

## 口径不变性核对（与 0.25 基线可比）

- `corpus_hash` 逐位一致：`de084f0b8b5b0058`（基线 `../e1_real_llm.jsonl` vs 本轮，同语料同查询）。
- harness 同一：`lantai.eval.staged.run_staged_eval` 全六段；embed 面同为确定性 512（`deterministic_embed`），真 LLM 限定 chat 面。
- 模型钉板逐字段一致（`meta.json` vs 基线 `../meta.json`）：`Qwen/Qwen3-8B` / `BAAI/bge-m3` / `https://api.siliconflow.cn/v1` / temperature 0.2（`model_identity` 四字段同值；探活延迟 69.2s vs 基线 38.3s，均 ok）。
- 数据隔离双闸 ok（settings 三项指 `eval-data/` + db.engine 内存库，`meta.json` double_gate_ok=true）；API 单通道直调（chat 面），无宿主真实库写入。

## 代码形态钉板（本次数字对应哪个修复）

- 复跑进程 2026-10-28 00:21:29 导入模块，此时工作区代码为**两处修复的合并态**：`lantai/parsing/extractor.py`（00:04:32 落盘：提取 user 消息走中文任务框架模板 `EXTRACT_USER_TEMPLATE`，可被库内行覆盖）+ `lantai/llm/prompts.py`（EXTRACT_SYS 内英文/中文两条语言规则行）。即：**本留证数字对应当前工作区实际代码**，证据与随仓形态一致。
- 并行作业留痕：本票在两个并行会话各自做了根因实验与修复（本目录上游实验 `../lang-drift-2026-10/`；另一实验 `../e1e2-lang-drift-2026-10/`，其 post_fix 臂对合并态提取 4/4 中文）。首次复跑尝试（2026-10-27 23:49:53 起跑，导入时点早于 00:04 的 extractor.py 改动）于 ~00:20 被评测 harness 后台时限终止在真轮中段——正式脚本无逐题 checkpoint，该次无任何产物；本数字来自 00:21:29 从零起跑的完成版。两版本差异仅在提取 prompt 形态，管道其余五段完全同构。
- 提取段 3 报一致率本次未跑（--skip-consistency）：基线已证供应商非确定性显著（0/10），对表基线固定在确定性替身，非本票验收面。

## 如实披露：次级指标变化（非验收口径）

- **提取 NFR（噪音零产出）0.75 → 0.0**：本轮 4/4 条噪音（"嗯嗯好的收到"等）被真提取产出摘要泄漏（基线 1/4）。单轮小样本、n=4、供应商非确定性显著，不据此下因果结论；语言规则使提取对任何输入都更「有话可说」是候选解释之一。不计入 retrieve 达标判定，移交维护者观察；如需收紧属独立后续票。

## 声明

本地小样本操作级自证，不外推为普适效果，不与外部榜单横比。真轮约 34 次 chat 真调用，供应商高峰期探活曾出现 639.9s 长尾（重试后成功），完成版无降级。
