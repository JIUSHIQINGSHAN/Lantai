"""结构判类（校雠三态去重升级）测试：36 对回归集，规则层真实 jieba 不 mock。

样本源自 .scratch/dedup-threshold-calibration/report.md（prototype 实测）：
单一余弦阈值无法分离 merge/update，判别信号在结构。
middle 带（锚点比中低）交 LLM judge —— 测试注入桩 judge（仅外部网络属 mock）。
"""
import pytest

from lantai.gate.relation import (
    classify_relation, anchors_of, values_of, diff_values,
)

# 期望表：A 改写 → merge；B 同实体更新 → update；C 不相关 → insert
# judge 桩：middle 带按样本期望返回（模拟 LLM 兜底的正确裁决）
_EXPECT = {
    # --- A：近义改写 → merge ---
    ("项目截止日期是3月15号", "项目截止日期为3月15日"): "merge",
    ("我喜欢喝无糖咖啡", "我平时都喝无糖咖啡"): "merge",
    ("公司域名是 example.com", "我们公司的域名是 example.com"): "merge",
    ("周会时间定在每周一上午10点", "每周一上午十点开周会"): "merge",
    ("我的生日是1995年6月1日", "我出生于1995年6月1日"): "merge",
    ("项目使用Python开发", "这个项目是用Python写的"): "merge",
    ("我住在北京朝阳区", "我居住在北京朝阳区"): "merge",
    ("数据库使用SQLite", "存储用的是SQLite数据库"): "merge",
    ("我喜欢吃辣", "我偏爱辣味食物"): "merge",
    ("明天下午三点开会", "会议在明天下午3点举行"): "merge",
    ("用户名为admin", "登录账号是admin"): "merge",
    ("我对海鲜过敏", "我吃海鲜会过敏"): "merge",
    # --- B：同实体更新 → update ---
    ("项目截止日期是3月15号", "项目截止日期推迟到4月1号"): "update",
    ("周会时间定在每周一上午10点", "周会时间改到每周五下午3点"): "update",
    ("我的邮箱是 a@b.com", "我的新邮箱是 c@d.com"): "update",
    # 已知限制：全换词更新（锚点零重合）规则无依据 → 中带交 judge 裁决（桩返回 insert），
    # judge 缺席/失败时降级 insert，宁 miss 不脏写
    ("我在腾讯工作", "我跳槽去了字节跳动"): "insert",
    ("项目使用Python开发", "项目改用Go语言重写"): "update",
    ("我的手机号是13800000000", "我的手机号换成了13900000000"): "update",
    ("部署环境是本地服务器", "部署环境迁移到了云服务器"): "update",
    ("预算大约10万元", "预算调整为15万元"): "update",
    ("数据库使用SQLite", "数据库迁移到了PostgreSQL"): "update",
    ("负责人是小明", "项目负责人换成了小红"): "update",
    ("版本号是v0.14.0", "版本号升级到v0.15.0"): "update",
    ("服务器在杭州机房", "服务器迁移到上海机房"): "update",
    # --- C：不相关 / 同主题异事实 → insert ---
    ("项目截止日期是3月15号", "我喜欢吃辣"): "insert",
    ("周会时间定在每周一上午10点", "我对海鲜过敏"): "insert",
    ("公司域名是 example.com", "我住在北京朝阳区"): "insert",
    ("项目使用Python开发", "我的生日是1995年6月1日"): "insert",
    ("我住在北京朝阳区", "预算大约10万元"): "insert",
    ("数据库使用SQLite", "明天下午三点开会"): "insert",
    ("负责人是小明", "用户名为admin"): "insert",
    ("服务器在杭州机房", "我喜欢喝无糖咖啡"): "insert",
    ("版本号是v0.14.0", "部署环境是本地服务器"): "insert",
    ("项目使用Python开发", "项目文档用英文写"): "insert",
    ("周会时间定在每周一上午10点", "周会由小明主持"): "insert",
    ("公司域名是 example.com", "公司邮箱是 hr@example.com"): "insert",
    # --- ADR-0023 实质新词扩展信号（old⊆new 旧锚点零丢失 + 新增实质词 ≥ 2）---
    ("数据库使用SQLite", "数据库使用SQLite并且迁移到了PostgreSQL"): "update",
    ("项目使用Python开发", "项目使用Python开发并且加入了测试框架"): "update",
    # 对照组：仅轻微加词（新增实质词 < 阈值）→ 维持 merge
    ("我喜欢喝无糖咖啡", "我非常喜欢喝无糖咖啡"): "merge",
}


def _judge_stub(old: str, new: str) -> str:
    """middle 带桩 judge：按期望表返回（模拟 LLM 兜底正确裁决）。"""
    exp = _EXPECT.get((old, new))
    if exp is None:
        raise AssertionError(f"judge 收到期望表外样本: {old!r} ~ {new!r}")
    return exp


@pytest.mark.parametrize("pair", sorted(_EXPECT.keys()))
def test_classify_36_pairs(pair):
    old, new = pair
    expected = _EXPECT[pair]
    got = classify_relation(old, new, llm_judge=_judge_stub)
    assert got == expected, f"{old!r} ~ {new!r}: got {got}, want {expected}"


def test_no_judge_falls_back_to_insert():
    """middle 带无 judge（LLM 关/失败）→ insert（宁 miss 不脏写）。"""
    old, new = "项目使用Python开发", "项目改用Go语言重写"
    # 锚点比 0.25 无共享值 → judge 缺席 → insert
    assert classify_relation(old, new) == "insert"


def test_judge_exception_falls_back_to_insert():
    """judge 抛异常 → insert，不污染写入路径。"""

    def boom(o, n):
        raise RuntimeError("llm down")

    assert classify_relation("项目使用Python开发", "项目改用Go语言重写",
                             llm_judge=boom) == "insert"


def test_anchors_exclude_values():
    """锚点应排除值（域名/数字/邮箱），值由 values_of 独立捕获。"""
    toks = anchors_of("公司域名是 example.com")
    assert "example.com" not in toks
    assert "公司" in toks and "域名" in toks


def test_values_normalize_dates():
    """日期归一化：号/日 后缀等价（3月15号 == 3月15日）。"""
    assert values_of("项目截止日期是3月15号") == values_of("项目截止日期为3月15日")


def test_diff_values():
    assert diff_values("我的手机号是13800000000", "我的手机号换成了13900000000") == {"13900000000"}
    assert diff_values("周会时间定在每周一上午10点", "每周一上午十点开周会") == set()
