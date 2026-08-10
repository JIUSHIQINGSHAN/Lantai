"""评估管道表：EvalQuerySet（查询集）+ EvalRun（运行记录）。

设计约束（已钉死，禁改）：
- str 主键（new_id），JSON 列用 sa_column=Column(JSON)
- 表类不能用 `from __future__ import annotations`（SQLModel 类型解析崩）
- 字段名与 docs/dry-run-eval-task-split.md 契约一致
"""
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field, Column, JSON

from lantai.core.ids import new_id
from lantai.core.time import utcnow


class EvalQuerySet(SQLModel, table=True):
    """评估查询集：从 RetrievalEvent 干净事件构造的可重复评估样本集。"""
    __tablename__ = "eval_query_set"

    id: str = Field(primary_key=True)  # new_id("eqs")
    name: str = Field(index=True)  # 查询集名（唯一）
    built_at: datetime = Field(default_factory=utcnow)
    criteria: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # {"noise_excluded": true, "dedup": true, "source": "retrieval_event"}
    sample_count: int = 0
    queries: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{"query": str, "event_id": str, "lane": str, "norm_hash": str}]


class EvalRun(SQLModel, table=True):
    """评估运行：一次 dry-run 的参数快照 + 结果指标。"""
    __tablename__ = "eval_run"

    id: str = Field(primary_key=True)  # new_id("erun")
    query_set_id: str = Field(index=True)
    query_set_name: str = ""
    param_overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # 本次运行覆盖的参数 {key: value}
    param_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # 实际生效参数快照（default_snapshot() 合并 overrides 后）
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    status: str = "running"  # running / done / error
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))
    per_query: list = Field(default_factory=list, sa_column=Column(JSON))
    # [{"query": str, "result_ids": [..], "top_scores": [..], "zero_result": bool, "latency_ms": int}]
    baseline_run_id: Optional[str] = None  # jaccard 对比的基线运行
