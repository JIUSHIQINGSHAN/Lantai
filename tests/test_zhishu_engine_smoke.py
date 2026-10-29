"""测试直书（ZhishuEngine）多轨确定性规则引擎与程序约束结构化沉淀。

遵循 AGENTS.md 测试纪律：
- 真实调用 ZhishuEngine.parse()、ZhishuEngine.check()、ZhishuEngine.fastpath_check() 与 fastpath_check() 门面，不 mock 内部计算逻辑；
- 验证五大轨道（Fact、Preference、Constraint/Procedural、Config、General）；
- 验证宁 miss 不脏写（负向模糊句型坚决放行走 None）；
- 验证 100% 向后兼容性。
"""

import pytest

from lantai.parsing.fastpath import fastpath_check
from lantai.parsing.zhishu import ZhishuEngine


class TestZhishuEngineSmoke:
    """ZhishuEngine 核心轨道与边界无 mock 冒烟套件。"""

    def test_fact_rail(self):
        """Seam 1: Fact 轨 —— 用户身份、住址与职业声明。"""
        # 传统自我声明
        r1 = ZhishuEngine.parse("我叫张三")
        assert r1 is not None
        assert r1["lane"] == "fact"
        assert r1["rail"] == "fact"
        assert r1["extractor"] == "zhishu"
        assert r1["summary"] == "用户自称：张三"
        assert "张三" in r1["claims"]
        assert r1["extractor_confidence"] == 1.0

        # 职业声明
        r2 = ZhishuEngine.parse("我的职业是后端架构师")
        assert r2 is not None
        assert r2["lane"] == "fact"
        assert r2["rail"] == "fact"
        assert r2["summary"] == "用户职业：后端架构师"
        assert "后端架构师" in r2["claims"]

        # 现居/居住地声明
        r3 = ZhishuEngine.parse("我现居杭州")
        assert r3 is not None
        assert r3["lane"] == "fact"
        assert r3["rail"] == "fact"
        assert r3["summary"] == "用户居住地：杭州"
        assert "杭州" in r3["claims"]

    def test_preference_rail(self):
        """Seam 2: Preference 轨 —— 明确的偏好表达。"""
        r1 = ZhishuEngine.parse("我喜欢用 Python 编写测试用例")
        assert r1 is not None
        assert r1["lane"] == "preference"
        assert r1["rail"] == "preference"
        assert r1["summary"] == "用户偏好：用 Python 编写测试用例"

        r2 = ZhishuEngine.parse("我讨厌在生产环境盲目修改配置")
        assert r2 is not None
        assert r2["lane"] == "preference"
        assert r2["rail"] == "preference"
        assert r2["summary"] == "用户偏好：在生产环境盲目修改配置"

        r3 = ZhishuEngine.parse("我习惯晚上进行代码审查")
        assert r3 is not None
        assert r3["lane"] == "preference"
        assert r3["rail"] == "preference"
        assert r3["summary"] == "用户偏好：晚上进行代码审查"

    def test_constraint_procedural_rail(self):
        """Seam 3: Constraint 轨 —— 强约束与程序铁律，永不衰减规则。"""
        # 负向禁止铁律
        r1 = ZhishuEngine.parse("严禁在未获批准前删除任何审计日志")
        assert r1 is not None
        assert r1["lane"] == "rule"
        assert r1["rail"] == "constraint"
        assert r1["decay_class"] == "procedural"
        assert "未获批准前删除任何审计日志" in r1["constraints"][0]
        assert r1["summary"].startswith("程序约束：")

        r2 = ZhishuEngine.parse("切勿直接在主分支合并未通过门禁的代码")
        assert r2 is not None
        assert r2["lane"] == "rule"
        assert r2["rail"] == "constraint"
        assert r2["decay_class"] == "procedural"
        assert "直接在主分支合并未通过门禁的代码" in r2["constraints"][0]

        r3 = ZhishuEngine.parse("禁止未授权访问生产数据库")
        assert r3 is not None
        assert r3["lane"] == "rule"
        assert r3["rail"] == "constraint"
        assert "未授权访问生产数据库" in r3["constraints"][0]

        # 正向强制规范
        r4 = ZhishuEngine.parse("务必在每次发版前运行 release_check.py 脚本")
        assert r4 is not None
        assert r4["lane"] == "rule"
        assert r4["rail"] == "constraint"
        assert "在每次发版前运行 release_check.py 脚本" in r4["constraints"][0]

    def test_config_rail(self):
        """Seam 4: Config 轨 —— 键值与默认参数设置（支持带空格与无空格）。"""
        r1 = ZhishuEngine.parse("配置 max_workers 为 8")
        assert r1 is not None
        assert r1["lane"] == "rule"
        assert r1["rail"] == "config"
        assert r1["decay_class"] == "procedural"
        assert r1["summary"] == "配置设定：max_workers = 8"
        assert r1["config_key"] == "max_workers"
        assert r1["config_value"] == "8"

        # 无空格模式
        r2 = ZhishuEngine.parse("配置max_retries为3")
        assert r2 is not None
        assert r2["config_key"] == "max_retries"
        assert r2["config_value"] == "3"

        r3 = ZhishuEngine.parse("默认 embedding_model 是 text-embedding-3-small")
        assert r3 is not None
        assert r3["lane"] == "rule"
        assert r3["rail"] == "config"
        assert r3["config_key"] == "embedding_model"
        assert r3["config_value"] == "text-embedding-3-small"

        r4 = ZhishuEngine.parse("参数timeout设为30s")
        assert r4 is not None
        assert r4["lane"] == "rule"
        assert r4["rail"] == "config"
        assert r4["config_key"] == "timeout"
        assert r4["config_value"] == "30s"

    def test_general_rail(self):
        """Seam 5: General 轨 —— 显式备忘与记录指令。"""
        r1 = ZhishuEngine.parse("记住：每周五下午复盘发布质量")
        assert r1 is not None
        assert r1["lane"] == "general"
        assert r1["rail"] == "general"
        assert r1["summary"] == "每周五下午复盘发布质量"

        r2 = ZhishuEngine.parse("记一下 客户确认的迁移时间是下周二")
        assert r2 is not None
        assert r2["lane"] == "general"
        assert r2["rail"] == "general"
        assert r2["summary"] == "客户确认的迁移时间是下周二"

        r3 = ZhishuEngine.parse("备忘：核对七处单一版本真源")
        assert r3 is not None
        assert r3["lane"] == "general"
        assert r3["rail"] == "general"
        assert r3["summary"] == "核对七处单一版本真源"

    def test_ning_miss_bu_zang_xie_negative_cases(self):
        """Seam 6: 宁 miss 不脏写 —— 模糊、复杂、闲聊或长对话坚决不匹配（返回 None，放行走 LLM/闲聊过滤器）。"""
        # 闲聊 / 疑问
        assert ZhishuEngine.parse("今天天气怎么样？") is None
        assert ZhishuEngine.parse("你觉得哪个方案更好呢") is None
        # 太短
        assert ZhishuEngine.parse("好") is None
        assert ZhishuEngine.parse("我") is None
        assert ZhishuEngine.parse("好的收到") is None
        # 复杂复合句（含转折与逻辑词，不属于纯确定性直写）
        assert ZhishuEngine.parse("我虽然喜欢用 Python，但偶尔也写写 Go") is None
        assert ZhishuEngine.parse("如果明天不下雨的话我就去跑步") is None
        # 非直写指令的提及
        assert ZhishuEngine.parse("刚才有人跟我说记住密码很危险") is None

    def test_methods_contract_and_facade_compatibility(self):
        """Seam 7: 契约方法与门面向后兼容 —— ZhishuEngine.check / fastpath_check / 原 fastpath_check 函数。"""
        # check 与 fastpath_check 类方法别名
        r_check = ZhishuEngine.check("我叫王五")
        assert r_check is not None
        assert r_check["summary"] == "用户自称：王五"

        r_alias = ZhishuEngine.fastpath_check("我叫赵六")
        assert r_alias is not None
        assert r_alias["summary"] == "用户自称：赵六"

        # 传统 fastpath_check 门面
        res1 = fastpath_check("我叫李四")
        assert res1 is not None
        assert res1["fastpath"] is True
        assert res1["extractor"] == "zhishu"
        assert res1["lane"] == "fact"
        assert res1["summary"] == "用户自称：李四"
        assert res1["extractor_confidence"] == 1.0
        assert isinstance(res1["claims"], list)
        assert isinstance(res1["constraints"], list)

        # 传统显式指令
        res2 = fastpath_check("记住：明天开会")
        assert res2 is not None
        assert res2["lane"] == "general"
        assert res2["summary"] == "明天开会"

        # 不匹配案例
        assert fastpath_check("今天天气怎么样") is None
        assert fastpath_check("好") is None
