"""评估指标冒烟测试——纯函数直调，零 DB，不 mock。

覆盖边界：空列表、全 zero_result、部分零结果、used_ids 命中/未命中/无数据、jaccard 空集合。
"""
from remembrance.eval.metrics import (
    avg_result_count, compute_metrics, jaccard_overlap, weak_hit_rate, zero_result_rate,
)


class TestZeroResultRate:
    def test_empty_returns_zero(self):
        assert zero_result_rate([]) == 0.0

    def test_all_zero(self):
        qs = [{"result_ids": []}, {"result_ids": []}, {"result_ids": []}]
        assert zero_result_rate(qs) == 1.0

    def test_none_zero(self):
        qs = [{"result_ids": ["a"]}, {"result_ids": ["b", "c"]}]
        assert zero_result_rate(qs) == 0.0

    def test_mixed(self):
        qs = [{"result_ids": ["a"]}, {"result_ids": []}, {"result_ids": ["b"]}, {"result_ids": []}]
        assert zero_result_rate(qs) == 0.5

    def test_zero_result_flag(self):
        qs = [{"zero_result": True}, {"zero_result": False, "result_ids": ["x"]}]
        assert zero_result_rate(qs) == 0.5


class TestAvgResultCount:
    def test_empty_returns_zero(self):
        assert avg_result_count([]) == 0.0

    def test_uniform(self):
        qs = [{"result_ids": ["a", "b"]}, {"result_ids": ["c", "d"]}]
        assert avg_result_count(qs) == 2.0

    def test_mixed(self):
        qs = [{"result_ids": ["a"]}, {"result_ids": []}, {"result_ids": ["b", "c", "d"]}]
        assert avg_result_count(qs) == 4 / 3


class TestJaccardOverlap:
    def test_empty_inputs_return_zero(self):
        assert jaccard_overlap([], []) == 0.0
        assert jaccard_overlap([["a"]], []) == 0.0
        assert jaccard_overlap([], [["a"]]) == 0.0

    def test_identical_returns_one(self):
        a = [["x", "y"], ["z"]]
        b = [["x", "y"], ["z"]]
        assert jaccard_overlap(a, b) == 1.0

    def test_disjoint_returns_zero(self):
        a = [["x"], ["y"]]
        b = [["z"], ["w"]]
        assert jaccard_overlap(a, b) == 0.0

    def test_partial_overlap(self):
        a = [["a", "b"], ["c", "d", "e"]]
        b = [["a", "c"], ["d", "e"]]
        # query1: {a,b} vs {a,c} -> inter {a}=1, union {a,b,c}=3 -> 1/3
        # query2: {c,d,e} vs {d,e} -> inter {d,e}=2, union {c,d,e}=3 -> 2/3
        expected = (1/3 + 2/3) / 2
        assert abs(jaccard_overlap(a, b) - expected) < 1e-9

    def test_empty_pair_skipped(self):
        a = [["a"], []]
        b = [["a"], []]
        # 第二对双空跳过，只看第一对 {a}vs{a}=1.0
        assert jaccard_overlap(a, b) == 1.0

    def test_one_empty_pair_counted_as_zero(self):
        a = [["a"], []]
        b = [["a"], ["x"]]
        # 第一对 1.0，第二对 {x} vs {} -> inter 0, union 1 -> 0
        assert jaccard_overlap(a, b) == 0.5


class TestWeakHitRate:
    def test_no_used_map_returns_none(self):
        qs = [{"event_id": "e1", "result_ids": ["a"]}]
        assert weak_hit_rate(qs) is None
        assert weak_hit_rate(qs, used_ids_map=None) is None
        assert weak_hit_rate(qs, used_ids_map={}) is None

    def test_empty_per_query_returns_none(self):
        assert weak_hit_rate([], used_ids_map={"e1": ["a"]}) is None

    def test_hit(self):
        qs = [{"event_id": "e1", "result_ids": ["a", "b"]}]
        used = {"e1": ["a"]}
        assert weak_hit_rate(qs, used_ids_map=used) == 1.0

    def test_miss(self):
        qs = [{"event_id": "e1", "result_ids": ["x", "y"]}]
        used = {"e1": ["a"]}
        assert weak_hit_rate(qs, used_ids_map=used) == 0.0

    def test_mixed_hits(self):
        qs = [
            {"event_id": "e1", "result_ids": ["a"]},
            {"event_id": "e2", "result_ids": ["x"]},
            {"event_id": "e3", "result_ids": ["c"]},
        ]
        used = {"e1": ["a"], "e2": ["nope"]}  # e3 无标注跳过
        # e1 命中(1/1)，e2 未命中(0/1)，e3 跳过
        assert weak_hit_rate(qs, used_ids_map=used) == 0.5

    def test_no_annotated_events_returns_none(self):
        qs = [{"event_id": "e1", "result_ids": ["a"]}]
        used = {"e2": ["x"]}  # e1 无标注
        assert weak_hit_rate(qs, used_ids_map=used) is None


class TestComputeMetrics:
    def test_empty_per_query(self):
        m = compute_metrics([])
        assert m["sample_count"] == 0
        assert m["zero_result_rate"] == 0.0
        assert m["weak_hit_rate"] is None
        assert m["jaccard_vs_baseline"] is None

    def test_full_metrics(self):
        qs = [
            {"event_id": "e1", "result_ids": ["a", "b"]},
            {"event_id": "e2", "result_ids": []},
            {"event_id": "e3", "result_ids": ["c"]},
        ]
        used = {"e1": ["a"], "e3": ["nope"]}
        m = compute_metrics(qs, used_ids_map=used)
        assert m["sample_count"] == 3
        assert m["zero_result_rate"] == round(1/3, 4)
        assert m["avg_result_count"] == round(3/3, 4)  # 2+0+1
        assert m["weak_hit_rate"] == 0.5
        assert m["jaccard_vs_baseline"] is None

    def test_with_baseline(self):
        qs = [{"result_ids": ["a", "b"]}, {"result_ids": ["c"]}]
        baseline = [["a", "b"], ["c", "d"]]
        m = compute_metrics(qs, baseline_per_query=baseline)
        # q1: {a,b}vs{a,b}=1.0; q2: {c}vs{c,d}=0.5 -> mean 0.75
        assert m["jaccard_vs_baseline"] == 0.75

    def test_no_used_data_honest_none(self):
        qs = [{"result_ids": ["a"]}]
        m = compute_metrics(qs)
        assert m["weak_hit_rate"] is None
