# E1 阶段化评测体系规范（操作级自证 v1）

> **状态**：规范稿（roadmap-v2 P0-3 立项依据文档，实现走票据 `.scratch/roadmap-v2-execution/issues/03-p0-staged-eval-e1.md`）
> **日期**：2026-09-25
> **立项依据**：`docs/research/memory-landscape-2026-09/roadmap-v2.md:16`（P0-3 验收口径：**每步有独立指标+样本数；HaluMem 式阶段归因**）
> **调研依据**：`docs/research/memory-landscape-2026-09/synthesis.md:11`（LoCoMo 信任危机后评测转向操作级/阶段级归因与能力分层，兰台应自证而非刷榜）；`docs/research/memory-landscape-2026-09/iterations/iter-09.md:9-12`（LoCoMo 答案键 6.4% 错误；HaluMem arXiv 2511.03506 按操作阶段定位记忆幻觉）
> **术语纪律**：本文档**不登记任何新命名**（继承调研纪律「本报告不登记新功能名」）。文中出现的中文术语——沙汰、校雠、锦囊、樊篱、拾遗、辨域、笔削、更漏、潜移、披沙——均已登记于根目录 `CONTEXT.md` 词汇表并各有 ADR 出处；阶段名（提取/闸门/入库/索引/召回/注入）与失败分类名（§3）为普通描述词与英文技术标识，不属于命名体系管辖的正式中文名。

---

## 1. 评测目标与设计原则

### 1.1 背景与动机：为什么不刷榜

- **LoCoMo 信任危机**：答案键 6.4% 错误（2025 审计）+ judge 争议引发厂商公开互撕（iter-09.md:9）；端到端 QA 总分已失去公信力（iter-09.md:20）。
- **厂商自报分数的共同漏洞**：只在自家平台跑、judge 不公开、**不报写精度与删除验证**——「写入精度/遗忘质量/删除边界」仍是基准荒地（iter-09.md:21）。
- **兰台对策**：操作级自证——把「提取→闸门→入库→索引→召回→注入」每步的失败率、幻觉率、陈旧率**分开计量**（对齐 HaluMem 方法论，iter-09.md:22）。
- **现状缺口**：`lantai/eval/runner.py:41` `run_dry_run` 只有查询级两层计分（召回+回答），单条失败只记 error 计数（`runner.py:136-138`），一次失败无法归因到「提取错了、闸门拒了、索引丢了还是召回没中」（`.scratch/roadmap-v2-execution/spec.md` 现状基线 #3）。

### 1.2 三大设计原则

**原则一：操作级阶段解耦（条件化评测）**

六阶段按数据流串联，但**计分彼此条件化**：第 k+1 段只对「第 k 段对账通过」的样本子集计分。上游失败样本不进入下游分母——失败只计入其首错段，**不污染其他段的成功率**（票据交付 3「单段失败只计入该段归因，不污染其他段」）。

- 依此原则，索引段的判据必须是**库内直查**（FTS 行存在性 + 向量库按 id 直查），不得以「检索是否命中」代判——否则召回失败会被错记成索引失败，归因失真。
- 阶段间传递的中间产物（候选、决策、记忆条、索引行、检索结果）逐段留痕，任何一段的输出都可独立复核。

**原则二：失败因果归因（首错归因 + 预埋锚点）**

- **首错归因**：每条样本沿链找到**第一个可观测偏离预期的位置**归因（§3 判定顺序），跨段传播只作 `root_cause_chain` 附注，不改首错归属。
- **预埋锚点**：锚点语料中**预埋已知失败**（≥1 条闸门必拒、≥1 条索引前删档、≥1 条改写零召回、≥1 条围栏逃逸载荷，票据 TDD 口径），报告必须把每一条归到正确阶段与归因桶、不串段——这是 E1 的核心验收，比任何数值阈值都硬。
- 失败桶命名与 HaluMem 四型幻觉（捏造/错误/冲突/遗漏）建立映射（§3.3）。

**原则三：确定性数据锚点（可复现优于可宣称）**

对齐 2026-09-18 调研评测协议（`docs/research/agent-memory-development-directions-2026-09.md:32`）：**固定题目、数据版本、模型、提示词、token 预算与随机种子**；按会话逐步摄取，禁止未来轮次泄漏。

- 语料为本地固定锚点集（§4.1），带逐条预期标签（expect dict），版本化 + 内容 hash 入报告；
- 确定性路径（闸门规则、FTS、字段校验、樊篱）走真实实现，断言可逐字符复核；
- 外部 LLM 段（提取、闸门中带、judge）允许替身/选配 judge，但**替身须走 `chat_json` 同一接口形状**（`lantai/llm/client.py:24`），且闸门对替身输出的校验逻辑必须真实执行（票据冒烟口径）；
- 门槛分三档（§2.0 表）：**确定门**（确定性路径，必须达成，进 CI 门禁）/ **报告型**（LLM 参与段，只报告与基线对比，不设硬阈值——阈值均为建议而非文献定论）/ **扩展位**（依赖未落地能力，如实标 `not_applicable`，不编造数值）。

### 1.3 诚实性纪律（继承既有评测契约）

- 无数据返回 `None`、绝不编造（`lantai/eval/metrics.py:1-7` 约束原文）；弱标注缺席时 weak_hit_rate 诚实标 unavailable 的先例（`runner.py:162-166`）。
- 回答层（`rule_judge`/`llm_judge`，`lantai/eval/answer_quality.py:18,47`）**不计入六段成败**：任务成功可能来自模型能力而非记忆系统（2026-09-18 调研主线 C 结论），E1 只在报告附录作下游质量参考。
- 自评结果**不直接调参**（票据边界；调参走既有参数治理轨道）。
- 所有对外数字标注 claim 属性：本报告为本地小样本自证，不外推为普适效果（synthesis.md:66 同款声明）。

