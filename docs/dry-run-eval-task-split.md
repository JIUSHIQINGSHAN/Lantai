# Dry-Run 评估管道 · 三模型任务分配

> 目标：实现可信度体系 v2 · Step 3 后半（dry-run 评估），在 **210 条干净检索事件**上跑相对指标。
> 分工：GLM5.2 / Kimi-K3 / DeepSeek-V4-Flash 各干一个模块，大哥手动切模型驱动，最后集成。
>
> **硬约束（已钉死，禁改）**：
> - 系统**无标注评估集**，禁假想 ground truth；只用相对指标（zero_result / jaccard / 弱命中率）
> - 参数注入用**显式 param_overrides**（contextvars 对 sync 线程池无效）
> - 测试纪律：每个核心函数必须有不 mock 的冒烟测试（AGENTS.md）

---

## 📐 接口契约（三个任务共同遵守，禁止自行改签名）

### 新包 `remembrance/eval/`

```
remembrance/eval/
├── __init__.py        # 空
├── models.py          # [GLM5.2] EvalQuerySet / EvalRun 表
├── query_set.py       # [GLM5.2] build_query_set()
├── metrics.py         # [Kimi-K3] compute_metrics() 等纯函数
├── runner.py          # [DeepSeek] run_dry_run() + 命令行入口
scripts/run_dry_run.py # [DeepSeek] CLI
```

### 表结构（models.py，GLM5.2 负责，字段名钉死）

```python
class EvalQuerySet(SQLModel, table=True):      # 表名 eval_query_set
    id: str = Field(primary_key=True)          # new_id("eqs")
    name: str = Field(index=True)              # 查询集名（唯一）
    built_at: datetime = Field(default_factory=utcnow)
    criteria: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # {"noise_excluded": true, "dedup": true, "source": "retrieval_event"}
    sample_count: int = 0
    queries: list = Field(default_factory=list, sa_column=Column(JSON))
        # [{"query": str, "event_id": str, "lane": str, "norm_hash": str}]

class EvalRun(SQLModel, table=True):           # 表名 eval_run
    id: str = Field(primary_key=True)          # new_id("erun")
    query_set_id: str = Field(index=True)
    query_set_name: str = ""
    param_overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 本次运行覆盖的参数 {key: value}
    param_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
        # 实际生效参数快照（default_snapshot() 合并 overrides 后）
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    status: str = "running"                    # running / done / error
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))
    per_query: list = Field(default_factory=list, sa_column=Column(JSON))
        # [{"query": str, "result_ids": [..], "top_scores": [..], "zero_result": bool, "latency_ms": int}]
    baseline_run_id: Optional[str] = None      # jaccard 对比的基线运行
```

### 函数签名（各任务按此实现，不得改名）

```python
# query_set.py [GLM5.2]
def build_query_set(name: str, *, noise_excluded: bool = True,
                    dedup: bool = True, limit: int | None = None) -> EvalQuerySet:
    """从 retrieval_event 干净事件构造查询集（去重 norm_hash），入库并返回。"""

# metrics.py [Kimi-K3] 全部纯函数，不碰 DB
def zero_result_rate(per_query: list[dict]) -> float: ...
def avg_result_count(per_query: list[dict]) -> float: ...
def jaccard_overlap(a: list[list[str]], b: list[list[str]]) -> float:
    """两轮运行同查询的召回集合 Jaccard 均值；a/b 是每查询的 result_ids 列表。"""
def weak_hit_rate(per_query: list[dict], *, used_ids_map: dict[str, list[str]] | None = None) -> float | None:
    """弱命中率：used_ids 在 top-k 结果中的比例；无 used_ids 数据时返回 None（诚实标 unavailable）。"""
def compute_metrics(per_query: list[dict], *, baseline_per_query: list[list[str]] | None = None) -> dict:
    """聚合全部指标，返回 {"zero_result_rate":.., "avg_result_count":.., "weak_hit_rate":..|None, "jaccard_vs_baseline":..|None, "sample_count":n}"""

# runner.py [DeepSeek]
def run_dry_run(query_set: EvalQuerySet, *, param_overrides: dict | None = None,
                top_k: int = 5, baseline_run_id: str | None = None,
                use_rerank: bool = True) -> EvalRun:
    """遍历查询集调 hybrid_search（注入 param_overrides），算指标，写 EvalRun。"""
```

---

## 🎯 任务 A — GLM5.2：数据层（表 + 查询集）

