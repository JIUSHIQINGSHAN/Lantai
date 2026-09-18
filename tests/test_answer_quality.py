"""回答层计分 + 新召回指标测试（P0 票05）。

测试纪律：judge / 聚合 / 指标纯函数直调不 mock；llm_judge 仅 mock 外部 LLM；
离线冒烟走 run_offline_eval（真实种子→检索→指标，仅 mock embed/向量/意图）。
"""

from lantai.eval.answer_quality import (
    compute_answer_metrics,
    llm_judge,
    rule_judge,
)
from lantai.eval.forgetting_quality import compute_forgetting_metrics


class TestRuleJudge:
    def test_full_hit(self):
        out = rule_judge("q", ["数据库使用SQLite存储"], ["SQLite"])
        assert out == {"hit_points": 1, "points": 1, "hit_rate": 1.0, "missed": []}

    def test_partial_hit_reports_missed(self):
        out = rule_judge("q", ["部署环境使用Docker容器"], ["Docker", "K8s"])
        assert out["hit_points"] == 1
        assert out["points"] == 2
        assert out["hit_rate"] == 0.5
        assert out["missed"] == ["K8s"]

    def test_total_miss(self):
        out = rule_judge("q", ["无关内容"], ["不存在"])
        assert out["hit_rate"] == 0.0
        assert out["missed"] == ["不存在"]

    def test_empty_key_points_honest_none(self):
        out = rule_judge("q", ["c"], [])
        assert out["hit_rate"] is None
        assert out["points"] == 0

    def test_multi_point_corpus_joined(self):
        """要点分散在多条记忆 → 合并语料后命中。"""
        out = rule_judge("q", ["数据库使用SQLite", "部署在华东机房"], ["SQLite", "华东机房"])
        assert out["hit_rate"] == 1.0


class TestLLMJudge:
    def test_llm_judge_covers(self, monkeypatch):
        import lantai.eval.answer_quality as aq

        monkeypatch.setattr(
            "lantai.eval.answer_quality.chat_json",
            lambda *a, **k: {"covered": [True, False]},
        )
        out = llm_judge("q", ["c"], ["要点1", "要点2"])
        assert out["hit_rate"] == 0.5
        assert out["judge"] == "llm"
        assert out["missed"] == ["要点2"]

    def test_llm_judge_malformed_falls_back_to_rule(self, monkeypatch):
        """判官输出畸形 → 降级 rule_judge（宁降级不编造）。"""
        import lantai.eval.answer_quality as aq

        monkeypatch.setattr(
            "lantai.eval.answer_quality.chat_json", lambda *a, **k: {"covered": "yes"}
        )
        out = llm_judge("q", ["部署环境使用Docker容器"], ["Docker"])
        assert out["judge"] == "rule_fallback"
        assert out["hit_rate"] == 1.0


class TestAnswerMetrics:
    def test_aggregation_by_points_weight(self):
        per_query = [
            {
                "category": "paraphrase",
                "answer": {"hit_points": 2, "points": 2, "hit_rate": 1.0, "missed": []},
            },
            {
                "category": "paraphrase",
                "answer": {"hit_points": 0, "points": 1, "hit_rate": 0.0, "missed": ["x"]},
            },
            {"category": "typo", "answer": {"hit_points": 0, "points": 0, "hit_rate": None}},
            {"category": "typo"},  # 无 answer（judge off）
        ]
        out = compute_answer_metrics(per_query)
        assert out["scored_queries"] == 2
        assert out["overall_hit_rate"] == round(2 / 3, 4)
        assert out["by_category"]["paraphrase"]["hit_rate"] == round(2 / 3, 4)
        assert "typo" not in out["by_category"]

    def test_empty_honest_none(self):
        out = compute_answer_metrics([])
        assert out["overall_hit_rate"] is None
        assert out["scored_queries"] == 0


class TestNewRecallMetrics:
    def test_metrics_include_new_categories(self):
        per_query = [
            {"category": "typo_mid", "query": "q", "result_ids": ["t"], "target_id": "t"},
            {"category": "typo_mid", "query": "q", "result_ids": [], "target_id": "t"},
            {"category": "paraphrase", "query": "q", "result_ids": ["t"], "target_id": "t"},
        ]
        m = compute_forgetting_metrics(per_query)
        assert m["typo_mid_recall_rate"] == 0.5
        assert m["paraphrase_recall_rate"] == 1.0
        # 既有指标不受影响（空维度诚实 0.0）
        assert m["stale_hit_rate"] == 0.0
        assert m["fresh_recall_rate"] == 0.0

    def test_offline_mini_dataset_unmocked_smoke(self):
        """不 mock 冒烟：离线全链（种子→检索→两层计分）真实执行。"""
        from lantai.eval.offline import run_offline_eval

        ds = {
            "name": "mini-v4-smoke",
            "namespace": "eval_fq",
            "apply_forgetting": True,
            "cases": [
                {
                    "category": "fresh",
                    "query": "喝无糖咖啡",
                    "seeds": [{"content": "用户偏好：喜欢喝无糖咖啡", "lane": "preference"}],
                    "target": 0,
                },
                {
                    "category": "paraphrase",
                    "query": "咖啡偏好是什么",
                    "seeds": [{"content": "用户偏好：喜欢喝无糖咖啡2", "lane": "preference"}],
                    "target": 0,
                    "key_points": ["无糖咖啡"],
                },
                {
                    "category": "typo_mid",
                    "query": "服务起动时加载配置",
                    "seeds": [{"content": "服务启动时加载配置文件", "lane": "fact"}],
                    "target": 0,
                },
            ],
        }
        result = run_offline_eval(ds, judge="rule")
        m = result["metrics"]
        assert m["fresh_recall_rate"] == 1.0
        assert "typo_mid_recall_rate" in m and "paraphrase_recall_rate" in m
        assert result["answer"]["scored_queries"] == 1
        # 离线 FTS-only 下 paraphrase 诚实记录（当前 AND 链兜底为 0，不编造）
        assert m["paraphrase_recall_rate"] == 0.0
        assert result["answer"]["overall_hit_rate"] == 0.0
