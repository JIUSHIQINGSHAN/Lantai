# 最小复现实验留档：提取语言漂移 → 中文查询双通道失配（v0.25 窗口票 02 步骤 1）

- 日期：2026-10-27 23:40–23:56（本地）
- 脚本：`run_probe.py`（可复跑，用法见其 docstring）；机器可读结果：`probe_results.json`；控制台原样输出：`probe_console.log`
- 口径钉板：`Qwen/Qwen3-8B` @ `https://api.siliconflow.cn/v1`，temperature 0.2（`lantai/llm/client.py` chat_json 硬编码）；探活延迟 32.4s，无 429/降级。数据隔离：`staged_deterministic_env`（本目录 `eval-data/`，独立于宿主真实库）。
- 诚实护栏：所有提取样本均非 fallback（`fallback_suspect` 三征共现检验全 false），延迟见 `probe_results.json`。

## 一、输入

- 环节①③（真 LLM 现场提取）：基线留证中确认漂移的 3 条中文 utterance（`lantai/eval/staged.py` 语料原样）：
  - u1「用户的主数据库是 PostgreSQL 15，部署在公司内网」
  - u5「用户对花生过敏」
  - u6「用户每周三下午开团队例会」
- 环节②（确定性，无 LLM）：基线真实查询 q_db「用户的主数据库是什么」、q_allergy「用户对花生过敏吗」；文档面取两份——基线留证 run1 的英文摘要（`../e1_extraction_consistency.jsonl` 逐字原样）与源中文 utterance（阳性对照）。交叠算法与 harness 写入侧同款：FTS 面字符 3-gram 交集计数、向量面 `lantai/eval/deterministic_embed.py`（512 维 sha256 3-gram hash）余弦。

## 二、输出（摘要，全量见 probe_results.json）

### 环节①：现行 prompt 真提取 —— 英文漂移现场复现（3/3）

| sid | CJK 占比 | 摘要（原样截取） |
|---|---|---|
| u1 | 0.0 | The main database is PostgreSQL 15, deployed within the company's internal network. |
| u5 | 0.0 | The user has a peanut allergy, which requires careful management to avoid allergic reactio… |
| u6 | 0.0 | The user has a team meeting scheduled every Wednesday afternoon. This is a recurring routi… |

与基线留证（2026-10-26）同形态：u1 甚至与 run1 逐字一致——漂移是稳定行为，不是偶发。

### 环节②：双通道失配（确定性，同一时刻可复算）

| 查询 | 对英文摘要 gram 交叠 | 对英文摘要余弦 | 对中文对照 gram 交叠 | 对中文对照余弦 |
|---|---|---|---|---|
| q_db | **0** | 0.1759 | 6 | 0.5164 |
| q_allergy | **0** | 0.1282 | 5 | 0.9129 |

判读：FTS/BM25 通道对英文语料**零交叠**（通道死）；向量通道英文信号仅为中文对照的 1/3–1/7（512 维 hash 有碰撞噪声，英文余弦绝对值不趋零，但排序上被中文语料压倒）。端到端排序后果由不 mock 冒烟测试钉死：`tests/test_extract_language_lock.py::test_language_drift_breaks_recall` 用基线留证英文摘要回放 run_staged_eval 全六段（真 SQLite+FTS5+Chroma+hybrid_search），精确复现基线 retrieve 0.25（miss 恰为 q_db/q_allergy）。

### 环节③：语言锁定 prompt 真提取 —— 摘要回中文（3/3）

| sid | CJK 占比 | 摘要（原样截取） |
|---|---|---|
| u1 | 0.71 | 该文本简要说明用户的主数据库为PostgreSQL 15，并明确指出其部署位置为公司内网。 |
| u5 | 1.0 | 用户对花生过敏，这一信息表明其在接触花生或相关制品时可能引发不良反应。 |
| u6 | 1.0 | 用户每周三下午定期召开团队例会，这是其固定的会议安排。 |

锁定文案与入仓修改稿（`lantai/llm/prompts.py` EXTRACT_SYS）逐字一致（本脚本 `LOCKED_EXTRACT_SYS` 常量即该文案）。

## 三、结论