### 1.4 与既有评测资产的关系

| 资产 | 关系 |
|---|---|
| dry-run 两层计分（94 样本，召回层+回答层，commit `2a4b8172`） | **并存不互斥**。召回层指标定义不动；E1 复跑必须数值一致（§4.3 对照校验） |
| 遗忘质量六门（`lantai/eval/forgetting_quality.py:170` `evaluate_forgetting_quality`，门禁 `lantai/eval/offline.py:113` `check_gates`，基线 `docs/memory-quality/baseline-2026-09-19.md`） | 时效类两门（temporal_order / superseded_order）**并入 E1 ⑤召回**；种子→检索→清理的确定性用例风格是 E1 锚点语料的范式来源 |
| 弱标注链（`RetrievalEvent` + `log_retrieval`/`backfill_used_ids`，`lantai/models/tables.py:520`、`lantai/observability/retrieval_log.py:50,106`） | ⑤召回的事件留痕底座；⑥注入的回执对账对象 |
| 笔削确定性测试（`tests/test_bixiao_deterministic.py`） | 三面真实（SQLite+FTS5+内嵌 Chroma）+ 确定性替身 embed（sha256 字符 3-gram）的技术范式来源，E1 冒烟直接沿用 |
| E2 确定性用例（「更新后还引用旧值吗」「撤回后禁用命中=0」「删除后全介质覆盖」，iter-09.md:26） | E1 是地基：E2 用例在 E1 阶段框架上以全链锚点语料形式追加，不另起炉灶 |
| E3 外部锚点（LongMemEval-S / MemoryAgentBench 子集，P2-3 另票） | **不在 E1 范围**（票据边界 §6） |

---

## 2. 六阶段评估链路与精确指标定义

### 2.0 链路总览

```
 utterance（锚点语料）
   │ ①提取 Extract      ingestion/dialogue.py:57 ingest_dialogue → MemoryCandidate
   │ ②闸门 Gate         gate/prefilter.py:121 + gate/dedup.py:15 + gate/relation.py:212 + gate/decision.py:41
   │ ③入库 Store        过闸候选 → evolution/proposer.py propose_from_candidate 生成 MemoryProposal
   │                    （生产路径 lantai/services/candidate_service.py:90-103，apply_proposal 以
   │                     proposal_id 为入参、不能直接吃候选）→ evolution/promoter.py:37 apply_proposal → MemoryItem
   │ ④索引 Index        storage/fts.py:35 sync_fts + retrieval/hybrid.py:703 index_memory_item → memory_fts + 向量库
   │ ⑤召回 Retrieve     retrieval/hybrid.py:285 hybrid_search
   │ ⑥注入 Inject       llm/fence.py:45 wrap_as_data → 宿主出口 → 回执对账
   ▼
 EvalRun 阶段结果落库 + 阶段化报告（§5）
```

| 阶段 | 被测对象（真实实现，不 mock） | 输入→输出 | 门槛类型 |
|---|---|---|---|
| ① 提取 | 提取管线（LLM 可替身，校验真实） | utterance → `MemoryCandidate`（facts/summary/confidence/provenance） | 报告型 |
| ② 闸门 | `prefilter` + `dedup` + `relation` + `decision` 全真实 | 候选 → 放行/拒绝/待审/三态 | 报告型（规则路径的回退行为单独计数） |
| ③ 入库 | `apply_proposal` 全真实 | 过闸候选 → `MemoryItem` | 确定门（覆盖率 100%、合法性拒写行为 100%） |
| ④ 索引 | 真 SQLite + FTS5 trigram + 真向量库（embed 可确定性替身） | MemoryItem → memory_fts 行 + 向量行 | 确定门（一致性率 1.0） |
| ⑤ 召回 | 真 `hybrid_search`（rerank 可关档，拾遗路径真实可达） | 锚点查询 → top-k | 报告型（时效两门为确定门，数值不得回退） |
| ⑥ 注入 | 真 `wrap_as_data`/`neutralize_fence_escapes` 全出口 | 检索结果 → 围栏注入串 + 回执 | 确定门（隔离率 1.0）；回执为扩展位→P0-4 |

**样本记号**：N = 该段实际对账样本数（= 进入本段的上游通过样本）；各指标分母均为 N 或其明示子集，样本数与指标**成对报告**（P0-3 验收口径「每步有独立指标+样本数」）。

### 2.1 ① 提取阶段（Extract）

**样本单位**：锚点语料中的一条 utterance i，带预标注预期事实集 G_i = {(主体, 谓词, 事实值)} 或「噪音」标记。

**FER（事实抽取率，Fact Extraction Rate）**

- 定义：抽出的候选事实中与预期事实匹配的比例，按 utterance 汇总。
- 公式：`FER = Σ_i |E_i ∩ G_i| / Σ_i |G_i|`，其中 E_i 为第 i 条实际抽出的事实集。
- **匹配判据（v1 确定性口径，保证跨实现可复现）**：三元组逐元归一化后**逐字符相等**即匹配。
  归一化规则（确定性，无裁量）：去首尾空白 → 全角转半角 → 英文小写 → 去中英文标点。
  **v1 不做谓词同义判定**（「谓词核心」「同义改写」仅限数值/日期/专名之外的自然语言差异面，
  v1 同义词表为空——宁 miss 不脏写）；扩展同义词表必须版本化登记并随语料快照冻结入报告，
  未登记版本一律按严格逐字符口径判。数值、日期、专名在任何版本下都必须逐字符一致
  （错一个字符按错误抽取计，进幻觉率）。
