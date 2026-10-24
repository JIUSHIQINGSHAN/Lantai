"""认知上下文 status==active 过滤：票 review-fixes-2026-10/06

`CognitiveContextBuilder._owned_memory_stmt` 此前只有归属条件、没有
`status == "active"`——retracted/archived/consolidated 的记忆全部进入
`/cognitive/context` 并经 `/context/prompt` 注入 Agent 提示词，而全库
其余读路径（`hybrid.py` 三处）均只吐 active。

测试策略同 `test_cognitive_context_read_ownership.py`：走 `param_env`
的真实内存库 + 真实 FTS + 真实路由栈（TestClient），**不 mock 被测
内部逻辑**；撤回走真实 `record_ops_service.retract_memory`（状态翻转/
索引清理/审计全程真实执行），归属见 route 层 Principal。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, MemoryItem
from lantai.services.record_ops_service import (
    STATUS_ARCHIVED,
    STATUS_CONSOLIDATED,
    STATUS_RETRACTED,
    retract_memory,
)

SECRET_RETRACTED = "已撤回的机密：连接池上限 999，口令 ghost-pass"
SECRET_ARCHIVED = "已归档的旧闻：连接池曾是 128"
SECRET_CONSOLIDATED = "已巩固的碎片：连接池 256 被合并"
ALIVE = "现行有效的记忆：连接池 500"


def _principal(user_id: str, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id="t1",
        allowed_lanes=["general", "fact"],
        role=role,
    )


def _seed(session_factory, rows) -> None:
    """rows: (mid, content, status, user_id)——管道状态是本票的输入变量。"""
    now = utcnow()
    with session_factory() as s:
        for mid, content, status, uid in rows:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    namespace="default",
                    key=f"key-{mid}",
                    content=content,
                    lane="fact",
                    role=CognitiveRole.OBSERVATION,
                    status=status,
                    importance=0.5,
                    decay_score=1.0,
                    confidence=0.9,
                    use_count=0,
                    created_at=now,
                    updated_at=now,
                    user_id=uid,
                    tenant_id="t1",
                )
            )
        s.commit()


@pytest.fixture()
def app_isolated(param_env):
    """挂真实 app，DB 依赖指到 param_env 内存库；用完即还原。

    override 的 key 必须用路由自己绑定的函数对象（`routes_cognitive.get_session`），
    详见 test_cognitive_context_read_ownership.py 同名 fixture 的踩坑记录。
    """
    from lantai.api import routes_cognitive as routes_cognitive_mod
    from lantai.api.app import app

    session_factory, _ = param_env

    def _override_session():
        yield session_factory()

    route_dep = routes_cognitive_mod.get_session
    app.dependency_overrides[route_dep] = _override_session
    yield app
    app.dependency_overrides.pop(route_dep, None)


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _ids(items) -> list[str]:
    return [x.get("id") for x in items]


class TestCognitiveContextActiveFilter:
    def test_retracted_memory_not_injected(self, param_env, app_isolated):
        """票面验收主路径：真实撤回一条记忆后，/cognitive/context 与
        /cognitive/context/prompt 都不再包含其正文——行仍在库（供审计），
        是被读侧过滤掉，不是被删掉。"""
        session_factory, _ = param_env
        _seed(session_factory, [("mem-a", SECRET_RETRACTED, "active", "user-A")])

        with TestClient(app_isolated) as c:
            # 撤回前：现行记忆该被看见（对照组，防止把端点修成全瞎）
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )
            assert resp.status_code == 200, resp.text
            assert "mem-a" in _ids(resp.json()["facts"]), "撤回前 active 记忆应可见"

            # 真实撤回（削）：状态翻转 + FTS/向量清理 + 审计，全程不 mock
            with session_factory() as s:
                result = retract_memory("mem-a", reason="过期机密", actor="user-A", session=s)
            assert result.get("ok") is True, result

            resp_ctx = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )
            resp_prompt = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context/prompt", params={"task": "连接池"}),
            )

        # 行仍在库且已 retracted（证明是过滤而非删除）
        with session_factory() as s:
            row = s.get(MemoryItem, "mem-a")
            assert row is not None and row.status == STATUS_RETRACTED

        assert resp_ctx.status_code == 200, resp_ctx.text
        assert "mem-a" not in _ids(resp_ctx.json()["facts"]), (
            f"已撤回记忆仍进入 /cognitive/context：{_ids(resp_ctx.json()['facts'])}"
        )
        assert SECRET_RETRACTED not in resp_ctx.text, "已撤回记忆正文仍注入上下文"

        assert resp_prompt.status_code == 200, resp_prompt.text
        assert SECRET_RETRACTED not in resp_prompt.text, "已撤回记忆正文仍注入提示词"
        assert "mem-a" not in resp_prompt.text, "已撤回记忆 id 仍出现在提示词里"

    def test_archived_and_consolidated_excluded(self, param_env, app_isolated):
        """归档/已巩固（consolidated）同样不注入；active 对照组必须仍在。"""
        session_factory, _ = param_env
        _seed(
            session_factory,
            [
                ("mem-alive", ALIVE, "active", "user-A"),
                ("mem-arch", SECRET_ARCHIVED, STATUS_ARCHIVED, "user-A"),
                ("mem-cons", SECRET_CONSOLIDATED, STATUS_CONSOLIDATED, "user-A"),
            ],
        )

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        ids = _ids(resp.json()["facts"])
        assert "mem-alive" in ids, f"active 对照组被藏了（修废）：{ids}"
        assert "mem-arch" not in ids, f"archived 记忆漏进上下文：{ids}"
        assert "mem-cons" not in ids, f"consolidated 记忆漏进上下文：{ids}"
        assert SECRET_ARCHIVED not in resp.text
        assert SECRET_CONSOLIDATED not in resp.text

    def test_admin_branch_also_excludes_non_active(self, param_env, app_isolated):
        """admin 分支（`select(MemoryItem)` 全表）同样补 status 过滤——
        已撤回的主张对 admin 也是「停止使用」，口径同 hybrid._query_items
        （status 过滤无条件施加，在归属判定之外）。"""
        session_factory, _ = param_env
        _seed(
            session_factory,
            [
                ("mem-alive", ALIVE, "active", "user-A"),
                ("mem-gone", SECRET_RETRACTED, STATUS_RETRACTED, "user-A"),
            ],
        )

        with TestClient(app_isolated) as c:
            resp = _as(
                _principal("admin-1", role="admin"),
                lambda: c.get("/cognitive/context", params={"task": "连接池"}),
            )

        assert resp.status_code == 200, resp.text
        ids = _ids(resp.json()["facts"])
        assert "mem-alive" in ids, f"admin 看不见 active 记忆（修废）：{ids}"
        assert "mem-gone" not in ids, f"admin 分支漏进已撤回记忆：{ids}"
        assert SECRET_RETRACTED not in resp.text
