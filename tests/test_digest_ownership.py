"""Digest 读侧归属与宿主机信息脱敏：票 .scratch/readside-gaps/04

`GET /digest/today` 一个身份都不取，返回体里两样不该给任意持 key 者的东西：

1. **宿主机绝对路径**——`"path": "C:\\Users\\Asus\\Desktop\\记忆\\docs\\memory-digest\\..."`
   把操作系统用户名、部署位置、仓库结构一次交出。
2. **全库聚合统计量**——8 项计数无归属过滤。单人部署下是"我自己的统计"，
   多人用则 A 能看见 B 制造了多少记忆、多少待审。

比票 01/02/03 低一个量级：不涉及他人 content，不涉及 id oracle。
修法两路：路径脱敏（只回文件名）+ 统计量按归属收窄。

口径同票 03/04：NULL 属主老行**可见**（真实库 629/650 行 NULL，
单人部署，判不可见等于让报表归零）。
"""

from datetime import UTC, datetime, timedelta

import pytest

from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem


def _principal(user_id: str, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id="t1",
        allowed_lanes=["general", "fact"],
        role=role,
    )


def _mem(mid: str, user_id: str | None, content: str) -> MemoryItem:
    now = utcnow()
    return MemoryItem(
        id=mid,
        memory_type="semantic",
        namespace="default",
        key=f"key-{mid}",
        content=content,
        lane="fact",
        role="OBSERVATION",
        status="active",
        importance=0.5,
        decay_score=1.0,
        confidence=0.9,
        use_count=0,
        created_at=now,
        updated_at=now,
        user_id=user_id,
        tenant_id="t1" if user_id else None,
    )


def _cand(cid: str, user_id: str | None, summary: str) -> MemoryCandidate:
    now = utcnow()
    return MemoryCandidate(
        id=cid,
        tenant_id="t1" if user_id else None,
        user_id=user_id,
        document_id="doc-1",
        summary=summary,
        status="pending_review",
        extractor_confidence=0.9,
        lane="general",
        created_at=now,
        updated_at=now,
        review_due_at=now + timedelta(days=7),
    )