1. **因果链证实**：「中文 utterance → Qwen3-8B 英文摘要（现行 EXTRACT_SYS 无语言指令）→ 中文查询 FTS 零交叠 + 向量信号坍缩 → retrieve 段 0.25」三环节全部成立（环节①③真 LLM 各 3 次调用，环节②确定性可复算，端到端由不 mock 冒烟测试钉死）。
2. **语言锁定 prompt 有效**：在 EXTRACT_SYS 增加一条语言规则后，同一批 utterance 的摘要全部回到中文（3/3，含中英混排的 u1 亦保持中文主体），查询-语料同语恢复。
3. 判据勘误留痕：`channel_mismatch_confirmed` 初版用「英文余弦 < 0.05」绝对阈值，对 512 维 hash 碰撞噪声定得过紧而误报 false；已改为「gram 零交叠 + 英文余弦弱于中文对照」并由同一份原始数据重算（见 probe_results.json verdict_recompute_note），原始数据未重采。

> **并行实验交叉引用（2026-10-28 00:50 补记，含 E1 裁决结果）**：另一并行会话在同一工作区就同一票做了四臂根因实验（`docs/benchmarks/e1e2-lang-drift-2026-10/root-cause-exp.jsonl`，16 记录）：其 lang_locked_prompt（英文 system 规则）臂 0/4 英文、v3_zh_user_frame（中文 user 任务框架）臂 2/2 中文、post_fix（合并态）臂 4/4 中文——与本实验环节③（英文 system 规则 3/3 中文）结论相反。两份小样本对「system 规则单独是否充分」不可裁决（n=3 vs n=4，供应商非确定性显著）。**E1 全量真轮复跑已给出管道级裁决：合并态（中文 user 框架 + 双语言规则行）真轮 retrieve 0.75 对基线 0.25，达标（≥0.75），见 `../lang-lock-rerun-2026-10/RERUN-REPORT.md`；「system 规则单独是否充分」仍为悬案，维持合并态即已实测形态。** 两实验的 baseline 臂结论一致：漂移真实存在且非确定性（其 baseline u1 亦出现过 1 次中文）。

## 四、两方案比较与选择（票 02 验收口径 2）

| 维度 | 方案甲：语言锁定 prompt | 方案乙：双语索引 |
|---|---|---|
| 改动面 | `lantai/llm/prompts.py` EXTRACT_SYS 增一条语言规则（1 行文案）；零 schema 变更 | 索引段为每条记忆生成第二语言副本并双写：触及提取/索引边界、FTS 与向量双通道路径、可能的元数据/schema 扩展 |
| 运行时代价 | 零（无新增调用、无新增延迟） | 每条记忆入库多一次真 LLM 翻译调用（基线实测提取段 25–122s/次量级），失败模式新增：翻译失败/翻译漂移/双副本一致性 |
| 侵入性 | 极低：不动存储与索引代码路径 | 高：索引口径本身被改动 |
| 对本票口径的影响 | 不影响与 0.25 基线的可比性（「只换代码不换口径」） | 检索索引内容构成变化，跨票数字对比引入新自由度 |
| 效果边界（本实验实测） | 中文源+中文查询场景恢复到管道上界（环节③+冒烟 0.75） | 理论上跨语言查询均命中，但本票无该场景考题 |
| 效果实测状态（立项时均未实测） | **已实测有效**（本实验环节③） | 未实测 |

**选择：方案甲（语言锁定 prompt）。** 理由：两方案效果在本票验收场景（中文源+中文查询）下等价（甲实测已达替身轮 0.75 的管道上界）；甲代价近零、侵入性极低、不触碰索引口径，乙的额外翻译调用与索引面改动在本票无对应需求且破坏与 0.25 基线的同口径可比。甲的已知边界如实记录：查询语言与源文本语言不一致的场景（中文宿主 + 英文源）仍会失配，若未来需要跨语言召回再立项双语索引。

## 五、验证钩子

- `tests/test_extract_language_lock.py`（4 个不 mock 冒烟用例）：prompt 钉板、漂移→0.25 复现、锁定→0.75 上界、extract_candidate 直调（锁定 prompt 下发 + 降级兜底）。
- E1 复跑：`../lang-lock-rerun-2026-10/`（与 0.25 基线同口径对照，见其 REPORT.md）。
