# ADR-0072: 底本（Diben）——跨会话现场定本与认知状态传承引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 跨会话认知连续性深化与 ADR-0013 意象候选转正（`checkpoint 会话快照 -> 底本`）

---

## 1. 背景

在智能体长程多轮交互与跨会话协同中，会话切换或上下文压缩常常导致 Agent 认知断层：
1. **五段快照停留在存储层**：既有 `SessionCheckpoint` 仅作为五段标量存储，缺乏对工作意图演化、关键决策约束及漂移识别的认知层抽象；
2. **缺乏意图延续与漂移偏转检测（Drift Detection）**：新会话启动或用户发起新指令时，Agent 无法精确判断用户是在延续既有任务、补充既有决策，还是已转向全新意图（Topic Drift），容易产生张冠李戴的幻觉；
3. **安全注入防御（樊篱贯穿）**：底本快照承载了过往会话的关键决策与待办，注入提示词若未经樊篱（`<memory_data>`）隔离，历史受控文本可能被利用实施间接提示注入；
4. **命名与规范治理**：落实 ADR-0013 意象池候选转正规范，正名「**底本**」（Diben · 版本学核心意象，宋代章学诚《校讎通义》「校讎之法，必本于底本」）。

---

## 2. 决策

### 2.1 命名正名与意象出处
正式确立中文意象名：**底本（Diben）**。
- **出处**：版本学核心意象。宋代及清代章学诚《校讎通义》：「校讎之法，必本于底本。」底本即校勘纂辑所据之定本、原本。
- **含义**：在会话间隙或任务重启时，将当前会话的认知现场（意图、决策、工作区、待办）固化为唯一可信定本，为新一轮交互提供定本锚点与传承依据，保证 Agent 行事有据、不迷失方向。

### 2.2 领域模型与核心数据结构
定义专职会话定本契约 `SessionAnchor`：
```python
@dataclass
class SessionAnchor:
    session_id: str
    active_intent: str
    next_action: str
    working_context: str
    key_decisions: list[str]
    open_notes: list[str]
    updated_at: datetime
    user_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

定义意图漂移分析契约 `DriftAnalysis`：
```python
@dataclass
class DriftAnalysis:
    query: str
    continuity_score: float  # [0.0, 1.0]
    is_continuation: bool
    drift_level: str  # none | slight | significant
    drift_reason: str
    fenced_reason: str
    analyzed_at: datetime
```

### 2.3 核心引擎 `DibenEngine`
在 `lantai/cognition/diben.py` 实现专职引擎：
1. `synthesize_anchor(session_id: str, blocks: dict[str, Any])`：
   - 将五段认知现场（在做/下一步/工作区/决策/待办）提炼为结构化定本 `SessionAnchor`；
   - 校验字段长度与合法性（宁 miss 不脏写）；
2. `analyze_drift(current_query: str, anchor: dict[str, Any] | None = None, session_id: str | None = None)`：
   - 自动获取激活定本；
   - 运用分词与关键实词匹配，计算语义延续度 `continuity_score`；
   - 给出意图漂移评级（`none` / `slight` / `significant`）；
3. `get_active_anchor(session_id: str | None = None)`：
   - 安全检索当前租户范围内最新有效的底本定本。

### 2.4 多租户行级 ACL 与宁 miss 不脏写
- 强制校验 `tenant_id` 与 `user_id`，非特权用户仅可访问与分析属于自己的会话定本；
- 跨租户底本访问坚决阻断。

### 2.5 认知上下文切面与 MCP 工具扩容
1. **认知上下文**：`CognitiveContext` 新增 `diben_digest` 切面，在 `to_prompt()` 中渲染 `## Canonical Session Anchor (底本 · 定本快照)`；
2. **MCP 工具池**：注册 `diben_anchor` 工具，工具池由 84 项扩充至 **85 项**，保持 1:1 双向映射守卫。

---

## 3. 影响

- 建立跨会话状态传承的认知层定本标准，消解会话间隙意图失落；
- 为 Agent 提供了输入意图是否漂移的快速判别能力；
- 与既有 `SessionCheckpoint` 数据表完美兼容，零迁移风险。
