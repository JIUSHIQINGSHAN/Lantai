"""完璧（ADR-0057）多实体事实抽取纪律测试。"""

from lantai.parsing.extractor import extract_candidate


def test_extract_candidate_atomic_facts_field_default(monkeypatch):
    """验证抽取解析结果默认具备 atomic_facts 列表字段（即使 LLM 返回常规数据或异常）。"""

    def fake_chat_json(sys_prompt, user_prompt):
        return {
            "summary": "测试摘要",
            "claims": ["张三、李四、王五共同创办了兰图科技"],
            "atomic_facts": [
                {
                    "statement": "张三、李四、王五共同创办了兰图科技",
                    "predicate": "cofound",
                    "object": "兰图科技",
                    "entities": [
                        {"name": "张三", "role": "subject"},
                        {"name": "李四", "role": "subject"},
                        {"name": "王五", "role": "subject"},
                        {"name": "兰图科技", "role": "object"},
                    ],
                }
            ],
            "extractor_confidence": 0.9,
        }

    monkeypatch.setattr("lantai.parsing.extractor.chat_json", fake_chat_json)

    res = extract_candidate("创始团队", "张三、李四、王五于 2025 年共同创办了兰图科技。")
    assert "atomic_facts" in res
    assert len(res["atomic_facts"]) == 1
    fact = res["atomic_facts"][0]
    assert fact["statement"] == "张三、李四、王五共同创办了兰图科技"
    assert fact["predicate"] == "cofound"
    assert len(fact["entities"]) == 4


def test_extract_candidate_fallback_has_atomic_facts(monkeypatch):
    """验证当 LLM 出现网络/超时异常降级时，返回值依然保留 atomic_facts 兜底结构（不脏写、不报错）。"""

    def fake_chat_json_fail(sys_prompt, user_prompt):
        raise RuntimeError("LLM request timeout")

    monkeypatch.setattr("lantai.parsing.extractor.chat_json", fake_chat_json_fail)

    res = extract_candidate("异常降级测试", "这是一段测试文本")
    assert "atomic_facts" in res
    assert isinstance(res["atomic_facts"], list)
    assert len(res["atomic_facts"]) == 0
    assert res["extractor_confidence"] == 0.3
