# E1/E2 正式跑测报告（票 02 步骤③，2026-10-26）

## 环境钉板（模型身份）

- LLM：`Qwen/Qwen3-8B` @ `https://api.siliconflow.cn/v1`（settings.LLM_MODEL / OPENAI_BASE_URL，.env 实配）
- temperature：0.2（`lantai/llm/client.py` chat_json 硬编码，网关不回报版本号——以模型名+base_url 为身份钉板）
- embed 面：**未放真 embed**——harness 写入侧固定 512 维确定性 embed（`lantai/eval/staged.py` ④ 段），真 embed（BAAI/bge-m3，1024 维）与写入侧混维即拒查，故真 LLM 轮的「真」限定在 chat 面（提取/闸门矛盾/提案）。EMBED_MODEL 配置为 `BAAI/bge-m3`，留证于 meta.json。
- 数据隔离双闸（跑前确认在位，meta.json double_gate_ok=true）：闸一 settings 三项（LANTAI_HOME/DATABASE_URL/CHROMADB_PATH）全部指向 `docs/benchmarks/e1e2-formal-2026-10/eval-data/`；闸二 db.engine 为独立内存库、vector_store 待重建（不触宿主真实库）。

## 留证文件（逐项 JSONL）

| 文件 | 内容 |
|---|---|
| meta.json | 模型身份、双闸快照、harness 指认、一致率 |
| e1_extraction_consistency.jsonl | 提取段 3 报一致率逐 sid 逐 run 明细（10 fact × 3 run） |
| e1_real_llm.jsonl / e1_stub_ci.jsonl | E1 六段全量（stages/anchors/chains/獬豸日志）两轮 |
| e2_real_llm.jsonl / e2_stub_ci.jsonl | E2 双视图 37 用例两轮 |

## 结果

### E1（六段回放）

| 段 | 真 LLM 轮 | 替身 CI 对照轮 |
|---|---|---|
| extract | 1.0（NFR 0.75：1 条噪音泄漏） | 1.0（NFR 1.0） |
| gate | 0.9167（gate_false_pass 1） | 1.0 |
| store | 1.0 | 1.0 |
| index | 0.9（index_fts_orphan 1=预埋锚点命中） | 0.9（同） |
| retrieve | 0.25（recall_miss 2） | 0.75 |
| inject | 1.0 | 1.0 |

真 LLM 轮锚点：index_desync ✅ / zero_recall ✅ / fence_escape ✅；**gate_reject 缺席**——l1（"可能大概也许…"）真提取置信度未过拒收线，闸门判 working_only（chain: gate_rejection:working_only），按语料标签属 gate_false_pass。属真 LLM 行为与标签口径的实测分歧，非 harness 故障。

retrieve 段真 LLM 轮 0.25 的主因：Qwen3-8B 把中文 utterance 摘要成**英文**（"The main database is PostgreSQL 15…"，见一致性留证），中文改写查询与英文语料的 FTS/向量双通道均失配（miss: q_db、q_allergy）。替身轮中文语料 0.75。语言漂移是本轮第一发现。

### E1 提取段 3 报一致率：0/10 = 0.0

同一输入 3 次真提取，摘要无一 sid 逐字一致（措辞/大小写/置信度/claims 数均漂移；u3/l1/f1 个别 run 摘要为空）。全部 30 次调用均为真调用（fallback_suspect=false，延迟 25–122s/次），非网络失败。结论：**供应商非确定性显著，提取段不可作逐字对账基准**——与对表基线（步骤②）采用确定性提取替身的设计互为印证。

### E2（时间双视图）：真 LLM 轮与替身轮均 37/37 = 1.0

E2 管道不经提取/闸门 LLM（intent off、rerank off），两轮数字一致属预期。

## 声明

本地小样本操作级自证，不外推为普适效果，不与外部榜单横比。真 LLM 轮期间供应商 RPM 限流（429）出现过一次（首轮试跑，提案生成降级转待审）；正式留证轮 chat_json 内建 3 次指数退避重试全部通过，无降级。
