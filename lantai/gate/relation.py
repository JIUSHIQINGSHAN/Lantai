"""三态去重结构判别（校雠升级）：规则优先、LLM 兜底。

依据 prototype 实测（.scratch/dedup-threshold-calibration/report.md）：
单一余弦阈值无法分离 merge/update —— 判别信号在结构（哪些部分变了），
而非相似度高低。本模块在 cosine 预筛（prescreen）之后对中带样本判类：

- 新增值（归一化后） & 锚点比 ≥ DEDUP_ANCHOR_HIGH → update（同实体换值）
- 新增值 & 锚点比 < DEDUP_ANCHOR_LOW → insert（无锚点依据，宁 miss 不脏写）
- 无新增值 & 共享值非空 → merge（值完全相同 = 同事实）
- 无新增值 & 锚点比 ≥ DEDUP_ANCHOR_HIGH → merge（高重合改写）
- 其余中带 → LLM judge（注入调用方提供）；judge 缺席/异常 → insert

锚点 = 内容词（jieba 分词，滤停用/单字/标点/值）；值 = 归一化值片段
（日期/邮箱/域名/数字）。锚点排除值，避免值本身抬高重合度
（"公司域名" ~ "公司邮箱" 共享 example.com 的假阳性）。
"""
import re

import jieba

from lantai.core.settings import settings


def _lcut(text: str) -> list[str]:
    try:
        return jieba.lcut(text)
    except Exception:  # pragma: no cover - jieba 意外失败时退化为逐字
        return [c for c in text if c.strip()]


# 值片段：日期（含 号/日 归一）/ ISO 日期 / 邮箱 / 域名 URL / 数字 ≥2 位
_DATE_CN = r"\d{1,2}月\d{1,2}(?:日|号)?"
_DATE_ISO = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
_EMAIL = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
_DOMAIN = r"(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?:/[^\s，。；]*)?"
_NUM = r"\d{2,}"

_VALUE_PATTERNS = (_DATE_CN, _DATE_ISO, _EMAIL, _DOMAIN, _NUM)

_PUNCT = set("，。！？；：、（）()【】[]《》<>「」『』\"'·…—,.;:!?/\\\u3000 \t\n")
_STOP_WORDS = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "这", "那",
               "和", "与", "及", "或", "等", "就", "都", "也", "还", "很", "更",
               "把", "被", "给", "对", "从", "到", "往", "向", "于", "为", "因",
               "而", "且", "并", "但", "只", "有", "没", "不", "要", "会", "能",
               "上", "下", "中", "后", "前", "内", "外", "里", "月", "年", "日"}

# 地点值类（有限地名表，属领域数据而非配置）：城市迁移/变更类更新
# 的判别依据（"服务器在杭州机房" → "服务器迁移到上海机房"）
_LOCATIONS = {
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "重庆",
    "武汉", "西安", "天津", "长沙", "郑州", "青岛", "大连", "厦门", "合肥",
    "济南", "福州", "昆明", "南昌", "贵阳", "南宁", "海口", "太原", "石家庄",
    "哈尔滨", "长春", "沈阳", "香港", "澳门", "台北",
    "本地", "云端", "云", "线上", "线下", "国内", "海外", "国外",
}


def _keep(token: str) -> bool:
    t = token.strip()
    if len(t) < 2:
        return False
    if all(ch in _PUNCT for ch in t):
        return False
    return t not in _STOP_WORDS


def _value_spans(text: str) -> list[str]:
    spans: list[str] = []
    for pat in _VALUE_PATTERNS:
        for m in re.finditer(pat, text):
            v = m.group(0)
            # 日期归一：去掉 号/日 后缀（3月15号 == 3月15日）
            v = re.sub(r"(日|号)$", "", v)
            spans.append(v)
    return spans


def anchors_of(text: str) -> set[str]:
    """锚点词集：jieba 分词滤停用/单字/标点/值片段（含值子串泄漏），去重。"""
    spans = set(_value_spans(text)) | _location_tokens(text)
    keep: set[str] = set()
    for t in _lcut(text):
        if not _keep(t):
            continue
        if _is_value_token(t) or t in _LOCATIONS:
            continue
        if any(t in v or v in t for v in spans):
            continue  # 值片段子串（example/com 泄漏）不算锚点
        keep.add(t)
    return keep


def _is_value_token(token: str) -> bool:
    return any(re.fullmatch(pat, token) for pat in _VALUE_PATTERNS)


def _location_tokens(text: str) -> set[str]:
    return {t for t in _lcut(text) if t in _LOCATIONS}


def values_of(text: str) -> set[str]:
    """归一化值集合：日期/邮箱/域名/数字/地点。"""
    return set(_value_spans(text)) | _location_tokens(text)


def diff_values(old: str, new: str) -> set[str]:
    """新增值：新文本有、旧文本无（归一化后）。"""
    return values_of(new) - values_of(old)


def _judge_or_insert(llm_judge, old: str, new: str) -> str:
    if llm_judge is None:
        return "insert"
    try:
        rel = llm_judge(old, new)
    except Exception:
        return "insert"
    if rel not in ("merge", "update", "insert"):
        return "insert"
    return rel


def classify_relation(old_content: str, new_content: str,
                      *, llm_judge=None) -> str:
    """结构判类：merge / update / insert。

    规则优先（确定性、可测）；中带交 llm_judge（外部网络，测试注入桩）；
    judge 缺席/异常/非法返回值一律 insert（宁 miss 不脏写）。
    """
    oa, na = anchors_of(old_content), anchors_of(new_content)
    ov, nv = values_of(old_content), values_of(new_content)
    added = nv - ov

    ratio = len(oa & na) / len(oa) if oa else 0.0

    if added:
        if ratio >= settings.DEDUP_ANCHOR_HIGH:
            return "update"
        if ratio < settings.DEDUP_ANCHOR_LOW:
            return "insert"
    else:
        if ov & nv:
            return "merge"
        # ADR-0023 实质新词信号：旧锚点零丢失（改写是替换、扩展是增量）+
        # 新增实质词 ≥ 阈值 → 扩展事实走 update 提案（有刹车，不吞内容）
        dropped = oa - na
        extra = na - oa
        if not dropped and len(extra) >= settings.DEDUP_EXTRA_ANCHOR_LIMIT:
            return "update"
        if ratio >= settings.DEDUP_ANCHOR_HIGH:
            return "merge"
    return _judge_or_insert(llm_judge, old_content, new_content)