- 遗漏（G_i 中未被抽出的事实）计入本指标分母的 miss，**不进幻觉率**（不双计，映射 HaluMem「遗漏」型，见 §3.3）。
- 门槛类型：报告型。锚点要求：每条事实型 utterance 的 G_i 逐条人工预标并随语料版本冻结。

**NFR（口语噪音过滤率，Noise Filtering Rate）**

- 定义：预标注为「无记忆价值」的噪音 utterance（纯闲聊、寒暄、指令回显）中，被正确**零事实产出**（或明确闲聊标记）的比例。
- 公式：`NFR = 正确零产出的噪音语料数 / 噪音语料总数`。
- 漏过 = 噪音语料被抽出 ≥1 条事实（该条事实同时进幻觉率的「捏造」候选核对——若其内容源内无依据）。
- 边界注记：沙汰（`gate/prefilter.py:121` `relevance_check`）在闸门段执行；本指标测的是**提取段**的产出面（`ingestion/dialogue.py:49` `_is_chitchat` 与 fastpath 闲聊分派），两者在报告中分属 ①② 两段、各自计量，不合并。
- 门槛类型：报告型。

**提取幻觉率（Extraction Hallucination Rate）**

- 定义：抽出事实中**源内无依据或源依据被歪曲**的比例，按 HaluMem 三亚型分类（遗漏归 FER）：
  - `fabrication` 捏造：源文本无任何依据的事实；
  - `misextraction` 错误：源有信息但抽错（数值/主体/时间张冠李戴）；
  - `conflict` 冲突：与同会话其他轮次信息矛盾地抽取。
- 公式：`幻觉率 = (fabrication + misextraction + conflict) / Σ_i |E_i|`；分母为 0 时如实返回 `None`（不编造 0）。
- 门槛类型：报告型（LLM 段不设硬阈值；与上一版本语料快照对比趋势）。

### 2.2 ② 闸门阶段（Gate）

**样本单位**：一条进入闸门的候选（来自 ① 实际产出，或锚点预构造的定标候选）。预标注标签 ∈ {`keep`（应放行）, `sift`（应沙汰）, `reject`（应拒绝）, `pending`（应入锦囊待审）} 及三态标签 ∈ {`merge`（近义合并）, `update`（同主体更新）, `insert`（新事实插入）}。

**信噪分离准确率（沙汰准度，Sift Accuracy）**

- 定义：沙汰地板过滤（`lantai/gate/prefilter.py:121` `relevance_check`）对 keep/sift 二分类的正确率。
- 公式：`沙汰准度 = (正确放行 + 正确沙汰) / 二分类样本总数`。
- 附报两个方向性指标（分开报、不合并，误杀比漏汰代价高）：
  - `误杀率` = keep 被沙汰数 / keep 总数；
  - `漏汰率` = sift 被放行数 / sift 总数。
- 闸门整体决策（含 reject/pending，`lantai/gate/decision.py:41` `decide`：低置信度拒 `GATE_MIN_EXTRACTOR_CONF`、硬冲突 `archive_conflict`、锦囊待审）另报四分类混淆矩阵 `keep/sift/reject/pending`。
- 门槛类型：报告型（规则路径确定性可复现；LLM 参与部分随版本对比）。

**校雠三态判别准确率（Relation Accuracy）**

- 定义：`find_similar`（`lantai/gate/dedup.py:15`，余弦预判 merge/update/undecided/insert）+ `classify_relation`（`lantai/gate/relation.py:212`，锚点结构判类）组合输出与预标注三态的一致率。
- 公式：`三态准确率 = 判对数 / 三态样本总数`；附 3×3 混淆矩阵（merge/update/insert）。
- 确定性回退单列：judge 缺席/异常/非法返回值一律回退 `insert`（宁 miss 不脏写，`relation.py:200` `_judge_or_insert`）。预标注非 insert 而实际因回退得 insert 的样本计入独立桶 **`gate_fallback_insert`**，不与「真实判错 insert」混计——两者的修复动作完全不同（前者是 judge 供给问题，后者是判别质量问题）。
- 阈值锚点：判类阈值 `DEDUP_ANCHOR_HIGH=0.6` / `DEDUP_ANCHOR_LOW=0.3`（`lantai/core/settings.py:223-224`）与余弦预判 `DEDUP_MERGE_THRESHOLD=0.90` / `DEDUP_UPDATE_THRESHOLD=0.65` / `DEDUP_PRESCREEN_MERGE=0.95`（`settings.py:216-219`）随 param_snapshot 入报告，保证结果可归因到参数版本。
- 门槛类型：报告型（规则路径 + 确定性回退行为为确定子集：回退行为必须与 `relation.py` 实现逐例一致）。

### 2.3 ③ 入库阶段（Store）

**样本单位**：一条已过闸候选，执行 `apply_proposal`（`lantai/evolution/promoter.py:37`）。
**胶水步骤（harness 必须复现生产路径）**：`apply_proposal` 以 `proposal_id` 为入参，不能直接吃
候选——harness 须先经 `propose_from_candidate`（`lantai/evolution/proposer.py`）生成
MemoryProposal，两步合起来才是生产入库路径（`lantai/services/candidate_service.py:90-103`
`review_candidate` 实证），跳过提案步骤的回放不是被测生产路径。

**溯源元数据覆盖率（Provenance Coverage）**

- 定义：入库成功的 MemoryItem 中，溯源字段**齐备且可回溯**的比例。齐备清单（字段依据 `lantai/models/tables.py:73-100` MemoryCandidate 与 `:103-159` MemoryItem）：
  1. `provenance.prompt`（提取提示词标识，`dialogue.py:169-174` 五型分派）；
  2. `provenance.model` 或确定性来源标记（fastpath/导入路径无 LLM，须有对应来源标识）；
  3. `document_id` 可回溯到 RawDocument（content_hash 去重链）；
  4. 会话来源：`session_id` 落列 + `provenance.origin_session_id`/`origin_turn`/`origin_source`（`dialogue.py:175-184`）——有会话来源时为必选项；
  5. `source_ids`（关联依据，如适用）。