@pytest.fixture()
def digest_owned_env(tmp_path, monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session + 报告输出目录隔离。

    自包含：不 import `test_digest.py` 的 `digest_env`（测试模块之间不共享
    fixture，那是隐式依赖）。建表 / patch 方式与那个夹具逐字一致。
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(
        "lantai.workers.digest_worker._digest_dir",
        lambda: tmp_path / "digest",
    )

    with session_factory() as s:
        s.add(_mem("mem-A", "user-A", "A 的内容"))
        s.add(_mem("mem-B", "user-B", "B 的机密：连接池 100，数据库密码 hunter2"))
        s.add(_mem("mem-null", None, "老行：迁移前写入的记忆"))
        s.add(_cand("cand-A", "user-A", "A 的候选"))
        s.add(_cand("cand-B", "user-B", "B 的机密：连接池 100，数据库密码 hunter2"))
        s.commit()
    return session_factory


# ── Red 1（决定性）：绝对路径脱敏 ────────────────────────────────


class TestDigestHostInfoMasking:
    def test_path_is_filename_not_absolute(self, digest_owned_env):
        """`path` 只该是文件名——不能把用户名和部署位置交出去。

        两个分支都要锁：报告**已存在**（走 `load_today_digest` 读文件分支）
        与**不存在**（走 `run_digest_once` 生成分支）。第一版只随机命中一个
        分支，变异验证 M1 因此 MISSED——断言打在没执行到的那行上。
        """
        from lantai.workers.digest_worker import load_today_digest, run_digest_once

        # 分支 A：报告不存在 → run_digest_once 生成
        body_gen = run_digest_once(principal=_principal("user-A"))
        self._assert_filename_only(body_gen, "run_digest_once（生成分支）")

        # 分支 B：报告已存在 → load_today_digest 读文件
        body_read = load_today_digest(principal=_principal("user-A"))
        self._assert_filename_only(body_read, "load_today_digest（读取分支）")

    @staticmethod
    def _assert_filename_only(body: dict, where: str) -> None:
        assert body.get("ok") is True, body
        path_field = body.get("path", "")
        assert "\\" not in path_field and "/" not in path_field, (
            f"{where}：path 字段泄漏了绝对路径：{path_field!r}"
        )
        assert path_field.endswith(".md"), f"{where}：path 字段不该是绝对路径：{path_field!r}"
        assert "C:" not in str(body), f"{where}：响应体里出现了绝对路径：{str(body)[:300]!r}"

    def test_report_content_still_returned(self, digest_owned_env):
        """脱敏不能把报告本身弄丢（不能修废）。"""
        from lantai.workers.digest_worker import load_today_digest

        body = load_today_digest(principal=_principal("user-A"))
        assert body.get("content"), "报告正文没了——脱敏把功能修废了"
        assert "day" in body


# ── Red 2：统计量按归属收窄 ─────────────────────────────────────


class TestDigestStatsOwnership:
    def test_stats_exclude_other_users_rows(self, digest_owned_env):
        """A 看盘点：8 项计数只含 A 的行 + NULL 老行（逐项断言具体数字）。

        第一版只断言 `memories.total` 与 `pending.total` 两项，变异验证
        M2（`new_mem` 去归属）/M7（反思不传 principal）/M8（反思去归属）
        因此全 MISSED——那几项根本没被看。**判据必须覆盖每一个会变的量。**
        """
        from lantai.workers.digest_worker import collect_digest_stats

        stats = collect_digest_stats(principal=_principal("user-A"))
        assert stats["memories"]["total"] == 2, (
            f"A 的 total 该是 2（A 自己 + NULL 老行），实际 {stats['memories']['total']}"
        )
        assert stats["memories"]["new"] == 2, f"A 的 new 该是 2，实际 {stats['memories']['new']}"
        assert stats["pending"]["total"] == 1, (
            f"A 的 pending 该是 1（A 自己的候选），实际 {stats['pending']['total']}"
        )
        assert stats["pending"]["new_today"] == 1, (
            f"A 的 pending.new_today 该是 1，实际 {stats['pending']['new_today']}"
        )
        # 反思提案：种子没有 proposal，但这一项必须真的按 principal 过滤过
        assert stats["reflection"]["created"] == 0, (
            f"A 的反思计数该是 0，实际 {stats['reflection']['created']}"
        )

    def test_null_owner_rows_counted(self, digest_owned_env):
        """NULL 属主老行仍计入——单人部署下这是 629/650 行的真实数据。"""
        from lantai.workers.digest_worker import collect_digest_stats

        stats = collect_digest_stats(principal=_principal("user-A"))
        assert stats["memories"]["total"] == 2, "NULL 老行被藏了（报表会归零）"

    def test_admin_sees_all(self, digest_owned_env):
        """admin 照见全部（不能把运维报表修废）。"""
        from lantai.workers.digest_worker import collect_digest_stats

        stats = collect_digest_stats(principal=_principal("admin-1", role="admin"))
        assert stats["memories"]["total"] == 3, f"admin 看不到全部：{stats['memories']}"
        assert stats["pending"]["total"] == 2

    def test_internal_call_converges_to_default(self, digest_owned_env):
        """principal=None（内部 worker / MCP）按 "default" 收敛，不报错。"""
        from lantai.workers.digest_worker import collect_digest_stats

        stats = collect_digest_stats()
        # 种子没有 default 属主的行，只有 NULL 老行 → total 1
        assert stats["memories"]["total"] == 1, (
            f"内部调用该收敛到 default 并只算 NULL 老行，实际 {stats['memories']['total']}"
        )

    def test_route_forwards_identity(self, digest_owned_env):
        """REST 入口必须把身份传下去（M6：路由不下传 principal）。

        单列一条走真实 TestClient 的用例：前面几条都直调 worker 函数，
        绕过路由层——路由忘传 principal 时它们照样绿。判据是响应里的
        统计数字（A 只见 2 条记忆），不是"机密不在响应里"。
        """
        from fastapi.testclient import TestClient

        from lantai.api.app import app
        from lantai.core.auth import get_current_user

        app.dependency_overrides[get_current_user] = lambda: _principal("user-A")
        try:
            with TestClient(app) as c:
                resp = c.get("/digest/today")
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stats"]["memories"]["total"] == 2, (
            f"路由没把身份传下去：A 看见了 {body['stats']['memories']['total']} 条记忆"
        )
        assert "\\" not in body["path"] and "/" not in body["path"], (
            f"REST 响应泄漏了绝对路径：{body['path']!r}"
        )

    def test_reflection_stats_exclude_other_users(self, digest_owned_env):
        """反思提案计数也按归属收窄——`_aggregate_reflection` 的 7 个查询。

        单列一条：M7（不传 principal）/M8（查询里去归属条件）只在这条
        能被抓住。种子给 A 和 B 各一条 reflect 提案，A 只该看见自己的。
        """
        from lantai.core.time import utcnow
        from lantai.models.tables import MemoryProposal
        from lantai.workers.digest_worker import collect_digest_stats

        now = utcnow()
        with digest_owned_env() as s:
            for uid, pid in (("user-A", "prop-A"), ("user-B", "prop-B")):
                s.add(
                    MemoryProposal(
                        id=pid,
                        proposal_type="merge",
                        candidate_id=None,
                        decided_by="reflect",
                        evidence_ids=["e1"],
                        proposed_patch={"key": pid},
                        confidence=0.9,
                        status="applied",
                        created_at=now,
                        updated_at=now,
                        user_id=uid,
                        tenant_id="t1",
                    )
                )
            s.commit()

        stats_a = collect_digest_stats(principal=_principal("user-A"))
        assert stats_a["reflection"]["created"] == 1, (
            f"A 的反思计数该是 1（只看自己的），实际 {stats_a['reflection']['created']}"
        )
        assert stats_a["reflection"]["applied"] == 1
        assert stats_a["reflection"]["by_type"] == {"merge": {"applied": 1}}, (
            f"A 的反思分布混入了 B 的提案：{stats_a['reflection']['by_type']}"
        )

        stats_admin = collect_digest_stats(principal=_principal("admin-1", role="admin"))
        assert stats_admin["reflection"]["created"] == 2, (
            f"admin 该看见全部反思提案，实际 {stats_admin['reflection']['created']}"
        )
