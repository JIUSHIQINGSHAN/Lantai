"""票 65：context._task_relevance 空白分词——中文 Jaccard 恒零，两面排序退化纯置信度。

工单：`.scratch/v0.68-next-window-2026-11/issues/` 65（context 任务相关度 CJK 分词）。

**先说影响**：中文文本没有空格，`.split()` 把整句切成一个长 token——任务描述与
记忆内容的 token 集交集恒空，相关度恒 0；`/cognitive/context` 与 `/context/prompt`
两面排序退化为纯 confidence/时间序，「任务相关」承诺失效。英语路径对标点同样
半残（"deploy!" 与 "deploy" 是两个 token，J=0）。

**mock 边界声明**（AGENTS.md 纪律）：`_task_relevance` 纯函数直调；排序臂真
SQLite 内存库 + 真 `CognitiveContextBuilder.build` 直调（db 由构造参数直接注入，
零 monkeypatch）；jieba 为既有声明依赖（pyproject，校雠 jiaochou.py 同源先例）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.cognition.context import CognitiveContextBuilder

_relevance = CognitiveContextBuilder._task_relevance
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem

_TASK = "部署服务上线"
_RELEVANT = "明天部署服务到生产环境"
_UNRELATED = "完全无关内容"


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


def _add_memory(engine, content: str, confidence: float) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="fact",
                content=content,
                lane="general",
                tier="long_term",
                status="active",
                role=CognitiveRole.OBSERVATION,
                confidence=confidence,
                importance=0.5,
                decay_class="semantic",
                user_id="default",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()
    return mid


class TestTaskRelevanceJaccard:
    """纯函数直调：jieba 分词后 Jaccard（校雠 jiaochou.py 同源口径，len>1 过滤）。"""

    def test_cjk_relevance_recovers(self):
        """TDD 1：中文同题异述 → 修复前恒 0（红），修复后 J=1/3 > 0.3（票面阈值）。"""
        score = _relevance(_RELEVANT, _TASK)
        assert score > 0.3, f"中文任务相关度恒 0（空白分词病）：score={score}，两面排序退化纯置信度"

    def test_unrelated_content_stays_zero(self):
        """区分度臂（两态皆绿）：无关内容 J=0，修复不得把噪声抬成正相关。"""
        assert _relevance(_UNRELATED, _TASK) == 0.0

    def test_empty_faces_zero_no_crash(self):
        """边界臂（两态皆绿）：空任务/空内容 → 0.0 且不炸。"""
        assert _relevance("", "") == 0.0
        assert _relevance(_TASK, "") == 0.0
        assert _relevance("", _TASK) == 0.0

    def test_english_punctuation_token_recovered(self):
        """英语标点臂：'deploy!' 与 'deploy' 同 token（修复前 J=0 红）；逗号句 J=1。"""
        assert _relevance("deploy!", "deploy") > 0, (
            "英语标点 token 半残：'deploy!' 与 'deploy' 判不相关"
        )
        assert _relevance("deploy, the service", "deploy the service") > 0.9, (
            "英语逗号 token 半残：带逗号形态与净形态判不相关"
        )


class TestContextOrderingRelevanceFirst:
    """排序臂：真库两条记忆 + 真 build——相关者优先恢复（修复前纯置信度序）。"""

    def test_relevant_memory_ranks_first(self, engine):
        relevant_id = _add_memory(engine, _RELEVANT, confidence=0.2)
        unrelated_id = _add_memory(engine, _UNRELATED, confidence=0.55)

        ctx = CognitiveContextBuilder(Session(engine)).build(_TASK)

        facts = ctx.facts
        assert len(facts) == 2, f"facts 切面应有两条：{[r.get('id') for r in facts]}"
        assert facts[0]["id"] == relevant_id, (
            f"相关度未参与排序（退化为纯置信度序）：facts={[r['id'] for r in facts]}，"
            f"relevant={relevant_id} unrelated={unrelated_id}"
        )
