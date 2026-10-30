# ADR-0076: 拾遗（Shiyi）——主流水线零召回自适应补救闭环与深度检索治理

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对既有混合检索主流水线（`hybrid_search`）在初筛失配或时态/衰减过滤后出现零召回（Zero-Recall）时直接截断返空、未与认知层拾遗补救（ShiyiEngine）形成主动闭环的架构痛点，转正本落地规范。

---

## 1. 背景与痛点

在生产环境的实际使用中，虽然兰台构建了 `ShiyiEngine`（ADR-0069）作为独立的认知层端点，但在**核心检索主流水线**中依然存在以下断层：
1. **主流水线零召回缺乏主动自适应触发**：
   既有 `hybrid_search` 在初筛向量/FTS 候选均未命中，或者在经 Chronos 时间轴过滤、衰减过滤后候选列表变为空（Zero-Recall）时，直接退回空数组，未能主动唤醒 `ShiyiEngine` 启动多阶遗漏补偿回溯；
2. **检索结果与拾遗补救契约缺乏统一度量投影**：
   拾遗补救输出的是 `RemedyCandidate` 契约，而下游 Agent 及主流接口期望的是统一的检索结果集（包含 `score`, `memory`, `document`, `layer`, `explain` 等）；需要定义标准桥接，使补救结果无缝注入并携带 `is_remedied: True` 与 `remedy_tier`；
3. **行级安全隔离与樊篱防御铁律**：
   在主流水线自动放宽检索阶梯时，绝对禁止跨越租户边界与非授权属主范围（宁 miss 不脏写、宁漏不越权）；所有补救正文强制经由樊篱（`wrap_as_data`）数据围栏包裹；
4. **命名与版本治理**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。取典籍官署官职意象「**拾遗**」（Shiyi · 唐代谏官官职「拾遗」，取「捡回遗漏之物、补苴罅漏」之意）。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**拾遗（Shiyi）**
- **出处**：唐代谏官官职「拾遗」（杜甫曾任左拾遗），取「捡回遗漏之物、补苴罅漏」之意。
- **定位**：主流水线零召回自适应补救闭环与深度检索治理引擎。

### 2.2 核心机制与领域契约

1. **`RemedyCandidate.to_search_result()` 统一投影**：
   定义拾遗候选条目向主流水线检索结果项的标准转换：
   ```python
   def to_search_result(self, *, layer: str = "instance") -> dict[str, Any]:
       return {
           "score": round(self.confidence, 4),
           "memory": {
               "id": self.id,
               "content": self.content,
               "domain": self.domain,
               "lane": self.lane,
               "confidence": self.confidence,
               "user_id": self.user_id,
               "tenant_id": self.tenant_id,
               "metadata": {
                   **self.metadata,
                   "is_remedied": True,
                   "remedy_tier": self.remedy_tier,
                   "remedy_reason": self.remedy_reason,
               },
           },
           "document": self.content,
           "fenced_content": wrap_as_data(self.content, item_id=self.id, score=self.confidence),
           "layer": layer,
           "is_remedied": True,
           "remedy_tier": self.remedy_tier,
           "remedy_reason": self.remedy_reason,
       }
   ```

2. **`hybrid_search` 零召回自适应接入**：
   在 `lantai/retrieval/hybrid.py` 的 `hybrid_search` 中新增参数 `enable_shiyi: bool = True`：
   - 当 `candidates` 初筛为空或 `items` 过滤后为空时，且 `enable_shiyi` 为 `True` 且 `query` 存在实质内容，自动调用 `ShiyiEngine.remedy_recall(...)`；
   - 将补救候选按 `to_search_result()` 格式转换为结果集并返回；
   - 在 `trace_steps` 中追加 `{ "step": "shiyi_remedy", "candidate_count": len(remedied), ... }` 留存审计线索；
   - 保持兼容回退：当 `enable_shiyi=False` 时，保持既有静默返回空结果的行为。

3. **安全与樊篱铁律**：
   - 补救过程必须严格校验 `_check_access`（行级租户与属主隔离，宁漏不越权）；
   - 补救结果强制施加 `<memory_data>` 围栏隔离。

4. **MCP 89 工具里程碑与服务层**：
   - 新增 `remedy_pipeline_service` 服务函数；
   - 扩展 REST 端点 `POST /shiyi/pipeline`；
   - 注册 MCP 工具 `shiyi_pipeline`，扩充至 89 工具里程碑并保持 1:1 双向对齐。

---

## 3. 影响

- 根治生产环境检索初筛失配或时态衰减导致的「零召回击穿」；
- 形成完整的「主流初筛 $\to$ 零召回捕获 $\to$ 自适应拾遗回溯 $\to$ 统一格式输出」主流检索闭环；
- MCP 工具总数扩充至 89 个，且保持 1:1 双向一致性守卫。
