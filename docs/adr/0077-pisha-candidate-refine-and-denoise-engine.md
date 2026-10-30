# ADR-0077: 披沙（Pisha）——候选记忆递归提纯去噪、多轨指代消解与认知引擎收敛

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对提取候选处于模糊带（0.2~0.5）、弱指代与口语废话堆积、既有 refine 逻辑分散在服务层缺乏认知引擎统一抽象的架构演进。

---

## 1. 背景与痛点

在智能体长程对话与多源记忆摄入过程中，从对话流中一次性提取出的候选记忆（`MemoryCandidate`）常存在以下痛点：
1. **模糊带候选堆积与弱指代干扰**：
   提取出的事实常落在置信度 `[0.2, 0.5]` 的模糊带，且充满诸如“他推荐了那个工具”、“关于这个事情要保持警惕”等弱代词，缺乏上下文具体实体；
2. **缺乏统一的一级认知引擎抽象（PishaEngine）**：
   既有系统虽在早期服务层存在局部的 `refine_service.py`（ADR-0030），但未如同「石室」「青鸟」「拾遗」「拟议」等新一代认知引擎一样，收敛抽象为标准的 `PishaEngine` 认知实体，导致算法、过滤规则与多轨提纯策略分散；
3. **确定性多轨指代消解与规则降级不足**：
   过度依赖单次 LLM 生成，缺少结合上下文实体线索（entity_hints）与确定性口语噪音过滤的复合流水线；当 LLM 不可用或网络抖动时缺乏有保障的降级规则；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**披沙**」（Pisha · 《世说新语·德行》「披沙拣金，往往见宝」，取去粗取精、沙中淘金、去噪提纯之意），作为 v0.56.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**披沙（Pisha）**
- **出处**：《世说新语·德行》「披沙拣金，往往见宝」。
- **定位**：候选记忆递归提纯去噪、多轨指代消解与置信度重估认知引擎。

### 2.2 核心领域契约
1. **`PishaResult` 结构化提纯契约**：
   ```python
   @dataclass
   class PishaResult:
       original_text: str
       refined_text: str
       original_confidence: float
       refined_confidence: float
       is_valid: bool
       lane: str  # general | profile | preference | rule | entity | project
       pronoun_resolutions: dict[str, str] = field(default_factory=dict)
       denoise_tags: list[str] = field(default_factory=list)
       reason: str = ""
       fenced_content: str = ""
       llm_failed: bool = False
   ```
2. **核心引擎 `PishaEngine`**（`lantai/cognition/pisha.py`）：
   - `refine_text(text: str, context: str = "", entity_hints: dict[str, str] | None = None) -> PishaResult`：纯函数指代提纯与启发式去噪，支持确定性指代替换与置信度校准；
   - `refine_candidate(candidate_id: str) -> dict[str, Any]`：单条候选提纯落库，若 `is_valid=False` 联动沙汰标为 `rejected`；
   - `batch_refine_candidates(min_conf: float = 0.2, max_conf: float = 0.6, limit: int = 10) -> list[dict[str, Any]]`：批量扫描模糊带候选并执行披沙提纯；
   - 贯彻「宁 miss 不脏写」与行级租户属主隔离（`_check_access`）。

---

## 3. 影响

- 统一将候选精炼能力升格为认知层标准引擎；
- 提纯后候选置信度明确重估（清晰事实升至 0.7+，纯客套废话降至 0.0 并淘汰）；
- 所有提纯内容均提供樊篱（`<memory_data>`）防护；
- 支撑 MCP 90 工具池与认知上下文切面。
