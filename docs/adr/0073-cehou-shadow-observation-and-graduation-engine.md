# ADR-0073: 测候（Cehou）——影子观察期巡检与演化状态监控引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 认知演化影子期状态机深化与 ADR-0013 意象候选转正（`shadow 观察期 -> 测候`）

---

## 1. 背景

在智能体长程自主演化与知识晋升过程中，新规则、参数修改（`ParamSuggestion`）或反思蒸馏提案在全面生效前，必须经过非侵入式的影子期观测（Shadow Observation Period）：
1. **缺乏统一的影子视窗巡检引擎**：既有系统虽定义了滑动窗口，但缺乏面向认知演化对象的实时健康度量、实测胜率聚合、样本收集与生命周期状态机管理；
2. **缺乏应验转正与漂移熔断裁决**：没有自动化的「待其应验、以定新章」机制，无法自动核算观察天数与样本门槛并输出转正（`graduated`）或回滚（`rolled_back`）裁决；
3. **安全注入防御（樊篱贯穿）**：影子视窗的观测指标、收益分析与转正结论若未经樊篱（`<memory_data>`）防护，易被下游 LLM 误当成指令执行；
4. **命名与规范治理**：落实 ADR-0013 意象池候选转正规范，正名「**测候**」（Cehou · 沈括《梦溪笔谈》「司天监测候天象，待其应验，以定历法」）。

---

## 2. 决策

### 2.1 命名正名与意象出处
正式确立中文意象名：**测候（Cehou）**。
- **出处**：古代天象、气象与律历观测官制/典制意象。北宋沈括《梦溪笔谈·象数一》：「司天监测候天象，待其应验，以定历法。」
- **含义**：在认知演化提案或新规则生效前，作为专职影子期观测与状态机评估引擎，持续测验守候、跟踪指标漂移、验证收益真实性，待其数据周全应验后再行转正入库。

### 2.2 领域模型与核心数据结构
定义专职影子观察视窗契约 `ShadowObservation`：
```python
@dataclass
class ShadowObservation:
    window_id: str
    target_type: str  # param | rule | proposal
    target_id: str
    state: str  # observing | graduated | rolled_back
    days_observed: int
    required_days: int
    sample_count: int
    min_samples: int
    success_rate: float
    metrics_delta: dict[str, float]
    observation_summary: str
    fenced_summary: str
    created_at: datetime
    updated_at: datetime
    user_id: str | None = None
    tenant_id: str | None = None
```

定义测候巡检汇总报告契约 `InspectionReport`：
```python
@dataclass
class InspectionReport:
    total_windows: int
    observing_count: int
    graduated_count: int
    rolled_back_count: int
    alerts: list[str]
    inspected_at: datetime
```

### 2.3 核心引擎 `CehouEngine`
在 `lantai/cognition/cehou.py` 实现专职引擎：
1. `inspect_window(window_id: str)`：
   - 核算累计观察天数、样本量与实测成功率；
   - 判定状态跃迁：若达到天数、样本门槛且成功率符合预期判定为 `graduated`；若异常率极高判定为 `rolled_back`；未达标则维持 `observing`；
2. `scan_active_windows(target_type: str | None = None)`：
   - 全局扫描当前租户内活跃观察视窗并生成巡检汇总报告；
3. `record_sample(window_id: str, success: bool, latency_ms: float = 0.0, score_delta: float = 0.0)`：
   - 记录实测运行样本，增量更新视窗胜率与指标分差。

### 2.4 多租户行级 ACL 与宁 miss 不脏写
- 强制校验 `tenant_id` 与 `user_id`，非特权用户仅可访问与记录属于自己的观察视窗；
- 样本量不足或门槛未满足时坚决维持观察态（`observing`），严禁静默转正。

### 2.5 认知上下文切面与 MCP 工具扩容
1. **认知上下文**：`CognitiveContext` 新增 `cehou_digest` 切面，在 `to_prompt()` 中渲染 `## Shadow Observation & Graduation (测候 · {count})`；
2. **MCP 工具池**：注册 `cehou_observe` 工具，工具池由 85 项扩充至 **86 项**，保持 1:1 双向映射守卫。

---

## 3. 影响

- 建立认知演化与参数建议落地前的安全观测护栏，保证「收益未经实证不轻定」；
- 完全向后兼容既有 `ParamSuggestion` 状态机与反思观察期口径；
- 提供了可解释的胜率、样本量与漂移告警视图。
