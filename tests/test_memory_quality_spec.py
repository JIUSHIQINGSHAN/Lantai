"""评测集规格防漂移锁定（AGENTS.md 测试纪律：核心主张不 mock）。

两道防线：
1. 数据集自身统计与期望分布一致（name / 总数 / 分类计数）。
2. 规格文档 `docs/memory-quality/chinese-memory-v1.md` 必须包含与代码同源的
   计数文本（期望值由 build_chinese_dataset 推导，非手抄）——case 增删/改名
   会同时打破断言 1 与 2，测试失败即文档过期，逼文档同步（宁 miss 不脏写）。
"""
from pathlib import Path

from lantai.eval.chinese_memory_cases import build_chinese_dataset

SPEC_DOC = (Path(__file__).parent.parent
            / "docs" / "memory-quality" / "chinese-memory-v1.md")

EXPECTED = {
    "name": "chinese-memory-v2",
    "total": 80,
    "categories": {"typo": 23, "fresh": 18, "stale": 14,
                   "temporal": 13, "superseded": 12},
}


def test_dataset_stats_match_expected():
    """第一道防线：数据集本身符合规格声明。"""
    ds = build_chinese_dataset()
    from collections import Counter
    cats = Counter(c["category"] for c in ds["cases"])
    assert ds["name"] == EXPECTED["name"]
    assert len(ds["cases"]) == EXPECTED["total"]
    assert dict(cats) == EXPECTED["categories"]


def test_spec_doc_counts_match_dataset():
    """第二道防线：规格文档计数与代码同源（防文档漂移）。"""
    if not SPEC_DOC.exists():
        raise AssertionError(f"规格文档缺失: {SPEC_DOC}")
    text = SPEC_DOC.read_text(encoding="utf-8")
    ds = build_chinese_dataset()
    from collections import Counter
    cats = Counter(c["category"] for c in ds["cases"])
    # 期望文本由代码推导——case 变化时此处自动变，文档不更新则失败
    assert f"{len(ds['cases'])} case" in text
    assert ds["name"] in text
    for cat in ("typo", "fresh", "stale", "temporal", "superseded"):
        assert f"{cat}×{cats[cat]}" in text, f"文档缺 {cat}×{cats[cat]} 计数"
