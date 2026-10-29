"""探颐（ADR-0037 · v0.30）：主动探针与消歧机制冒烟测试。

验证：
1. settings.ENABLE_PROACTIVE_PROBING 配置项与出厂默认；
2. detect_memory_probes 检出未决冲突探针（suggested_action="resolve_conflict"）；
3. detect_memory_probes 检出模糊置信度事实探针（0.3 <= confidence <= 0.6, suggested_action="clarify_fact"）；
4. format_probing_context 生成 Prompt 注入上下文；
5. 真实 SQLite 数据库端到端不 mock 冒烟。
"""

import pytest

from lantai.core.settings import settings


def test_settings_enable_proactive_probing_default():
    """Seam 1 验证：ENABLE_PROACTIVE_PROBING 配置项存在且默认为 True。"""
    assert hasattr(settings, "ENABLE_PROACTIVE_PROBING")
    assert settings.ENABLE_PROACTIVE_PROBING is True


def test_detect_memory_probes_conflict_seam(param_env):
    """Seam 2 验证：扫描未决冲突生成探针，必须包含 suggested_action='resolve_conflict' 与完整字段。"""
    from lantai.models.tables import ConflictEvent, MemoryItem
    from lantai.services.probing_service import detect_memory_probes

    session_factory, _ = param_env
    with session_factory() as s:
        m = MemoryItem(
            id="mem_prov_01",
            content="大哥常驻地在上海徐汇区",
            lane="fact",
            domain="user",
            user_id="default",
            status="active",
        )
        s.add(m)
        s.commit()

        conf = ConflictEvent(
            id="conf_prov_01",
            memory_id="mem_prov_01",
            incoming_ref="大哥目前常住北京海淀区",
            rule_name="location_conflict",
            status="open",
        )
        s.add(conf)
        s.commit()

        # 调取检测
        probes = detect_memory_probes(query="请问大哥的常住地在哪里？", db_session=s)
        assert len(probes) == 1
        p = probes[0]
        assert p["probe_id"] == "probe_conf_prov_01"
        assert p["conflict_id"] == "conf_prov_01"
        assert p["memory_id"] == "mem_prov_01"
        assert "北京" in p["question"] or "上海" in p["question"]
        assert p["suggested_action"] == "resolve_conflict"


def test_detect_memory_probes_marginal_confidence_seam(param_env):
    """Seam 3 验证：对处于临界模糊带（0.3 <= confidence <= 0.6）的事实/记忆检出澄清探针，suggested_action='clarify_fact'。"""
    from lantai.models.tables import FactAtomic, MemoryItem
    from lantai.services.probing_service import detect_memory_probes

    session_factory, _ = param_env
    with session_factory() as s:
        # 1. 记忆正文：置信度 0.45（处于模糊带）
        m_vague = MemoryItem(
            id="mem_fuzzy_01",
            content="大哥可能在周五下午参与项目架构评审会议",
            lane="fact",
            domain="user",
            user_id="default",
            confidence=0.45,
            status="active",
        )
        # 2. 记忆正文：置信度 0.9（高度确信，非模糊）
        m_solid = MemoryItem(
            id="mem_solid_01",
            content="系统核心模块代码必须具备不 mock 冒烟单测",
            lane="rule",
            domain="user",
            user_id="default",
            confidence=0.9,
            status="active",
        )
        # 3. 完璧事实：置信度 0.5（处于模糊带）
        f_vague = FactAtomic(
            id="fact_fuzzy_01",
            statement="李四向张三汇报了云原生容器部署进度",
            predicate="report_to",
            object="张三",
            confidence=0.5,
            user_id="default",
        )
        s.add_all([m_vague, m_solid, f_vague])
        s.commit()

        # 针对 query 检出模糊探针
        probes = detect_memory_probes(query="周五架构评审与容器部署进度情况", db_session=s)
        # 应当检出 m_vague 与 f_vague 的澄清探针，solid 不被检出
        probe_ids = [p["probe_id"] for p in probes]
        assert "probe_mem_fuzzy_01" in probe_ids
        assert "probe_fact_fuzzy_01" in probe_ids
        assert "probe_mem_solid_01" not in probe_ids

        # 验证模糊探针字段
        fuzzy_p = next(p for p in probes if p["probe_id"] == "probe_mem_fuzzy_01")
        assert fuzzy_p["suggested_action"] == "clarify_fact"
        assert "周五下午" in fuzzy_p["question"] or "架构评审" in fuzzy_p["question"]
        assert fuzzy_p["conflict_id"] is None