- 公式：`覆盖率 = 齐备条数 / 入库成功条数`。
- 门槛类型：**确定门 = 1.0**。依据：30 天验收「支持的宿主冒烟中来源可追溯率 100%」（`agent-memory-development-directions-2026-09.md:62`）；历史缺失单列，不冒充覆盖率。

**字段合法性通过率（Schema Validity Pass Rate）**

- 定义：入库尝试中字段全部合法的比例；非法输入必须**拒写并留痕**（宁 miss 不脏写），不静默修正。
- 合法性判据（逐字段）：`id/content` 非空；`lane/domain/tier/status/lifecycle_status/decay_class` 枚举值合法（`tables.py:127-153`）；`confidence/importance/decay_score ∈ [0,1]`；JSON 列可反序列化；外键可解析（`document_id`、`source_ids`、`superseded_by`）；时间字段可解析。
- 公式：`通过率 = 合法入库数 / 入库尝试数`；非法被拒的样本计入 `store_invalid_rejected`（这是**正确行为**，单独正名计数，不算失败）。
- 门槛类型：**确定门**——合法样本通过率 = 1.0；非法样本拒写率 = 1.0 且必留审计痕迹。

### 2.4 ④ 索引阶段（Index）

**样本单位**：一条已入库 MemoryItem。判据全部走**库内直查**（原则一），不经检索路径。

**FTS 事务一致性率（FTS Transactional Consistency Rate）**

- 定义：SQLite 行与 `memory_fts` 行（FTS5 trigram，`lantai/storage/fts.py:20-25`）一一对应且内容一致的比例。同事务同步契约见 `sync_fts`（`fts.py:35-50`，ADR-0008：强一致，不吞异常）。
- 一致性判据（对每条 id）：`SQLite 存在 ∧ FTS 行存在 ∧ content 一致`；或 `SQLite 不存在 ∧ FTS 行不存在`（删除面同步）。两者之一成立为一致。
- 公式：`FTS 一致性率 = 一致条数 / 对账条数`。
- 门槛类型：**确定门 = 1.0**。任何 <1.0 直接fail并输出失同步 id 清单。

**向量空间映射成功率（Vector Mapping Success Rate）**

- 定义：向量库写入且**按 id 可直查回自身**的比例（`lantai/storage/vector_store.py:23` ChromaVectorStore，`:43` add；生产入口 `retrieval/hybrid.py:703` `index_memory_item`）。
- 判成功：`vector_store.add` 后以同 id 查询 top-1 命中自身；embed 维度与库配置一致。
  **语料前置约束**：「查回自身」要求该条与全库其他条目的字符 3-gram 多重集互异——锚点语料
  构造时必须逐条校验 gram 分布互异（哈希替身 embed 下共享 gram 越多余弦越近），存在同 gram
  邻居时 top-1 可能合理地返回邻居而非自身，属语料缺陷而非被测对象失败。
- 失败分桶（不混计）：`embed_failed`（外部网络，如实单列）/ `vector_add_error`（存储异常）/ `dim_mismatch`（维度不匹配）/ `vector_not_self`（写入后查不回自身）。
- 门槛类型：**确定门 = 1.0**（替身 embed 用 sha256 字符 3-gram 确定性向量，跨进程稳定不依赖 PYTHONHASHSEED，范式见 `tests/test_bixiao_deterministic.py` 文档声明）。

### 2.5 ⑤ 召回阶段（Retrieve）

**样本单位**：一条锚点查询 q，带预期记忆 id 集 T_q；时效类查询另带视图标签（当前态/历史态）。

**Top-K 准确率（Hit@K）**

- 定义：top-k 结果命中至少一个预期记忆的查询占比（主指标，K 默认 5，与既有 dry-run `top_k=5` 对齐，`runner.py:45`）。
- 公式：`Hit@K = |{q : R_q ∩ T_q ≠ ∅}| / |Q|`，R_q 为实际 top-k id 集。
- 附报 `Recall@K = mean(|R_q ∩ T_q| / |T_q|)`（对齐 60 天验收「证据 Recall@5 单独报告」口径，`agent-memory-development-directions-2026-09.md:63`）。
- 门槛类型：报告型（与既有两层基线对比；门数值不动，见 §4.3）。

**倒数排名（MRR，Mean Reciprocal Rank）**

- 定义：首个预期记忆排名倒数的均值。
- 公式：`MRR = mean_q( 1 / min_rank(R_q ∩ T_q) )`；无命中按 0 计入（与 Hit@K 的分母一致，全零命中时如实报 0 并同时报 `zero_recall` 桶计数）。
- 门槛类型：报告型。

**时效视图过滤准确率（Temporal View Filtering Accuracy）**

- 定义：时效类查询中，视图过滤行为与预期一致的比例。分三个子判据（对应三条实现锚点）：
  1. `superseded_excluded`：当前态不返回已更替记忆（`hybrid.py:208` `_apply_supersedes_order`；`tables.py:150-156` `lifecycle_status`/`superseded_by`）；
  2. `temporal_order`：时序类查询返回符合时间语义的排序；
  3. `stale_excluded`：衰减/过期记忆不挤占当前视图（基线六门之 stale_hit_rate，`docs/memory-quality/baseline-2026-09-19.md`）。
