"""残留锁：`run_dry_run(intent_mode="rule")` 的 patch 必须在异常路径也收掉。

原实现把 `_intent_patcher.stop()` 放在正常路径末尾，中途任何异常都会把
`lantai.retrieval.hybrid.classify_intent` 的 mock 留成**进程级**残留——之后的
检索全部走规则意图且无人知晓（本仓 conftest 专门治理这类绑定残留，但生产
脚本路径下无人收尾）。

本锁用真实失败路径取证：让 `db.get_session` 在基线读取时炸，然后断言
`hybrid.classify_intent` 已经还原成真实函数。
"""

import pytest

from lantai.retrieval import hybrid


class _FakeQuerySet:
    def __init__(self, queries):
        self.queries = queries
        self.id = "qs-opt-probe"
        self.name = "opt-probe"
        self.params = {}
        self.version = 1


@pytest.fixture
def runner_env(monkeypatch):
    """最小环境：真实 hybrid_search 被替身（外部网络面），会话在基线读取时炸。"""
    import lantai.eval.runner as runner_mod

    def fake_search(query, **kw):
        return []

    monkeypatch.setattr(hybrid, "hybrid_search", fake_search)

    class _Boom:
        def __enter__(self):
            raise RuntimeError("baseline db unavailable")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(runner_mod.db, "get_session", lambda: _Boom())
    return runner_mod


def test_patch_released_on_exception(runner_env):
    """基线读取炸掉之后，classify_intent 必须已还原（不是 mock）。"""
    real = hybrid.classify_intent
    assert not hasattr(real, "mock_calls"), "前置：进测试前不该是 mock"

    with pytest.raises(RuntimeError, match="baseline db unavailable"):
        runner_env.run_dry_run(
            _FakeQuerySet([{"query": "部署脚本放哪"}]),
            intent_mode="rule",
            baseline_run_id="erun_missing",
        )

    assert hybrid.classify_intent is real, "异常路径把 classify_intent 的 patch 留下了"


def test_patch_released_on_success(monkeypatch):
    """正常路径同样要收（防「为了异常路径把 stop 删了」）。

    用真内存库跑完整一轮（外部网络面替身）：落库、指标、写 EvalRun 全走真代码。
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.eval.runner as runner_mod
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(hybrid, "hybrid_search", lambda q, **kw: [])
    real = hybrid.classify_intent

    runner_mod.run_dry_run(_FakeQuerySet([{"query": "部署"}]), intent_mode="rule")
    assert hybrid.classify_intent is real, "正常路径未收 patch"
