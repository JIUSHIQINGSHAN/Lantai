# Step 7 影子观察期 · 双模型任务分配（Kimi K3 + DeepSeek Flash）

> 目标：参数建议批准后不直接生效，先跑**影子观察期**——用隐式信号对比新旧参数，达标才正式应用，恶化自动回滚。**人工闸门不变**（系统铁律：最终应用仍需人工批准，验证自动化只提供证据）。
>
> 分工：**Kimi K3** 干高智力（架构设计 + 决策逻辑纯函数），**DeepSeek Flash** 干高执行力（按契约落地 + 集成 + 测试），**WorkBuddy（小弟）** 协调验收。
>
> **硬约束（已钉死，禁改）**：
> - 系统无标注评估集，禁假想 ground truth；只用隐式信号（zero_result / avg_result_count / jaccard）
> - 人工闸门不变：观察期达标 → 进入 pending 队列 → 人工批准 → 应用；绝无自动应用路径
> - 测试纪律：每个核心函数必须有**不 mock 的冒烟测试**（AGENTS.md）
> - 与现有架构一致：SQLModel 表、JSON 列 `sa_column=Column(JSON)`、str 主键 `new_id()`

---

## 📐 接口契约（两模型共同遵守，禁止自行改签名）

### 新文件

```
remembrance/parameters/shadow.py    # [Kimi] 决策逻辑纯函数（高智力）
remembrance/parameters/trust_models.py  # [DeepSeek] 追加 ShadowWindow 表（按 Kimi 设计）
remembrance/parameters/runtime.py   # [DeepSeek] 集成：注册 shadow / 检查观察期
tests/test_param_shadow.py          # [DeepSeek] 测试（核心函数不 mock 冒烟）
```

### ShadowWindow 表（Kimi 定字段 → DeepSeek 实现）

```python
class ShadowWindow(SQLModel, table=True):   # 表名 shadow_window
    id: str = Field(primary_key=True)        # new_id("sw")
    override_id: str = Field(index=True)     # 关联 ParamOverride.revision（发起者）
    base_revision: int = 0                   # 观察起点 revision
    param_overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 本次影子参数 {key: value}
    base_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 基线参数快照（默认快照）
    status: str = "observing"                # observing / promoted / rolled_back / cancelled
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    check_deadline: datetime = Field(default_factory=utcnow)  # 观察截止（started_at + OBSERVE_DAYS）
    metrics_base: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 基线运行指标（compute_metrics 输出）
    metrics_shadow: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 影子运行指标
    rollback_reason: Optional[str] = None
    verdict_reason: Optional[str] = None     # promote/rollback 的判定理由（可审计）
```

### 决策函数（Kimi 实现，DeepSeek 不得改签名）

```python
# shadow.py [Kimi]
def evaluate_window(base: dict, shadow: dict, *,
                    zero_result_delta: float = 0.05,
                    avg_result_delta: float = 1.0,
                    jaccard_floor: float = 0.7) -> dict:
    """判定观察窗结果。

    base/shadow: compute_metrics 输出（含 zero_result_rate / avg_result_count / jaccard_vs_baseline）。
    返回 {"verdict": "promote"|"rollback"|"hold", "reason": str, "signals": {...}}
    规则：
      - shadow.zero_result_rate - base.zero_result_rate > zero_result_delta → rollback（漏检恶化）
      - shadow.avg_result_count < base.avg_result_count - avg_result_delta → rollback（召回骤降）
      - jaccard_vs_baseline < jaccard_floor → rollback（召回集合偏离过大）
      - 否则 promote
    全输入空 → hold（数据不足不判定）
    """
```

```python
# shadow.py [Kimi]
def shadow_is_due(window) -> bool:
    """观察期是否到期（check_deadline 已过）。"""
```

```python
# shadow.py [Kimi]
def decide_promote_target(window, *, min_promote_days: int = 0) -> bool:
    """promote 前置检查：状态必须 observing 且到期（防过早 promote）。"""
```

### runtime 集成（DeepSeek 实现）

