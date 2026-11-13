"""直书（ZhishuEngine）——白名单高频确定性句型直写规则引擎。

《左传·成公十四年》「董狐，古之良史也，书法不隐」，成语「秉笔直书」——
直言直记，不加雕琢，不经润色，不假外求。

核心职能：
针对用户自称事实、偏好表达、程序约束/铁律、键值配置、显式备忘等高频确定性句式，
绕过外部大模型直接完成秒级结构化提取，置信度 1.0，0 Token 开销。

原则：
宁 miss 不脏写（precision ≥ 95%，不设 recall；模糊/疑问/复合句坚决放行给大模型）。
"""

import re
from typing import Any

# 疑问句与逻辑复合句黑名单（宁 miss 不脏写）
_NEGATIVE_SUFFIXES = ("?", "？", "吗", "呢", "么")
_NEGATIVE_KEYWORDS = (
    "虽然",
    "但是",
    "如果",
    "假如",
    "要是",
    "不过",
    "然而",
    "听别人说",
    "有人说",
    "刚才有人",
)

# 票 56：反问/疑问形状负向检查——`我是`/`我叫` 等自称前缀后紧跟疑问形态
# 即整句放行给大模型（不截断不猜测）。句尾后缀表只看句尾，反问句换任何
# 句尾即漏放（`我是不是该辞职了` 被写成「用户自称：不是该辞职了」）；
# `我是不是搞错了什么` 现靠句尾「么」侥幸 None，须转为形状判定。
_RHETORICAL_SHAPES = re.compile(
    r"^(?:我的名字|我的职业|我是|我叫|我住在|我现居|我常住|我定居|我从事|我是一名?)\s*[:：]?\s*(?:是不是|不是|还是|算不算|难道|究竟|怎么|为什么|为何|啥|什么)"
)

# 票 56：兼语/来源形状——自称捕获值以代词开头（「我叫他…」的「他明天来
# 开会」是兼语句，「我叫」非自称）或「从…来/来的」来源结构开头（「我是
# 从上海来的」是来源说明非自称）时放行。按宁 miss 从宽，只覆盖显然族。
# （单字前缀已覆盖复数项「他们/她们/它们」的 startswith 语义，不重复列。）
_PRONOUN_PREFIXES = ("他", "她", "它", "你", "我")
# 来源结构（预编译，与本文件既有正则同为模块级常量；「从…来」贪心已
# 覆盖「来的/而来」尾，不设冗余分支）
_SOURCE_SHAPE_RE = re.compile(r"^从.+(?:来|而来)")

# 1. Fact 轨：用户自称、职业、居住地（注意：更具体的职业优先于通用自称，防“我是一名”被“我是”前缀截胡）
_FACT_RULES = [
    (
        [
            re.compile(r"^(?:我(?:的职业(?:是|为)?)|我从事|我是一名?)\s*[:：]?\s*(.+)$"),
        ],
        "用户职业",
    ),
    (
        [
            re.compile(r"^(?:我(?:的名字(?:是|叫)?)|我是|我叫)\s*[:：]?\s*(.+)$"),
        ],
        "用户自称",
    ),
    (
        [
            re.compile(r"^(?:我(?:现居|住在|常住|定居(?:在|于)?))\s*[:：]?\s*(.+)$"),
        ],
        "用户居住地",
    ),
]

# 2. Preference 轨：偏好表达
_PREFERENCE_PATTERNS = [
    re.compile(r"^我(?:喜欢|不喜欢|讨厌|偏爱|偏好|习惯)\s*[:：]?\s*(.+)$"),
]

# 3. Constraint / Procedural 轨：程序铁律与负向约束
_CONSTRAINT_PATTERNS = [
    # 负向禁止铁律
    re.compile(r"^(?:严禁|禁止|切勿|千万别|千万不要|绝不(?:能|允许)?)\s*[:：]?\s*(.+)$"),
    # 正向强制规范
    re.compile(r"^(?:务必|必须|坚决)\s*[:：]?\s*(.+)$"),
]

# 4. Config 轨：配置参数与默认值（支持有空格与无空格）
_CONFIG_PATTERNS = [
    re.compile(r"^(?:配置|参数|默认)\s*([a-zA-Z0-9_\-\.]+)\s*(?:为|是|设为|:=|=)\s*(.+)$"),
    re.compile(r"^(?:把|将)?\s*([a-zA-Z0-9_\-\.]+)\s*(?:配置为|设为|设置为)\s*(.+)$"),
]

