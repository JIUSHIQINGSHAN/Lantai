"""
测试启发式相关性闸门
"""
import pytest
from lantai.gate.prefilter import relevance_check


@pytest.fixture(autouse=True)
def _reset_gate_cache(monkeypatch):
    """隔离 prefilter 热缓存（15s TTL）跨测试污染：每个测试从冷缓存开始。

    注意：prefilter._update_cache 用 global 重新赋值 dict，必须 patch 模块属性本身。
    """
    import lantai.gate.prefilter as pf
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


class TestEntityKeywordsLazy:
    """实体词表惰性编译：import 后设置环境变量也生效（修静默零召回 bug）"""

    @pytest.fixture(autouse=True)
    def _reset_pattern_state(self, monkeypatch):
        import lantai.gate.prefilter as pf
        monkeypatch.setattr(pf, "_PATTERN_CACHE", {"key": None, "pattern": None})
        monkeypatch.setattr(pf, "_KEYWORD_WARNED", False)

    def test_new_env_name_works_after_import(self, monkeypatch):
        monkeypatch.setenv("REMEMBRANCE_ENTITY_KEYWORDS", "阿猫|阿狗")
        res = relevance_check("阿猫最近怎么样")
        assert res["needs_memory"] is True
        assert res["reason"] == "self_reference"

    def test_old_env_name_fallback(self, monkeypatch):
        monkeypatch.setenv("ENTITY_KEYWORDS", "莉莉")
        res = relevance_check("莉莉上次说的计划")
        assert res["needs_memory"] is True

    def test_new_name_overrides_old(self, monkeypatch):
        monkeypatch.setenv("ENTITY_KEYWORDS", "旧实体")
        monkeypatch.setenv("REMEMBRANCE_ENTITY_KEYWORDS", "新实体")
        res_old = relevance_check("旧实体在哪")
        res_new = relevance_check("新实体在哪")
        assert res_old["needs_memory"] is False  # 新名优先，旧词表不参与
        assert res_new["needs_memory"] is True

    def test_recompile_when_env_changes(self, monkeypatch):
        import lantai.gate.prefilter as pf
        relevance_check("你好世界")  # 无实体词表，编译 base 模式
        p_before = pf._self_reference_pattern()
        monkeypatch.setenv("REMEMBRANCE_ENTITY_KEYWORDS", "旺财")
        relevance_check("随便问问")  # 触发缓存键不匹配 → 重编译
        p_after = pf._self_reference_pattern()
        assert p_before is not p_after
        res = relevance_check("旺财今天去哪了")
        assert res["needs_memory"] is True

    def test_cached_pattern_reused(self):
        import lantai.gate.prefilter as pf
        p1 = pf._self_reference_pattern()
        p2 = pf._self_reference_pattern()
        assert p1 is p2  # 缓存命中，零重编译

    def test_missing_keywords_warns_once(self, monkeypatch, capsys, caplog):
        import logging
        import lantai.gate.prefilter as pf
        with caplog.at_level(logging.WARNING, logger="lantai.gate"):
            relevance_check("你好世界")
            relevance_check("你好世界")
        warn_msgs = [r.getMessage() for r in caplog.records
                     if "REMEMBRANCE_ENTITY_KEYWORDS" in r.getMessage()]
        assert len(warn_msgs) == 1  # 只告警一次，不重复刷屏
        err = capsys.readouterr().err
        assert "REMEMBRANCE_ENTITY_KEYWORDS" in err  # 不静默


class TestGateCacheInjectable:
    """cache/now 可注入 → pure function，并发/时序用例互不干扰"""

    def test_independent_caches(self):
        ca = {"time": 0.0, "query": "", "needs_memory": False}
        cb = {"time": 0.0, "query": "", "needs_memory": False}
        relevance_check("上次我们聊的项目", cache=ca, now=1000.0)  # ca 热
        res_a = relevance_check("然后呢", cache=ca, now=1001.0)
        res_b = relevance_check("然后呢", cache=cb, now=1001.0)  # cb 冷
        assert res_a["reason"] == "session_followup_hot"
        assert res_b["reason"] != "session_followup_hot"
        assert res_b["needs_memory"] is False

    def test_now_controls_ttl_expiry(self):
        import lantai.core.settings as s
        c = {"time": 0.0, "query": "", "needs_memory": False}
        relevance_check("上次我们聊的项目", cache=c, now=0.0)
        res = relevance_check("然后呢", cache=c, now=s.settings.GATE_CACHE_TTL + 1.0)
        assert res["reason"] != "session_followup_hot"

class TestGateUserIsolation:
    """Ticket 2.2 [DD-07]: Gate 15s hot cache must be isolated by user."""
    def test_user_isolation(self):
        # 模拟用户A在前一秒发了一个长句（触发热缓存）
        res_a1 = relevance_check("这是一个非常长的句子，包含很多实词，比如架构、系统、微服务等", user_id="userA", now=1.0)
        assert res_a1["needs_memory"] is True
        
        # 用户B在后一秒发了一个短句，不应该受用户A的热缓存影响
        res_b = relevance_check("这是啥", user_id="userB", now=2.0)
        assert res_b["needs_memory"] is False  # 没有受 userA 热缓存影响

        # 用户A在3秒时发了一个短句，应该受自己热缓存影响
        res_a2 = relevance_check("继续说", user_id="userA", now=3.0)
        assert res_a2["needs_memory"] is True
        assert res_a2["reason"] == "session_followup_hot"