def test_format_probing_context_seam():
    """Seam 4 验证：format_probing_context 上下文格式化规范性与空安全。"""
    from lantai.services.probing_service import format_probing_context

    # 1. 空探针
    assert format_probing_context([]) == ""

    # 2. 混合探针
    sample_probes = [
        {
            "probe_id": "probe_conf_01",
            "conflict_id": "conf_01",
            "question": "顺便向您求证确认下：关于常住地，目前是否已变更为北京？",
            "suggested_action": "resolve_conflict",
        },
        {
            "probe_id": "probe_mem_02",
            "conflict_id": None,
            "question": "顺便向您求证确认下：关于周五架构会议，该事实是否准确？",
            "suggested_action": "clarify_fact",
        },
    ]
    formatted = format_probing_context(sample_probes)
    assert "【探颐·待求证事项】" in formatted
    assert "conf_01" in formatted
    assert "probe_mem_02" in formatted or "澄清提示" in formatted or "该事实是否准确" in formatted
    assert "关于常住地，目前是否已变更为北京？" in formatted


def test_tanyi_probing_real_db_end_to_end_smoke(param_env, monkeypatch):
    """Seam 5 端到端不 mock 冒烟：
    真实 SQLite 内存表写入冲突记录与模糊带事实，调用 detect_memory_probes 验证：
    1. 探针完整生成且字段完备（probe_id, conflict_id, question, suggested_action）；
    2. 开关 settings.ENABLE_PROACTIVE_PROBING = False 时短路返回空；
    3. format_probing_context 上下文正确渲染。
    """
    from lantai.models.tables import ConflictEvent, FactAtomic, MemoryItem
    from lantai.services.probing_service import detect_memory_probes, format_probing_context

    session_factory, _ = param_env
    with session_factory() as s:
        # 1. 插入一条冲突
        mem_conf = MemoryItem(
            id="mem_e2e_conf",
            content="项目核心数据库选用 PostgreSQL",
            lane="rule",
            domain="user",
            user_id="default",
            status="active",
        )
        conf = ConflictEvent(
            id="conf_e2e_01",
            memory_id="mem_e2e_conf",
            incoming_ref="项目核心数据库已切换为 SQLite",
            rule_name="db_conflict",
            status="open",
        )
        # 2. 插入一条模糊事实
        fact_vague = FactAtomic(
            id="fact_e2e_01",
            statement="王五负责自动化测试用例评审",
            predicate="responsible_for",
            object="自动化测试",
            confidence=0.4,
            user_id="default",
        )
        s.add_all([mem_conf, conf, fact_vague])
        s.commit()

        # 正常开启状态检测
        probes = detect_memory_probes(query="自动化测试数据库方案", db_session=s)
        assert len(probes) >= 2
        actions = {p["suggested_action"] for p in probes}
        assert "resolve_conflict" in actions
        assert "clarify_fact" in actions

        # 检查上下文格式化
        context_str = format_probing_context(probes)
        assert "【探颐·待求证事项】" in context_str
        assert "conf_e2e_01" in context_str
        assert "fact_e2e_01" in context_str

        # 验证全局开关关闭时的短路
        monkeypatch.setattr(settings, "ENABLE_PROACTIVE_PROBING", False)
        probes_disabled = detect_memory_probes(query="自动化测试数据库方案", db_session=s)
        assert probes_disabled == []
