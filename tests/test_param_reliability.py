"""Step 8 验证回流测试——纯函数 + 真实 SQLite。

覆盖：record_verification_result（pass/fail/streak）、reliability_penalty
（样本不足不动、streak 触发、rate 触发、只降不升、TTL 过期恢复）、
apply_penalty_to_weight。
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.parameters.reliability import (
    apply_penalty_to_weight,
    record_verification_result,
    reliability_penalty,
)


@pytest.fixture(scope="function")
def rel_db():
    import lantai.models.tables  # noqa: F401
    import lantai.parameters.trust_models  # noqa: F401
    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        db_module, "get_session", get_test_session
    ):
        yield get_test_session


class TestRecordVerification:
    def test_record_pass_creates_stat(self, rel_db):
        sf = rel_db
        stat = record_verification_result("journal", passed=True)
        assert stat.venue_class == "journal"
        assert stat.pass_count == 1
        assert stat.fail_count == 0
        assert stat.fail_streak == 0

    def test_record_fail_increments_streak(self, rel_db):
        record_verification_result("preprint", passed=False)
        record_verification_result("preprint", passed=False)
        stat = record_verification_result("preprint", passed=False)
        assert stat.fail_count == 3
        assert stat.fail_streak == 3

    def test_pass_resets_streak(self, rel_db):
        record_verification_result("preprint", passed=False)
        record_verification_result("preprint", passed=False)
        stat = record_verification_result("preprint", passed=True)
        assert stat.fail_streak == 0
        assert stat.pass_count == 1

    def test_unknown_default(self, rel_db):
        stat = record_verification_result("  ", passed=True)
        assert stat.venue_class == "unknown"


class TestReliabilityPenalty:
    def test_no_stat_returns_one(self, rel_db):
        assert reliability_penalty("journal") == 1.0

    def test_insufficient_samples_no_penalty(self, rel_db):
        """样本不足（< PENALTY_MIN_SAMPLES=3）不动手。"""
        record_verification_result("workshop", passed=False)
        record_verification_result("workshop", passed=False)
        assert reliability_penalty("workshop") == 1.0

    def test_streak_triggers_penalty(self, rel_db):
        """连续 3 次失败（≥ streak=2）触发降权。"""
        for _ in range(3):
            record_verification_result("preprint", passed=False)
        p = reliability_penalty("preprint")
        assert p < 1.0
        assert p >= 0.5  # 下限保护

    def test_fail_rate_triggers_penalty(self, rel_db):
        """fail_rate ≥ 0.5 触发（样本够）。"""
        # 2 fail + 1 pass = rate 0.67，样本 3
        record_verification_result("top_conf", passed=True)
        record_verification_result("top_conf", passed=False)
        record_verification_result("top_conf", passed=False)
        p = reliability_penalty("top_conf")
        assert p < 1.0

    def test_good_ratio_no_penalty(self, rel_db):
        """高通过率不降权。"""
        for _ in range(3):
            record_verification_result("journal", passed=True)
        assert reliability_penalty("journal") == 1.0

    def test_ttl_expiry_restores(self, rel_db):
        """TTL 过期后恢复 1.0（降权不永久）。"""
        from datetime import timedelta
        from unittest.mock import patch
        from lantai.core.time import utcnow
        # 制造 3 连败
        for _ in range(3):
            record_verification_result("workshop", passed=False)
        assert reliability_penalty("workshop") < 1.0
        # 把 last_verified_at 拨回 200 天前（> TTL 180）
        from lantai.parameters.trust_models import SignalReliabilityStat
        from sqlmodel import select
        sf = rel_db
        with sf() as s:
            stat = s.exec(select(SignalReliabilityStat).where(
                SignalReliabilityStat.venue_class == "workshop")).first()
            stat.last_verified_at = utcnow().replace(tzinfo=None) - timedelta(days=200)
            s.add(stat)
            s.commit()
        assert reliability_penalty("workshop") == 1.0


class TestApplyPenaltyToWeight:
    def test_applies_penalty(self, rel_db):
        for _ in range(3):
            record_verification_result("preprint", passed=False)
        w = apply_penalty_to_weight(1.0, "preprint")
        assert w < 1.0

    def test_no_penalty_keeps_weight(self, rel_db):
        assert apply_penalty_to_weight(0.97, "journal") == 0.97
