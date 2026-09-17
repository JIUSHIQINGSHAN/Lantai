"""轨迹级奖励信用分配（episode credit）测试——v022 吸收票据 06 最小切片。

不 mock 冒烟：真实内存 SQLite，直调 record_episode / episode_credit_map；
权重纯函数不 mock。票据纪律：只登记不接检索权重。
"""

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import EpisodeRecord, EpisodeStep


def _setup():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def session_factory():
        return Session(engine)

    return engine, session_factory


class TestCreditWeights:
    """权重纯函数：w_i = λ·(1/n) + (1−λ)·归一化(γ^(n−i))，和恒为 1。"""

    def test_sum_is_one_and_recency_heavier(self):
        from lantai.services.episode_service import episode_credit_weights

        w = episode_credit_weights(4, lam=0.5, gamma=0.9)
        assert len(w) == 4
        assert abs(sum(w) - 1.0) < 1e-9
        # 越靠近结果越重：收尾步 > 中间步 > 首步
        assert w[3] > w[1] > w[0]

    def test_lam_zero_pure_recency(self):
        from lantai.services.episode_service import episode_credit_weights

        w = episode_credit_weights(3, lam=0.0, gamma=0.9)
        assert abs(sum(w) - 1.0) < 1e-9
        assert w[2] > w[1] > w[0]

    def test_edge_cases(self):
        from lantai.services.episode_service import episode_credit_weights

        assert episode_credit_weights(0) == []
        assert episode_credit_weights(1) == [1.0]
        # λ 越界夹取，不炸
        w = episode_credit_weights(3, lam=99, gamma=0.9)
        assert abs(sum(w) - 1.0) < 1e-9


class TestRecordEpisode:
    def test_record_persists_steps_with_credits(self):
        engine, sf = _setup()
        import lantai.storage.db as dbm

        original = dbm.get_session
        dbm.get_session = sf
        try:
            from lantai.services.episode_service import episode_credit_map, record_episode

            res = record_episode(
                session_id="sess_ep",
                outcome="success",
                steps=[{"memory_id": "mem_a"}, {"memory_id": "mem_b"}, {"memory_id": "mem_c"}],
            )
            assert res["episode_id"]
            assert res["step_count"] == 3
            with sf() as s:
                ep = s.get(EpisodeRecord, res["episode_id"])
                assert ep.outcome == "success"
                assert ep.session_id == "sess_ep"
                steps = s.exec(
                    select(EpisodeStep).where(EpisodeStep.episode_id == ep.id)
                ).all()
                assert len(steps) == 3
                assert {st.position for st in steps} == {1, 2, 3}
            # 聚合：success 全为正、收尾步信用更大
            credits = episode_credit_map()
            assert set(credits) == {"mem_a", "mem_b", "mem_c"}
            assert all(v > 0 for v in credits.values())
            assert credits["mem_c"] > credits["mem_a"]

            # failure 反向抵消
            record_episode(
                session_id="sess_ep",
                outcome="failure",
                steps=[{"memory_id": "mem_c"}],
            )
            credits2 = episode_credit_map(["mem_c"])
            assert credits2["mem_c"] < credits["mem_c"]
        finally:
            dbm.get_session = original

    def test_cron_like_write_rejected(self):
        engine, sf = _setup()
        import lantai.storage.db as dbm

        dbm.get_session = sf
        try:
            from lantai.services.episode_service import record_episode

            try:
                record_episode(session_id="", outcome="success", steps=[{"memory_id": "m"}])
                raised = False
            except ValueError:
                raised = True
            assert raised, "无 session 的写入不产生 episode（上游纪律）"

            try:
                record_episode(session_id="s", outcome="bogus", steps=[{"memory_id": "m"}])
                raised = False
            except ValueError:
                raised = True
            assert raised, "非法 outcome 拒收"

            try:
                record_episode(session_id="s", outcome="success", steps=[])
                raised = False
            except ValueError:
                raised = True
            assert raised, "空轨迹拒收"
        finally:
            dbm.get_session = None

    def test_neutral_excluded_from_credits(self):
        engine, sf = _setup()
        import lantai.storage.db as dbm

        dbm.get_session = sf
        try:
            from lantai.services.episode_service import episode_credit_map, record_episode

            record_episode(
                session_id="s2", outcome="neutral", steps=[{"memory_id": "mem_x"}]
            )
            assert episode_credit_map() == {}
        finally:
            dbm.get_session = None