- 公式：`时效过滤准确率 = 全部适用子判据均成立的时效类查询数 / 时效类查询总数`。
  **口径（按查询计，修复维度失配）**：一条查询的三条子判据中，不适用的跳过；**任一适用子判据
  不成立即整条查询计错**——分子分母同为查询数，不出现「分子按子判据、分母按查询」的维度失配。
- 扩展位：`event_time` 当前/历史双视图（as-of）判据为更漏（ADR-0048，P1-1）落地后的追加项；E1 阶段该子判据如实标 `not_applicable`（`MemoryItem` 现仅有 `created_at/updated_at/valid_from/valid_to`，`tables.py:141-143`，无事件时间轴——现状见 `.scratch/roadmap-v2-execution/spec.md` 基线 #5）。
- 门槛类型：**确定门**——temporal_order_accuracy 与 superseded_order_accuracy 不得低于既有基线 1.0（门禁数值不动纪律）；新增子判据报告型。

### 2.6 ⑥ 注入阶段（Inject）

**样本单位**：一次注入 = 召回通过样本的检索结果 → 樊篱包裹 → 宿主出口 → 回执对账。

**樊篱围栏隔离率（Fence Isolation Rate）**

- 定义：注入串通过全部围栏校验项的比例。校验项（实现锚点 `lantai/llm/fence.py`）：
  1. **包裹完整**：每条正文被 `wrap_as_data`（`fence.py:45`）包裹，`<memory_data>` 开闭标记成对（`:20-21`）；
  2. **不可逃逸**：正文内闭合标记变体已中性化——`neutralize_fence_escapes`（`fence.py:30-37`，大小写/空白变体正则 `:24`）处理后，正文中不存在未转义的 `</memory_data>`；
  3. **声明行就位**：按出口契约附加 `FENCE_DECLARATION`（`fence.py:25-27`）——shell_hook 注入串头部 / MCP search `data_fence_notice` / to_prompt 头部（`fence.py:6-10` 契约清单）；
  4. **正文不出位**：对注入串做解析回放，正文只出现在 data 位内，围栏标记之外无正文片段泄漏。
- 对抗锚点：语料预埋含 `</memory_data>`、大小写/空白变体、「忽略以上指令」类正文的记忆（中性化后原样可见属**正确行为**——如实声明这是纵深防御与结构隔离，不宣称绝对防注入，`fence.py:12-13`）。
- 公式：`隔离率 = 通过全部校验项的注入数 / 注入尝试数`；失败分桶 `fence_unwrapped` / `fence_escape` / `fence_declaration_missing` / `fence_content_leak`。
- 门槛类型：**确定门 = 1.0**（有限隔离用例零泄漏，不外推为绝对安全——`agent-memory-development-directions-2026-09.md:62`）。

**宿主回执签名验证率（Receipt Verification Rate）**

- 定义（在 P0-4 回执契约上定义，当前为**扩展位**）：注入尝试中收到**绑定字段可验证**回执的比例。可验证 = 回执含 `event_id` + 注入记忆 id 集 + `session_id`/`turn` 绑定，且与 `RetrievalEvent`（`tables.py:520-547`）对账一致。
- 现状如实声明（**不宣称已有密码学签名**）：回执链一等化是 P0-4（票据 04）范围；现状仅有 used_ids 弱标注回填（`hermes-plugin/lantai-hook/__init__.py:213` `_call_backfill`，失败静默；服务端 `lantai/observability/retrieval_log.py:106` `backfill_used_ids`）。E1 阶段本指标：
  - 受控宿主冒烟（Hermes 插件 + `scripts/shell_hook.py`，宿主矩阵单点现状）可达时：按上述绑定对账口径计量；
  - 回执通道缺席/超时：该样本计 `receipt_missing` 桶，指标在**有回执样本子集**上报告，并在报告元信息中标注 `unavailable` 与通道状态——不编造 0。
- 公式：`回执验证率 = 绑定一致回执数 / 有回执样本数`（附 `receipt_missing` 计数与占比）。
- 门槛类型：**扩展位**（P0-4 落地后升级为确定门：受控宿主冒烟中来源可追溯率 100%，`agent-memory-development-directions-2026-09.md:62`）。

---

## 3. 阶段失败归因分类树

### 3.0 归因原则

1. **首错归因**：样本沿链 ①→⑥ 找第一个可观测偏离预期处归因；其后各段对该样本**不计分**（条件化评测的直接推论）。
2. **不串段**：一段的失败桶只出现在该段的 failure_buckets 里；跨段传播以 `root_cause_chain`（如 `["extraction_fault:missing", "recall_miss:zero"]`）附注在首错段条目上，供人读，不参与各段成功率计算。
3. **单条失败不中断**：任何一段单样本异常记录后继续（继承 `runner.py:136-138` 纪律）。
4. **unknown 桶诚实外溢**：无法归入六类的失败进 `unknown` 桶并附原始证据，禁止硬塞进已知桶凑数。

### 3.1 六类失败定义

