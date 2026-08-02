"""
测试启发式相关性闸门
"""
import pytest
from remembrance.gate.prefilter import relevance_check


@pytest.fixture(autouse=True)
def _reset_gate_cache(monkeypatch):
    """隔离 prefilter 热缓存（15s TTL）跨测试污染：每个测试从冷缓存开始。

    注意：prefilter._update_cache 用 global 重新赋值 dict，必须 patch 模块属性本身。
    """
    import remembrance.gate.prefilter as pf
    monkeypatch.setattr(pf, "_LAST_GATE_DECISION",
                        {"time": 0.0, "query": "", "needs_memory": False})
    yield


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


class TestGateHotCache:
    """追问热缓存：上一轮需要记忆且新查询 <12 字符 → 沿用上一轮判定（设计行为）"""

    def test_followup_hot(self):
        relevance_check("上次我们聊的项目")  # 置缓存 needs_memory=True
        res = relevance_check("然后呢")
        assert res["needs_memory"] is True
        assert res["reason"] == "session_followup_hot"

    def test_followup_cold_after_long_query(self):
        relevance_check("上次我们聊的项目")
        res = relevance_check("一个超过十二个字符的追问内容")
        # 长查询不沿用热缓存，走完整判定
        assert res["reason"] != "session_followup_hot"
