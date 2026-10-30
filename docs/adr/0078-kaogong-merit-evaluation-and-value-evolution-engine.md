# ADR-0078: 考功（Kaogong）——长程价值演化与多维功过评定引擎、生命周期状态机与认知层收敛

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对记忆价值升降级与生命周期管理仅停留在粗粒度阈值、缺乏认知层统一引擎实体与多维综合评定模型的架构演进。

---

## 1. 背景与痛点

在智能体长程运行、多轮任务交互与高频知识沉淀过程中，系统存量记忆必须具备自我评估、去芜存菁与生命周期闭环治理能力：
1. **认知层缺少专职收敛的 KaogongEngine 核心引擎抽象**：
   既有考功逻辑分散在服务层 `kaogong_service.py`（早期 ADR-0031），仅具备粗粒度的三档阈值比对，未如同「石室」「青鸟」「拾遗」「披沙」等新一代认知引擎一样，收敛抽象为标准的 `KaogongEngine` 认知实体；
2. **多维价值评定模型与因子分解缺失**：
   既有逻辑仅单纯比对 `use_count` 与 `helpful_count`，缺乏对综合使用频率密度（usage density）、时效衰减（decay penalty）、冲突隐患关联（conflict penalty）以及多因子评分（MeritScore）的精细化建模，无法解释“为何升、为何降”；
3. **缺乏结构化可解释的考功评定契约（KaogongResult）**：
   缺乏统一的领域实体契约，评定结果缺少明确的升降级动作（`promote_longterm`、`demote_deprecate`、`keep_neutral`）、分值明细与可审计理由；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**考功**」（Kaogong · 唐代吏部「考功司」，掌官吏功过品级考评《唐六典》），作为 v0.57.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**考功（Kaogong）**
- **出处**：唐代吏部「考功司」，掌官吏功过品级考评（《唐六典》）。
- **定位**：长程价值演化与多维功过评定引擎、生命周期状态机。

### 2.2 核心领域契约
1. **`KaogongResult` 结构化考功契约**：
   ```python
   @dataclass
   class KaogongResult:
       memory_id: str
       action: str  # promote_longterm | demote_deprecate | keep_neutral
       score: float
       use_count: int
       helpful_count: int
       helpful_ratio: float
       score_factors: dict[str, float] = field(default_factory=dict)
       old_tier: str = "working"
       new_tier: str = "working"
       old_importance: float = 0.5
       new_importance: float = 0.5
       reason: str = ""
       fenced_content: str = ""
   ```
2. **核心引擎 `KaogongEngine`**（`lantai/cognition/kaogong.py`）：
   - `evaluate_item(memory: MemoryItem) -> KaogongResult`：多因子价值综合评定纯函数，综合采纳率、使用密度、衰减与冲突风险；
   - `evaluate_memory_id(memory_id: str) -> KaogongResult`：单条记忆考评与落库流转；
   - `run_cycle(limit: int = 100) -> dict[str, Any]`：批量考功巡检与落库执行；
   - `get_report() -> dict[str, Any]`：获取当前考功审计报告快照；
   - 贯彻「宁 miss 不脏写」与行级租户属主隔离（`_check_access`）。

---

## 3. 影响

- 统一将记忆升降级与生命周期评定升格为认知层标准引擎；
- 评定理由结构化透出，多因子综合打分具备自解释性；
- 评定正文与审计线索全部包裹樊篱（`<memory_data>`）防注入围栏；
- 支撑 MCP 91 工具池扩充与认知上下文 `kaogong_digest` 切面呈现。
