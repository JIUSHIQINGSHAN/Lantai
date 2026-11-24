"""重构守护锁（第三批）：认知上下文的重复分词。

`CognitiveContextBuilder.build` 原实现对**每条**候选记忆都调一次
`_task_relevance(content, task)`，而该函数内部会 `jieba.cut(task)`——task 在
整次构建里不变，于是构建是 O(库容) 次对同一句子的分词。`/cognitive/context`
与 `/cognitive/context/prompt` 每次请求都走这条路。

这里锁两件事：
1. **等价性**：把任务词集提到外面只算一次，排序结果与逐条自算逐位一致；
2. **计数**：构建一次只对 task 分词一次（不靠耗时的运气）。

不 mock 产品逻辑：真实内存库 + 真实 jieba。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
from lantai.cognition import context as context_module
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem


@pytest.fixture
def builder_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    now = utcnow()
    contents = [
        "部署脚本放在内网跳板机上",
        "数据库连接池参数调优记录",
        "部署流程要求先跑全量测试",
        "用户偏好乌龙茶而非咖啡",
        "回滚方案需要预演两次",
    ]
    with Session(engine) as s:
        for idx, text in enumerate(contents):
            s.add(
                MemoryItem(
                    id=f"mem-{idx}",
                    key=f"k{idx}",
                    content=text,
                    role=CognitiveRole.OBSERVATION if idx % 2 else CognitiveRole.RULE,
                    lane="general",
                    status="active",
                    confidence=0.5 + idx * 0.05,
                    importance=0.5,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()
    with Session(engine) as s:
        from lantai.cognition.context import CognitiveContextBuilder

        yield CognitiveContextBuilder(db=s)


def _sections(ctx, key: str) -> list:
    return [m["id"] for m in getattr(ctx, key) or []]


def test_task_tokenized_once_per_build(builder_env, monkeypatch):
    """构建一次只对 task 分词一次（原实现是每条记忆一次）。"""
    calls: list[str] = []
    real = context_module._relevant_tokens

    def counting(text: str):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(context_module, "_relevant_tokens", counting)
    task = "部署脚本与数据库连接池"
    builder_env.build(task=task)

    task_calls = [c for c in calls if c == task]
    assert task_calls, "task 必须被分词（否则本锁空转）"
    assert len(task_calls) == 1, f"task 被分词 {len(task_calls)} 次，未提到循环外"


def test_sections_unchanged_vs_per_row_relevance(builder_env):
    """等价性：提前算任务词集不改变任何切面的顺序与内容。"""
    task = "部署脚本与数据库连接池"
    ctx = builder_env.build(task=task)

    # 参考实现：每条自算 task 词集（原行为）
    builder = builder_env
    memories = builder.db.exec(builder._owned_memory_stmt()).all()
    scored = sorted(
        (
            (
                0.6 * builder._task_relevance(m.content or "", task) + 0.4 * (m.confidence or 0.0),
                m.id,
            )
            for m in memories
        ),
        reverse=True,
    )
    expected_order = [mid for _, mid in scored]
    for key in ("facts", "rules", "experience", "beliefs", "principles"):
        got = _sections(ctx, key)
        # 每个切面里的条目顺序必须与参考打分一致（只取该切面的成员比顺序）
        want = [mid for mid in expected_order if mid in set(got)]
        assert got == want, f"切面 {key} 顺序漂移：{got} vs {want}"