# 5. General 轨：显式备忘与记录指令
_GENERAL_PATTERNS = [
    re.compile(r"^(?:记住|记一下|备注|记录|备忘)\s*[:：]?\s*(.+)$"),
]


class ZhishuEngine:
    """直书确定性规则直写引擎。"""

    @classmethod
    def parse(cls, content: str) -> dict[str, Any] | None:
        """解析文本。若命中确定性规则则返回结构化结果，否则返回 None。"""
        if not content:
            return None
        text = content.strip()
        if len(text) < 3:
            return None

        # 负向检查 1：疑问句尾缀
        if text.endswith(_NEGATIVE_SUFFIXES):
            return None

        # 负向检查 2：逻辑转折/条件复合句/转述
        for kw in _NEGATIVE_KEYWORDS:
            if kw in text:
                return None

        # 负向检查 3（票 56）：反问/疑问形状——自称前缀后紧跟疑问形态，
        # 形状判定取代句尾后缀侥幸（句尾换字不再翻车），整句放行给大模型
        if _RHETORICAL_SHAPES.match(text):
            return None

        # --- 轨 1: Fact 自我事实 ---
        for pat_list, prefix in _FACT_RULES:
            for pat in pat_list:
                m = pat.match(text)
                if m:
                    val = m.group(1).strip()
                    if val:
                        # 负向检查 4（票 56）：兼语/来源形状——捕获值以代词
                        # 开头（兼语句）或「从…来/来的」来源结构开头（来源
                        # 说明）时放行，宁 miss 不脏写。直接 return None：
                        # release 必须终结整个 fact 轨——若只 break 内层，
                        # 句子会落进下一条更不具体的自称轨被改道吞掉
                        # （`我是一名他尊敬的老师` → 职业轨 release → 自称轨
                        # 仍脏写「一名他尊敬的老师」，rail 还换了）。
                        if val.startswith(_PRONOUN_PREFIXES) or _SOURCE_SHAPE_RE.match(val):
                            return None
                        return cls._build_result(
                            lane="fact",
                            rail="fact",
                            summary=f"{prefix}：{val}",
                            claims=[val],
                        )

        # --- 轨 2: Preference 偏好设定 ---
        for pat in _PREFERENCE_PATTERNS:
            m = pat.match(text)
            if m:
                val = m.group(1).strip()
                if val:
                    return cls._build_result(
                        lane="preference",
                        rail="preference",
                        summary=f"用户偏好：{val}",
                    )

        # --- 轨 3: Constraint 规则约束与铁律 ---
        # lane 取系统标准 rule 分轨，decay_class 置 procedural 实现永不衰减
        for pat in _CONSTRAINT_PATTERNS:
            m = pat.match(text)
            if m:
                val = m.group(1).strip()
                if val:
                    return cls._build_result(
                        lane="rule",
                        rail="constraint",
                        summary=f"程序约束：{val}",
                        constraints=[val],
                        decay_class="procedural",
                    )

        # --- 轨 4: Config 配置设定 ---
        for pat in _CONFIG_PATTERNS:
            m = pat.match(text)
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                if key and val:
                    res = cls._build_result(
                        lane="rule",
                        rail="config",
                        summary=f"配置设定：{key} = {val}",
                        decay_class="procedural",
                    )
                    res["config_key"] = key
                    res["config_value"] = val
                    return res

        # --- 轨 5: General 显式备忘 ---
        for pat in _GENERAL_PATTERNS:
            m = pat.match(text)
            if m:
                val = m.group(1).strip()
                if val:
                    return cls._build_result(
                        lane="general",
                        rail="general",
                        summary=val,
                    )

        return None

    @classmethod
    def check(cls, content: str) -> dict[str, Any] | None:
        """检查并提取直书规则（契约别名，等价于 parse）。"""
        return cls.parse(content)

    @classmethod
    def fastpath_check(cls, content: str) -> dict[str, Any] | None:
        """门面兼容别名，等价于 parse。"""
        return cls.parse(content)

    @classmethod
    def _build_result(
        cls,
        lane: str,
        rail: str,
        summary: str,
        claims: list[str] | None = None,
        constraints: list[str] | None = None,
        decay_class: str | None = None,
    ) -> dict[str, Any]:
        """构建统一的直书提取结构体。"""
        res: dict[str, Any] = {
            "fastpath": True,
            "extractor": "zhishu",
            "rail": rail,
            "lane": lane,
            "summary": summary,
            "claims": claims or [],
            "methods": [],
            "constraints": constraints or [],
            "actions": [],
            "topic": [],
            "extractor_confidence": 1.0,
        }
        if decay_class:
            res["decay_class"] = decay_class
        return res
