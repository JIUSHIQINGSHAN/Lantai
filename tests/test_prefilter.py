"""
测试启发式相关性闸门
"""
import pytest
from remembrance.gate.prefilter import relevance_check


class TestGateCorrection:
    """纠错/纠偏 → 一定需要记忆"""

    def test_correction_chinese(self):
        res = relevance_check("不对，不是这样的")
        assert res["needs_memory"] is True
        assert res["reason"] == "correction_detected"

    def test_correction_english(self):
        res = relevance_check("no, that's wrong")
        assert res["needs_memory"] is True


class TestGateSocialCloser:
    """纯社交结束语 → 不需要"""

    def test_ok(self):
        res = relevance_check("ok")
        assert res["needs_memory"] is False
        assert res["reason"] == "social_closer"

    def test_thanks(self):
        res = relevance_check("谢谢")
        assert res["needs_memory"] is False

    def test_bye(self):
        res = relevance_check("bye")
        assert res["needs_memory"] is False

    def test_chitchat(self):
        res = relevance_check("今天天气不错")
        assert res["needs_memory"] is False


class TestGateSelfReference:
    """自我指代 → 需要 Identity"""

    def test_my_name(self):
        res = relevance_check("我的名字是什么")
        assert res["needs_memory"] is True
        assert res["scope"] == "identity"

    def test_i_am(self):
        res = relevance_check("我是谁")
        assert res["needs_memory"] is True


class TestGateExplicitRecall:
    """明确回忆请求 → 需要 Episode"""

    def test_remember(self):
        res = relevance_check("记得我之前说的吗")
        assert res["needs_memory"] is True
        assert res["scope"] == "episode"

    def test_forgot(self):
        res = relevance_check("我忘了")
        assert res["needs_memory"] is True


class TestGateReference:
    """指代/延续 → 需要 Episode"""

    def test_last_time(self):
        res = relevance_check("上次我们聊的项目")
        assert res["needs_memory"] is True
        assert res["scope"] == "episode"

    def test_continue(self):
        res = relevance_check("继续说")
        assert res["needs_memory"] is True


class TestGateContentQuery:
    """有实质内容 → 需要 Pinned"""

    def test_long_query(self):
        res = relevance_check("请问Python和FastAPI有什么区别和联系")
        assert res["needs_memory"] is True
        assert res["scope"] == "pinned"

    def test_short_query(self):
        """短查询不需要"""
        res = relevance_check("Python是什么")
        assert res["needs_memory"] is False


class TestGateDefault:
    """默认 → 不需要"""

    def test_random(self):
        res = relevance_check("asdfg")
        assert res["needs_memory"] is False
        assert res["reason"] == "no_signal"

    def test_too_short(self):
        res = relevance_check("好")
        assert res["needs_memory"] is False
