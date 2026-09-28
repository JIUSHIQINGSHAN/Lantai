"""票 `.scratch/fts-null-owner/03`：`_keyword_fallback` 的 LIKE 兜底漏 `OR IS NULL`。

**先说影响**：LIKE 是关键词召回的**最后一层**。它的触发条件是
`(not candidate_ids or has_short_tokens)`（`hybrid.py:879`），
其中 **`has_short_tokens` 是日常路径**——中文单字词、缩写、型号
（"3080"、"RX"）都 <3 字符，trigram 成不了词。

此时 FTS 已召回 NULL 属主老行（票 01 修好了），`candidate_ids` 非空，
但 LIKE **另起一条严格 SQL**，把老行从它自己那半边剔掉。

真实库 96.8% 是 NULL 属主（636/657），所以短词查询 + 降级路径
= 老行召回被这一层单独抹掉。

探针实证（`.scratch/fts-null-owner/probe_like_fallback.py`）：
种三条（NULL 老行 / 别人的行 / 自己的行），查询词「华硕」（2 字符），
`principal=default` → 只剩自己的那条。
"""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage.fts import init_fts, sync_fts

# 短查询词：2 字符 → trigram 成不了词 → `has_short_tokens` 为真
# → LIKE 分支必然参与（这是本票的入口，不是边缘情况）
SHORT_QUERY = "华硕"

# 三个属主共用的正文前缀（LIKE 匹配靠它）
BODY = "华硕天选三笔记本"


def _principal(user_id, *, role="user"):
    return Principal(
        tenant_id=None,
        user_id=user_id,
        agent_id=None,
        session_id=None,
        role=role,
        allowed_lanes=None,
    )


@pytest.fixture
def engine():
    """真内存 SQLite + 真 FTS5 trigram 表（不 mock 检索逻辑）。"""
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as conn:
        init_fts(conn.connection.driver_connection)
    return e


def _seed(engine, mid: str, owner: str | None, suffix: str) -> None:
    """造一条记忆并同步 FTS（走真实 `sync_fts`，不直接插 FTS 表）。"""
    now = utcnow()
    content = f"{BODY}，{suffix}"
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"key-{mid}",
                content=content,
                namespace="default",
                status="active",
                tier="working",
                importance=0.5,
                confidence=1.0,
                reason="",
                role="OBSERVATION",
                lane="general",
                domain="general",
                version=1,
                use_count=0,
                helpful_count=0,
                decay_score=1.0,
                decay_class="slow",
                event_time_precision="none",
                lifecycle_status="ACTIVE",
                user_id=owner,
                created_at=now,
                updated_at=now,
            )
        )
        sync_fts(s, mid, content)
        s.commit()


def _run_like_fallback(engine, monkeypatch, query: str, principal):
    """直跑 `_keyword_fallback`（不 mock 其内部计算逻辑）。

    `session` 显式传入内存库，避免走 `db.get_session()` 绑到宿主真实库。
    """
    from lantai.retrieval.hybrid import RetrievalParams, _keyword_fallback

    with Session(engine) as s:
        return _keyword_fallback(
            query,
            top_k=50,
            fetch_n=50,
            memory_types=None,
            lanes=None,
            trace=False,
            trace_steps=[],
            t0=0.0,
            params=RetrievalParams(),
            session=s,
            principal=principal,
        )


class TestLikeFallbackNullOwnerVisible:
    def test_null_owner_row_survives_like_fallback(self, engine, monkeypatch):
        """短词查询下，NULL 属主老行必须过了 LIKE 这一关。

        **本测试是票 03 的 Red**：修前只召回自己的行，老行被
        LIKE 的严格 `user_id = ?` 单独滤掉。
        """
        _seed(engine, "mem-legacy", None, "RTX 3050 显卡")
        _seed(engine, "mem-B", "user-B", "另一台机器")

        res = _run_like_fallback(engine, monkeypatch, SHORT_QUERY, _principal("default"))
        docs = [r["document"] for r in res]

        assert any("RTX 3050" in d for d in docs), (
            f"LIKE 兜底把 NULL 属主老行滤掉了（短词查询老记忆全体消失）：{docs}"
        )
        assert not any("另一台机器" in d for d in docs), f"LIKE 兜底搜到了别人的行：{docs}"

    def test_own_row_still_recalled(self, engine, monkeypatch):
        """收窄不能把正常功能修废——自己的行照常召回。"""
        _seed(engine, "mem-A", "default", "我的机器")
        _seed(engine, "mem-B", "user-B", "另一台机器")

        res = _run_like_fallback(engine, monkeypatch, SHORT_QUERY, _principal("default"))
        docs = [r["document"] for r in res]

        assert any("我的机器" in d for d in docs), f"自己的行没召回了：{docs}"
        assert not any("另一台机器" in d for d in docs), f"搜到了别人的行：{docs}"

    def test_none_principal_still_unfiltered(self, engine, monkeypatch):
        """`principal=None` 仍全表——worker / 定时反思不能空转（票 06 口径）。"""
        _seed(engine, "mem-legacy", None, "RTX 3050 显卡")
        _seed(engine, "mem-B", "user-B", "另一台机器")

        res = _run_like_fallback(engine, monkeypatch, SHORT_QUERY, None)
        docs = [r["document"] for r in res]

        assert len(docs) == 2, f"principal=None 不再全表了：{docs}"
