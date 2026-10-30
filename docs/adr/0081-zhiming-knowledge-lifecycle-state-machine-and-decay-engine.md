# ADR-0081: 知命（Zhiming）——认知生命全周期演化状态机与动态时效衰变引擎

**日期**: 2026-11-01  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 针对既有知识生命周期管理停留在静态单向流转、缺乏动态时效半衰期模型与复原双向状态机的架构演进。

---

## 1. 背景与痛点

在长期记忆和认知演化过程中，知识具有鲜明的生命节律（生长、成熟、衰变、取代、退役与复元）：
1. **生命周期流转单向且孤立**：
   原有 `KnowledgeLifecycleManager` 仅支持静态状态位转移，缺乏对临时弱化（`weakened`）知识在被重新验证或正面反馈时的复原机制（`rejuvenate` / `reinforce`）；
2. **缺乏认知引擎级领域契约与批处理能力**：
   缺乏结构化领域实体 `ZhimingItem`、`ZhimingTransition` 与 `ZhimingResult` 契约，没有独立认知引擎 `ZhimingEngine` 支撑精准生命周期推演与无缝状态流转；
3. **时效衰变计算粗糙硬编码**：
   缺乏结合访问频度、更新时间距今间隔与采纳率的动态半衰期衰变推演模型（Half-life Decay）；
4. **命名体系传承与版本对齐**：
   遵循 ADR-0013 命名体系（2–4 字、出自传统意象、名实相副，先登记 `CONTEXT.md` 词汇表再使用）。正名「**知命**」（Zhiming · 《论语·为政》「五十而知天命」、《论语·尧曰》「不知命，无以为君子也」，知生灭衰荣之期、顺应认知生命节律），作为 v0.60.0 版本代号与核心引擎名。

---

## 2. 决策

### 2.1 命名正名与出处
- **中文意象**：**知命（Zhiming）**
- **出处**：《论语·尧曰》「不知命，无以为君子也」、《论语·为政》「五十而知天命」。
- **定位**：认知生命全周期演化状态机与动态时效衰变引擎。

### 2.2 核心领域契约
1. **领域契约**：
   ```python
   class LifecyclePhase(str, Enum):
       CANDIDATE = "candidate"
       ACTIVE = "active"
       WEAKENED = "weakened"
       SUPERSEDED = "superseded"
       RETIRED = "retired"


   @dataclass
   class ZhimingTransition:
       memory_id: str
       from_phase: LifecyclePhase
       to_phase: LifecyclePhase
       effective_confidence: float
       decay_factor: float
       reason: str
       applied: bool = False
       updated_at: datetime = field(default_factory=utcnow)


   @dataclass
   class ZhimingItem:
       memory_id: str
       phase: LifecyclePhase
       base_confidence: float
       effective_confidence: float
       half_life_days: float
       age_days: float
       use_count: int
       helpful_count: int
       fenced_content: str = ""


   @dataclass
   class ZhimingResult:
       total_inspected: int
       transitions: list[ZhimingTransition] = field(default_factory=list)
       applied_count: int = 0
       distribution: dict[str, int] = field(default_factory=dict)
       summary: str = ""
       fenced_content: str = ""
   ```

2. **核心引擎 `ZhimingEngine`**（`lantai/cognition/zhiming.py`）：
   - `compute_decay(confidence, age_days, use_count, helpful_count) -> float`：动态半衰期衰减推演；
   - `evaluate_transition(item) -> ZhimingTransition`：状态转移裁决；
   - `apply_transition(transition, session) -> ZhimingTransition`：真实 DB 状态机更新；
   - `inspect_lifecycle(memory_id) -> ZhimingItem`：单条生命周期深度探查；
   - `run_lifecycle_audit(session, auto_apply) -> ZhimingResult`：全库或多租户生命周期巡检；
   - 贯彻「宁 miss 不脏写」与租户行级 ACL 隔离（`_check_access`）。

---

## 3. 影响

- 统一将记忆生命周期升级为动态、可度量、双向自愈的一级认知资产；
- 支持 `weakened -> active` 复元，避免误杀具备复苏潜力的优质记忆；
- 提供安全的行级 ACL 与樊篱（`<memory_data>`）防护；
- 支撑 MCP 94 工具池扩充与认知上下文 `zhiming_digest` 切面呈现。