| # | 失败类 | 定义（首错位置） | 触发信号 | 判定规则 | 证据字段 | 对应指标 |
|---|---|---|---|---|---|---|
| 1 | **Extraction Fault**（提取失真） | ① 段：候选产出与锚点预期事实集偏离 | 预期事实 miss / 捏造 / 抽错 / 噪音漏过 | `E_i ≠ G_i`（缺、多、错任一）；噪音 utterance 产出 ≥1 事实 | `MemoryCandidate.summary/claims/extractor_confidence/provenance` + 锚点 expect | FER、NFR、提取幻觉率 |
| 2 | **Gate Rejection**（闸门误判） | ② 段：闸门决策与锚点预期决策不符 | 应放行被拒/被沙汰（误杀）；应拒未拒（漏汰）；三态误判；确定性回退致错 | `decide()` 输出 ≠ expect；`find_similar`+`classify_relation` 输出 ≠ 三态标签 | `decision.reason`、`ConflictEvent`、沙汰日志、相似度分 | 沙汰准度、三态准确率 |
| 3 | **Store Crash**（入库崩坏） | ③ 段：过闸候选未落成合法 MemoryItem | `apply_proposal` 异常；字段校验失败被拒写；外键悬空 | 过闸样本在 MemoryItem 中无对应行，或行存在但 §2.3 合法性判据任一不成立 | `MemoryProposal`、异常留痕、拒写审计 | 溯源覆盖率、字段合法性 |
| 4 | **Index Desync**（索引失同步） | ④ 段：三面索引与库内状态不一致 | FTS 行缺失/多余/内容不一致；向量 add 失败/查不回自身；维度不匹配 | §2.4 库内直查判据任一不成立（**不经检索路径判定**） | `memory_fts` 行、向量库按 id 直查、`sync_fts` 异常 | FTS 一致性率、向量映射成功率 |
| 5 | **Recall Miss**（召回脱靶） | ⑤ 段：三面对账通过但检索未达标 | hit@k=0；MRR 排名过深；时效视图误包含/误排除 | `R_q ∩ T_q = ∅` 或排名/视图判据不成立，**且该样本 ④ 段对账已通过**（否则归 Index Desync） | `RetrievalEvent.result_ids/result_scores`、`search_trace`、拾遗降级标记 | Hit@K、MRR、时效过滤准确率 |
| 6 | **Injection Leak**（注入泄漏） | ⑥ 段：召回正确但注入面失守 | 围栏未包裹/可逃逸/声明缺失/正文出位/回执缺失或绑定不一致 | §2.6 任一校验项不成立；回执对账不一致 | 注入串原文、围栏校验输出、回执记录与 `RetrievalEvent` 对账差 | 隔离率、回执验证率 |

### 3.2 归因判定顺序（决策表）

对每条失败样本，按序问、首个「是」即定桶：

```
Q1 候选产出 ≠ 预期事实集？                          → Extraction Fault（附子型：missing/fabrication/misextraction/conflict/noise_leak）
Q2 闸门决策/三态 ≠ 预期？                           → Gate Rejection（附子型：false_kill/false_pass/relation_misjudge/gate_fallback_insert）
Q3 过闸候选未落成合法 MemoryItem？                  → Store Crash（附子型：apply_error/schema_invalid/fk_dangling）
Q4 三面索引直查判据任一不成立？                     → Index Desync（附子型：fts_missing/fts_content/vector_missing/vector_not_self/dim_mismatch）
Q5 检索 Hit@K/MRR/时效判据不成立？                  → Recall Miss（附子型：zero_recall/deep_rank/temporal_wrong/recall_fallback）
Q6 注入面任一校验项/回执对账不成立？                → Injection Leak（附子型：fence_unwrapped/fence_escape/fence_declaration_missing/fence_content_leak/receipt_missing/receipt_mismatch）
以上皆否但整体预期未达成？                          → unknown（附原始证据）
```

- 预埋失败锚点必须落在预期桶：闸门必拒样本→预期触发 Q2（若它先在 Q1 失败即语料构造错误，修语料不修判定）；索引前删档→Q4（验证删除面同步可见）；改写零召回→Q5（且 Q4 必须对账通过，证明是召回问题而非索引丢失）；围栏逃逸→Q6。
- **票据 TDD 验收原文**：「构造语料中预埋已知失败（1 条闸门必拒、1 条索引前删档、1 条改写零召回），报告能各归到正确阶段与归因桶，不串段」。

### 3.3 与 HaluMem 幻觉四型的映射

| HaluMem 型 | E1 归属 | 说明 |
|---|---|---|
| 捏造 fabrication | ① Extraction Fault 子型（进提取幻觉率分子） | 源内无依据的事实产出 |
| 错误 misextraction | ① Extraction Fault 子型（进提取幻觉率分子） | 数值/主体/时间张冠李戴 |
| 冲突 conflict | ① 子型；若漏检进库后在 ⑤ 显形（时效/更替错误）则首错仍在 ①（root_cause_chain 附注） | 与源内其他轮次矛盾抽取 |
| 遗漏 omission | ① FER 的 miss（**不进幻觉率**，不双计） | 预期事实未被抽出 |

---

## 4. 评测执行流程

### 4.1 锚点语料构造（`lantai/eval/` 内新增数据模块，不动在线链路）

固定小语料 **≥20 条**（票据最小交付 1），每条带 expect 标签，构成：

| 类别 | 条数 | expect 要点 |
|---|---|---|
| 普通事实 utterance | ≥6 | G_i 事实集（主体/谓词/值逐条预标） |
| 口语噪音 | ≥4 | noise=true（NFR 分母） |
| 近义重复 | ≥2 | 三态=merge |
| 同主体更新 | ≥2 | 三态=update |
| 新事实插入 | ≥2 | 三态=insert |
| 低置信度候选源 | ≥2 | 闸门=pending（锦囊）或 reject |
| 改写型查询 | ≥3 | T_q 预标；其中 ≥1 条预埋零召回 |
| 时效更替链 | ≥2 | superseded/temporal 标签 |
| 围栏逃逸载荷 | ≥1 | 正文含闭合标记变体与指令型文本 |
| 预埋失败四件套 | ≥4（含于上） | 闸门必拒 / 索引前删档 / 改写零召回 / 围栏逃逸 |

- 语料文件版本化（内容 sha256 入报告元信息）；按会话逐步摄取回放，**禁止未来轮次泄漏**（2026-09-18 评测协议）。
- 提取段 LLM 替身：走 `chat_json` 同接口形状（`lantai/llm/client.py:24`），替身输出必须真实穿过闸门校验逻辑（票据冒烟口径）。

