"""
参数注册表 + 纯校验冒烟测试（不 mock，真实直调核心函数）。
"""
import math

import pytest

from remembrance.parameters.registry import default_snapshot, get_param_registry
from remembrance.parameters.schemas import ParamChange
from remembrance.parameters.validation import (
    ParamValidationError,
    apply_validated_changes,
    validate_snapshot,
)


def test_default_snapshot_valid():
    """默认六参数快照必须通过校验。"""
    snap = validate_snapshot(default_snapshot())
    assert snap["RETRIEVAL_W_VECTOR"] == 0.6
    assert snap["DEDUP_MERGE_THRESHOLD"] == 0.8


def test_phantom_param_fails():
    with pytest.raises(ParamValidationError):
        validate_snapshot({"FAKE_PARAM": 0.5})


def test_security_param_fails_default_deny():
    """物理排除清单参数即使出现在快照里也拒绝。"""
    with pytest.raises(ParamValidationError):
        validate_snapshot({**default_snapshot(), "API_KEY": 1.0})


def test_non_adjustable_param_fails():
    """已知但未登记 adjustable 的参数（如衰减半衰期）拒绝。"""
    with pytest.raises(ParamValidationError):
        validate_snapshot({**default_snapshot(),
                           "ARCHIVE_DECAY_THRESHOLD": 0.02})


def test_out_of_range_fails():
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_VECTOR", before=0.6, after=0.85,
                        reason="t")])


def test_step_violation_fails():
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_FTS", before=0.05, after=0.02,
                        reason="t")])


def test_delta_too_big_fails():
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_DECAY", before=0.10, after=0.20,
                        reason="t")])


def test_sum_constraint_fails():
    """权重只改一个且不补偿 → 和不等于 1 → 拒绝。"""
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_VECTOR", before=0.6, after=0.55,
                        reason="t")])


def test_dedup_gap_fails():
    """merge-update 间距 < 0.10 → 拒绝。"""
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="DEDUP_UPDATE_THRESHOLD", before=0.65, after=0.75,
                        reason="t")])


def test_before_mismatch_fails():
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_VECTOR", before=0.55, after=0.6,
                        reason="t"),
            ParamChange(name="RETRIEVAL_W_BM25", before=0.25, after=0.2,
                        reason="t")])


def test_nan_infinity_fails():
    with pytest.raises(ParamValidationError):
        validate_snapshot({**default_snapshot(),
                           "RETRIEVAL_W_VECTOR": float("nan")})
    with pytest.raises(ParamValidationError):
        validate_snapshot({**default_snapshot(),
                           "RETRIEVAL_W_VECTOR": float("inf")})


def test_duplicate_change_fails():
    with pytest.raises(ParamValidationError):
        apply_validated_changes(default_snapshot(), [
            ParamChange(name="RETRIEVAL_W_VECTOR", before=0.6, after=0.55,
                        reason="a"),
            ParamChange(name="RETRIEVAL_W_VECTOR", before=0.6, after=0.55,
                        reason="b"),
            ParamChange(name="RETRIEVAL_W_BM25", before=0.25, after=0.3,
                        reason="c")])


def test_legal_compensated_change_ok():
    """合法：向量降 0.05，BM25 升 0.05，总和不变。"""
    after = apply_validated_changes(default_snapshot(), [
        ParamChange(name="RETRIEVAL_W_VECTOR", before=0.6, after=0.55,
                    reason="paper evidence"),
        ParamChange(name="RETRIEVAL_W_BM25", before=0.25, after=0.30,
                    reason="paper evidence")])
    assert after["RETRIEVAL_W_VECTOR"] == 0.55
    assert after["RETRIEVAL_W_BM25"] == 0.30
    # 完整快照可再次通过（组合约束满足）
    validate_snapshot(after)


def test_legal_dedup_tune_ok():
    """合法：两个阈值同步下移，间距保持 0.15。"""
    after = apply_validated_changes(default_snapshot(), [
        ParamChange(name="DEDUP_MERGE_THRESHOLD", before=0.80, after=0.78,
                    reason="paper evidence"),
        ParamChange(name="DEDUP_UPDATE_THRESHOLD", before=0.65, after=0.63,
                    reason="paper evidence")])
    assert after["DEDUP_MERGE_THRESHOLD"] == 0.78
    validate_snapshot(after)


def test_math_helpers():
    """math 模块可用（防误删 import）。"""
    assert math.isfinite(1.0)