**干**：`remembrance/eval/models.py` + `remembrance/eval/query_set.py` + `tests/test_eval_query_set.py`

**要点**：
- 照现有表风格（参考 `retrieval_event`：str 主键、JSON 列用 `sa_column=Column(JSON)`）
- SQLModel 坑（项目血泪）：`Field` 必须从 `sqlmodel` import（不是 pydantic）；表类**不能用** `from __future__ import annotations`
- `build_query_set` 从 `retrieval_event` 取 `is_system_noise=0`，按 `query_norm_hash` 去重（只保留每个 hash 最新一条），生成 queries JSON
- 冒烟测试：真实 SQLite（内存库）+ 插入几条 RetrievalEvent 造数据 → 调 build_query_set → 断言去重/过滤/字段

**验收**：`python -m pytest tests/test_eval_query_set.py` 全绿；能用真实库跑 `build_query_set("v1")` 返回 ~210 条干净查询。

---

## 🎯 任务 B — Kimi-K3：指标层（纯函数）

**干**：`remembrance/eval/metrics.py` + `tests/test_eval_metrics.py`

**要点**：
- 全部纯函数，零 DB 依赖，输入输出可测
- `jaccard_overlap`：每查询 `len(intersection)/len(union)`，空集合对记 0，全部空返回 0.0
- `weak_hit_rate`：`used_ids_map` 为空/无数据 → 返回 `None`（**诚实标 unavailable**，不编造 0）
- `compute_metrics` 聚合，`baseline_per_query` 缺省时 jaccard 项为 None
- 冒烟测试：手造 per_query 数据（含空结果、多结果、used_ids 命中/未命中）直调函数断言

**验收**：`python -m pytest tests/test_eval_metrics.py` 全绿；边界（空列表、全 zero_result、无 used_ids）都覆盖。

---

## 🎯 任务 C — DeepSeek-V4-Flash：执行层（runner + CLI）

**干**：`remembrance/eval/runner.py` + `scripts/run_dry_run.py` + `tests/test_eval_runner.py`

**前置**：`hybrid_search` 需要加可选 `param_overrides: dict | None = None`（在函数体内合并进参数快照，**不改默认行为**，旧调用不受影响）。这是已裁定方案。

**要点**：
- `run_dry_run`：遍历 query_set.queries → 对每条调 `hybrid_search(query, top_k=top_k, param_overrides=param_overrides, use_rerank=use_rerank)` → 收集 result_ids/top_scores/zero_result/latency → 调 `compute_metrics` → 写 EvalRun（status=done，finished_at=now）
- 异常处理：单条查询失败不中断（记录 error 继续），status 置 done 但 metrics 带 `errors: n`
- CLI：`python scripts/run_dry_run.py --query-set NAME [--override alpha=0.5 beta=0.3] [--baseline RUN_ID] [--top-k 5]`
- 冒烟测试：mock embed/vector_store（允许 mock 外部网络）→ 造小查询集 → 跑 run_dry_run → 断言 EvalRun 落库、metrics 有值

**验收**：`python -m pytest tests/test_eval_runner.py` 全绿；对真实查询集跑一轮默认参数 dry-run 出报告。

---

## 🔄 执行顺序与集成

1. **A（GLM5.2）与 B（Kimi-K3）可并行**——互不依赖，只认契约
2. **C（DeepSeek）依赖 A+B**——等表结构和 metrics 就绪再跑
3. 集成验收（三个都完成后）：`python scripts/run_dry_run.py --query-set v1` 跑真实 dry-run → 出零结果率/Jaccard 基线报告

## ⚠️ 切换模型时给模型的背景话术（大哥直接粘贴）

每个任务开头附这段（按模型名替换）：

> 你在给 Remembrance 记忆系统（C:\Users\Asus\Desktop\记忆）写 dry-run 评估管道的 <数据层/指标层/执行层>。项目是 Python 3.11 + SQLModel + FastAPI。测试用 `C:/Users/Asus/Desktop/记忆/.venv-audit/Scripts/python.exe -m pytest`。项目纪律：每个核心函数必须有**不 mock 的冒烟测试**；mock 只允许外部网络（embed/rerank）。接口契约见本任务书，**禁止改签名**。参考文件：`remembrance/models/tables.py`（表风格）、`remembrance/observability/retrieval_log.py`（JSON 列/埋点风格）、`remembrance/retrieval/hybrid.py`（hybrid_search）。完成后跑自己模块的测试全绿并汇报。