### 4.2 六步回放（harness 于 `lantai/eval/` 新增模块；离线回放，在线链路零改动）

1. **冻结环境**：记录 git commit、param_snapshot（`default_snapshot()` + overrides）、语料版本 hash、模型/提示词版本、随机种子——五固定入 EvalRun。
2. **① 提取回放**：utterance → `ingest_dialogue` → 逐条对账 FER / NFR / 幻觉率；失败样本按 Q1 分桶。
3. **② 闸门回放**：候选 → `relevance_check`/`find_similar`/`classify_relation`/`decide` → 对账沙汰准度 / 三态准确率（含 `gate_fallback_insert` 单列）。
4. **③ 入库回放**：过闸候选 → `apply_proposal` → 对账溯源覆盖率 / 字段合法性。
5. **④ 索引回放**：MemoryItem → `sync_fts` + `index_memory_item` → 三面直查对账（FTS 一致性率 / 向量映射成功率）。
6. **⑤⑥ 召回+注入回放**：锚点查询 → `hybrid_search`（`use_rerank=False` + `LANTAI_INTENT_OFF=1` 确定性关档，范式同 `tests/test_bixiao_deterministic.py`）→ Hit@K / MRR / 时效过滤；通过样本 → `wrap_as_data` 全出口包裹 → 围栏校验 → 受控宿主回执对账（可达时）。

每段落一行结果：`stage / samples / success_rate / failure_buckets`；段内逐样本留 trace id。

### 4.3 对照校验与门禁（阶段化不改评分定义）

- 对既有 **94 样本**查询集复跑 `run_dry_run`，召回层指标必须与两层基线（`docs/memory-quality/baseline-2026-09-19.md` 及其后续版本）**逐项一致**（`jaccard_vs_baseline` 对照 + 逐指标相等断言）——票据交付 4 与 TDD 口径「阶段化复跑后既有召回层门数值不变」。
- 遗忘质量六门照常可跑（`scripts/run_forgetting_quality.py --check` → PASS），E1 并入不改其定义。
- 全量门禁零回归是 E1 合并前置（P0-1 基线：1092 passed，见 `.scratch/roadmap-v2-execution/spec.md` 基线 #9）。

### 4.4 落库与出口

- **EvalRun 扩展**：阶段结果随 EvalRun 落库（新表 `eval_stage_result` 或 EvalRun JSON 字段——**属存储 Schema 变更，走 ADR**，票据交付 2）；与既有两层计分并存不互斥。
- **报告出口**：CLI（如 `scripts/run_staged_eval.py`）输出阶段化报告：每段一行 `stage / samples / success_rate / failure_buckets` + 完整 markdown 报告（§5 模板）。

### 4.5 不 mock 冒烟边界（票据原文执行）

- 闸门/入库/索引/召回/注入段**严禁 mock**：真实 SQLite + FTS + 本地 ngram embedding + 内嵌 Chroma；假向量库只替外部网络。
- 提取段 LLM 属外部网络可替身；**替身须走 `chat_json` 同一接口形状**，闸门对替身输出的校验逻辑真实执行。
- 注入段必须经 `lantai/llm/fence.py` 真实包裹——樊篱出口全覆盖不得绕开。

---

## 5. 自证报告模板

每次 E1 运行产出一份 markdown 报告（机器可读 JSON 并存），结构固定如下：

```markdown
# 兰台 E1 阶段化自证报告

## 0. 元信息
- run_id: <erun id>            生成时间: <UTC>
- 代码版本: git <commit hash>  语料版本: <corpus_id> sha256:<hash>
- 参数快照: param_snapshot_hash=<hash>（关键阈值: DEDUP_*=…, GATE_MIN_EXTRACTOR_CONF=…, DATA_FENCE_ENABLED=…）
- 提取模型/提示词: <model id / provenance.prompt 标识或「替身:chat_json 形状」>
- 随机种子: <seed>              token 预算: <预算或 unlimited>
- claim 声明: 本报告为本地小样本操作级自证，不外推为普适效果；不与任何外部榜单横比。

## 1. 阶段总表（每步独立指标+样本数）
| 阶段 | samples | success_rate | failure_buckets（计数） |
|---|---|---|---|
| ① Extract   | N1 | x.xx | extraction.missing:n / extraction.fabrication:n / … |
| ② Gate      | N2 | x.xx | gate.false_kill:n / gate.gate_fallback_insert:n / … |
| ③ Store     | N3 | x.xx | store.apply_error:n / store.schema_invalid:n / … |
| ④ Index     | N4 | x.xx | index.fts_missing:n / index.vector_not_self:n / … |
| ⑤ Retrieve  | N5 | x.xx | recall.zero_recall:n / recall.temporal_wrong:n / … |
| ⑥ Inject    | N6 | x.xx | inject.fence_escape:n / inject.receipt_missing:n / … |
| (溢出)      | —  | —    | unknown:n（附证据） |

## 2. 分段指标明细
### ① 提取
- FER = <v>（G 总数 <g>，命中 <h>，样本 <N1>）
- NFR = <v>（噪音样本 <n>，漏过 <k>）
- 提取幻觉率 = <v 或 None>（抽出 <e> 条：fabrication <a> / misextraction <b> / conflict <c>；遗漏归 FER）
### ② 闸门
- 沙汰准度 = <v>（误杀率 <v1> / 漏汰率 <v2>；四分类矩阵 keep/sift/reject/pending）
- 校雠三态准确率 = <v>（3×3 矩阵；gate_fallback_insert 单列 <n>）
### ③ 入库
- 溯源覆盖率 = <v>【确定门 1.0 → PASS/FAIL】（缺失字段分布）
- 字段合法性 = <v>【确定门】（拒写留痕 <n> 条，全数可审计）
### ④ 索引
- FTS 事务一致性率 = <v>【确定门 1.0 → PASS/FAIL】（失同步 id 清单）
- 向量映射成功率 = <v>【确定门】（embed_failed/vector_add_error/dim_mismatch/vector_not_self 分桶）
### ⑤ 召回
- Hit@5 = <v>；Recall@5 = <v>；MRR = <v>；zero_recall 桶 <n>
- 时效过滤准确率 = <v>【temporal_order / superseded_order 确定门 → 与基线比对 PASS/FAIL】
- event_time 双视图子判据：not_applicable（更漏 ADR-0048 P1-1 未落地，不编造数值）
### ⑥ 注入
- 樊篱隔离率 = <v>【确定门 1.0 → PASS/FAIL】（四校验项逐项结果；对抗载荷 <n> 条全数中性化）
- 回执验证率 = <v 或 unavailable>（receipt_missing <n>；口径=P0-4 绑定对账，密码学签名未实现——如实声明）

## 3. 失败归因明细
- 每桶: 归因类 / 计数 / 样本 id 列表 / 证据摘引（candidate id、decision.reason、失同步 id、RetrievalEvent id…）
- 预埋锚点核对: 闸门必拒→Gate Rejection ✔/✘；索引前删档→Index Desync ✔/✘；改写零召回→Recall Miss ✔/✘；围栏逃逸→Injection Leak ✔/✘（不串段声明）
- root_cause_chain 传播链示例（不改首错归属）

## 4. 对照校验
- 94 样本复跑: 召回层各指标 vs 两层基线 = 一致/不一致（逐项列出）；遗忘质量六门 PASS/FAIL
- 与上次 E1 运行的分段差值（趋势，无硬阈值）

## 5. 边界与不做
- 样本量 <n>，仅用于发现退化，不支持显著性/普适宣称；
- 回答层（rule/llm judge）结果仅附录参考，不计入六段成败（任务成功可能来自模型能力）；
- 自评不调参；本报告不作为发布闸门替代品（发布仍走 scripts/release_check.py + 全量测试）。
```

