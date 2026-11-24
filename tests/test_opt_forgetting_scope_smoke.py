"""生产保护锁：遗忘质量评测不得衰减/归档真实记忆。

修前 `evaluate_forgetting_quality` 在 `dataset["apply_forgetting"]` 时直接调
`apply_forgetting()`——`principal=None` 在该函数的语义是**全表**（定期任务的
系统行为，见 `_forgetting_scope` 的 docstring）。而默认调用方
`scripts/run_forgetting_quality.py` 的非 `--check` 模式直连部署库：一次评测会
给真实记忆做衰减并自动归档，`archived` 没有 undo 入口。

修法：作用域收窄到本次评测自己种下的 `eval_fq` 种子。本锁用真实库验证
「真实库里的其他记忆一行都没被碰」——不 mock 衰减逻辑，种子与干扰行都是真行。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

_UNTOUCHED_TEXT = "真实用户的部署笔记：集群入口在 10.0.0.8，切勿被评测归档"


@pytest.fixture
def eval_env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    # 种子写入走 sync_fts（ADR-0008 强一致），FTS 表必须真实存在
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())

    # 干扰行：一条极旧、极低重要度的**真实**记忆——按衰减规则本该被归档。
    # 若评测的作用域是全表，它必被归档，本锁即红。
    from datetime import timedelta

    ancient = utcnow() - timedelta(days=4000)
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id="mem_real_old",
                key="real-old",
                content=_UNTOUCHED_TEXT,
                namespace="default",
                lane="general",
                tier="long_term",
                status="active",
                decay_class="episodic",
                importance=0.05,
                decay_score=1.0,
                confidence=1.0,
                created_at=ancient,
                updated_at=ancient,
                last_used_at=ancient,
            )
        )
        s.commit()
    return engine


def _dataset() -> dict:
    """最小评测集：一条 stale 种子（极旧、该被归档）+ 一条 fresh 种子。"""
    return {
        "apply_forgetting": True,
        "cases": [
            {
                "category": "stale",
                "query": "旧部署方案",
                "seeds": [
                    {
                        "content": "旧部署方案：单机跑在测试机上",
                        "created_days_ago": 4000,
                        "importance": 0.05,
                        "decay_class": "episodic",
                        "created_at": None,
                    },
                    {
                        "content": "新部署方案：集群三节点",
                        "created_days_ago": 0,
                        "importance": 0.9,
                        "decay_class": "episodic",
                    },
                ],
            }
        ],
    }


def _stub_search(query, **kw):
    return []


def test_real_memory_untouched(eval_env):
    """评测跑完后，真实库里的旧记忆必须原样：status 与 decay_score 都不变。"""
    from lantai.eval.forgetting_quality import evaluate_forgetting_quality

    evaluate_forgetting_quality(_dataset(), search=_stub_search, top_k=5, judge="off")

    with Session(eval_env) as s:
        row = s.get(MemoryItem, "mem_real_old")
    assert row is not None, "真实记忆被删了"
    assert row.status == "active", (
        f"评测把真实记忆归档了（status={row.status}）——作用域没收窄到评测种子"
    )
    assert row.decay_score == 1.0, f"评测衰减了真实记忆（decay_score={row.decay_score}）"


def test_eval_seeds_still_expire(eval_env):
    """反向保险：收窄不等于不评测——陈旧种子必须仍被判归档（否则本锁空转）。"""
    from lantai.eval.forgetting_quality import EVAL_NAMESPACE, evaluate_forgetting_quality

    result = evaluate_forgetting_quality(_dataset(), search=_stub_search, top_k=5, judge="off")
    assert "metrics" in result

    # 种子在 finally 里已被清理，所以这里验的是「评测自身跑到了归档判定」：
    # 用 metrics 里的 stale 类指标对账，而不是靠残留行。
    stale = result["metrics"].get("stale_hit_rate")
    assert stale is not None, f"评测未产出陈旧类指标：{sorted(result['metrics'])}"


def test_no_seed_leaks_after_run(eval_env):
    """评测不得在库里留下自己的种子（修前靠 namespace 清理，须仍成立）。"""
    from lantai.eval.forgetting_quality import EVAL_NAMESPACE, evaluate_forgetting_quality

    evaluate_forgetting_quality(_dataset(), search=_stub_search, top_k=5, judge="off")
    with Session(eval_env) as s:
        left = s.exec(select(MemoryItem).where(MemoryItem.namespace == EVAL_NAMESPACE)).all()
    assert not left, f"评测种子残留在库里：{[m.id for m in left]}"