```python
# runtime.py [DeepSeek] 追加
def open_shadow(override_id: str, param_overrides: dict, *, observe_days: int | None = None) -> ShadowWindow:
    """建议批准后打开观察窗（status=observing）。"""

def check_shadow_due() -> list[ShadowWindow]:
    """轮询到期观察窗，跑对比 dry-run，调 evaluate_window 判定，更新状态。"""

def rollback_shadow(window_id: str, reason: str) -> None:
    """护栏回滚：恢复 base_snapshot 并记录。"""
```

---

## 🎯 任务一 — Kimi K3（高智力：设计 + 决策逻辑）

**干**：`remembrance/parameters/shadow.py`（evaluate_window / shadow_is_due / decide_promote_target）+ 设计说明

**要点**：
- `evaluate_window` 是纯函数，零 DB，输入 compute_metrics 输出即可测
- 规则要**保守**：宁可 hold 也不误 promote（人工闸门兜底）
- 边界：全空输入 → hold；jaccard 为 None（无基线）时跳过 jaccard 检查只比前两项
- 冒烟测试：手造 base/shadow 指标（恶化/改善/持平/空）直调断言

**验收**：`pytest tests/test_param_shadow.py` 里你自己写的用例全绿（DeepSeek 会补集成测试，你负责决策逻辑的纯函数测试）。

---

## 🎯 任务二 — DeepSeek Flash（高执行力：落地 + 集成 + 测试）

**干**：`trust_models.py` 追加 `ShadowWindow` 表 + `runtime.py` 集成（open_shadow/check_shadow_due/rollback_shadow）+ `tests/test_param_shadow.py` 集成测试 + `settings.py` 加观察期参数

**要点**：
- 表按契约字段实现（Kimi 的 shadow.py 只依赖这些字段名）
- `check_shadow_due`：查所有 status=observing 且到期的窗 → 对每条：跑 dry-run（复用 `remembrance.eval.runner.run_dry_run`，用 base_snapshot 和 param_overrides 各跑一轮）→ 调 `evaluate_window` → 更新状态（promoted → 写 ParamOverride？**不**——promote 只标记，实际应用走人工闸门，见下）
- **DEDUP shadow-only**：观察期内影子参数不写 ParamOverride（不参与实时去重/应用），只在 shadow_window 表里记录
- **人工闸门**：promoted 的窗生成一条 pending 建议（复用现有 ParamSuggestion 流程）等人工批准；**绝不自动应用**
- settings 加：`SHADOW_OBSERVE_DAYS: int = 7`、`SHADOW_CHECK_INTERVAL_SECONDS: int = 3600`
- 测试：表可建、open_shadow 落库、到期判定、rollback 恢复（mock dry-run 的 LLM 调用，允许 mock 外部网络）

**验收**：`pytest tests/test_param_shadow.py` 全绿；`pytest tests/` 全量回归无破坏。

---

## 🔄 执行顺序与集成

1. **Kimi（任务一）先行**——决策逻辑是纯函数，DeepSeek 的集成测试依赖它的行为
2. **DeepSeek（任务二）**——按契约实现表 + 集成，调 evaluate_window
3. **小弟（WorkBuddy）**：合并两模型产出 → 全量回归 → 真实建一个 shadow 窗验证流程 → 出报告 → 提交 GitHub

## ⚠️ 切换模型时的背景话术（大哥直接粘贴）

> 你在给 Remembrance 记忆系统（C:\Users\Asus\Desktop\记忆）实现 Step 7 影子观察期的 <决策逻辑/落地集成>。项目 Python 3.11 + SQLModel + FastAPI。测试用 `C:/Users/Asus/Desktop/记忆/.venv-audit/Scripts/python.exe -m pytest`。项目纪律：核心函数必须有**不 mock 的冒烟测试**（mock 只允许外部网络）；SQLModel 表类不能用 `from __future__ import annotations`；Field 必须从 sqlmodel import。接口契约见任务书，**禁止改签名**。参考：`remembrance/parameters/runtime.py`（参数应用）、`remembrance/parameters/trust_models.py`（表风格）、`remembrance/eval/runner.py`（dry-run 复用）。完成后跑自己模块测试全绿并汇报。