---

## 6. 边界与不做（票据原文）

- 不做 E3 外部锚点（LongMemEval-S / MemoryAgentBench 子集，P2-3 另票）。
- 不把完整对话上传外部评测。
- 不因阶段化结果自动调参（自评不直接调权；调参走既有参数治理轨道）。
- 不宣称绝对防注入/绝对安全；不把小样本自证包装成质量结论。

## 7. 验收口径（TDD，票据原文）

1. 六段各有 samples / success_rate / failure_buckets，**缺任一段即不合格**。
2. 预埋已知失败（闸门必拒 / 索引前删档 / 改写零召回）各归正确阶段与归因桶，**不串段**。
3. 阶段化复跑后既有召回层门数值不变（评测门数值不动纪律）。
4. 全量门禁零回归。
5. 冒烟不 mock 边界符合 §4.5。

## 8. 依据与引用台账

**调研材料（本任务输入）**
- `docs/research/memory-landscape-2026-09/synthesis.md:11,25,31`（评测转向、LoCoMo 6.4%、定位声明）
- `docs/research/memory-landscape-2026-09/iterations/iter-09.md:9-12,20-27`（九基准表、E1/E2/E3 落点、HaluMem 四型）
- `docs/research/memory-landscape-2026-09/roadmap-v2.md:16`（P0-3 验收口径）
- `docs/research/agent-memory-development-directions-2026-09.md:32,38,62-63`（评测协议五固定、事件链、30/60 天验收门槛）
- `.scratch/roadmap-v2-execution/issues/03-p0-staged-eval-e1.md`（本规范实现票据：交付切片/TDD/冒烟/边界）
- `.scratch/roadmap-v2-execution/spec.md`（现状基线 #3 E1 未阶段化、#9 门禁基线）

**代码锚点（本会话逐一核读）**
- 评测既有资产：`lantai/eval/runner.py:41,136-138`、`lantai/eval/metrics.py:75`、`lantai/eval/models.py:16,31`、`lantai/eval/answer_quality.py:18,47,76`、`lantai/eval/forgetting_quality.py:170`、`lantai/eval/offline.py:57,113`、`docs/dry-run-eval-task-split.md`、`docs/memory-quality/baseline-2026-09-19.md`
- ①提取：`lantai/ingestion/dialogue.py:57,149`；`lantai/llm/client.py:24`
- ②闸门：`lantai/gate/prefilter.py:121`、`lantai/gate/dedup.py:15`、`lantai/gate/relation.py:200,212`、`lantai/gate/decision.py:41`；阈值 `lantai/core/settings.py:216-224`
- ③入库：`lantai/evolution/promoter.py:37`；表结构 `lantai/models/tables.py:73-100,103-159`（provenance/lifecycle_status/superseded_by/valid_from/valid_to）
- ④索引：`lantai/storage/fts.py:15-50,96,146`（memory_fts trigram、sync_fts 同事务）、`lantai/storage/vector_store.py:23,43,75`、`lantai/retrieval/hybrid.py:703`
- ⑤召回：`lantai/retrieval/hybrid.py:208,285,716,752`（更替排序/混合检索/时序过滤/拾遗降级）；弱标注 `lantai/models/tables.py:520`、`lantai/observability/retrieval_log.py:50,106`
- ⑥注入：`lantai/llm/fence.py:20-27,30-37,45`；围栏开关 `lantai/core/settings.py:251`；回执现状 `hermes-plugin/lantai-hook/__init__.py:213`
- 确定性范式：`tests/test_bixiao_deterministic.py`（三面真实 + sha256 3-gram 替身 embed + 确定性关档）
