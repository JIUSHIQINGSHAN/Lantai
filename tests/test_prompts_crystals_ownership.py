"""`/prompts*` 与 `/crystals*` 归属：票 .scratch/readside-gaps/11

**先说影响**：两件事，都是知识资产全文可读，一个身份都不取。

1. `GET /prompts`、`GET /prompts/{id}` 把提示词模板全文吐出——
   提示词是使用者反复打磨的方法论，比记忆正文更接近"怎么用这个系统"。
2. `GET /crystals` 吐出技能结晶的 `procedure` / `trigger_rule`——
   从记忆里蒸馏出的操作流程。

第五轮补扫实证（`probe_round5b.py`）：`/crystals` len=338、
`/prompts` len=125、`/prompts/prompt-B` len=58，**全部含** B 的密文。
这三条是第四轮完全没打过的路由（静态审计在"无身份"名单里，
动态从未验证）——补扫才暴露。

**两张表都没有归属列**：`PromptTemplate`（`tables.py:731`）、
`SkillCrystal`（`tables.py:302`）。需加列 + 迁移，真实库行数很少
（prompttemplate 1 行、skillcrystal 5 行），迁移风险低。

**NULL 口径用 `OR IS NULL`**（同票 03/04/06/09）：老行保持 NULL，
靠这个兜住——单人部署下判"不可见"会让功能直接消失。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import PromptTemplate, SkillCrystal

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def pc_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
    ):
        yield session_factory


def _seed(session_factory) -> None:
    """B 各一条 + 一条 NULL 属主的老行。"""
    with session_factory() as s:
        s.add(
            PromptTemplate(
                id="prompt-B",
                template=f"B 的提示词：{SECRET}",
                description="B 的模板说明",
                user_id="user-B",
            )
        )
        s.add(
            PromptTemplate(
                id="prompt-null",
                template="未归属的老提示词",
                description="老模板",
                user_id=None,
            )
        )
        s.add(
            SkillCrystal(
                id="crystal-B",
                skill_name="B 的技能",
                trigger_rule=f"触发规则 {SECRET}",
                procedure=f"操作步骤 {SECRET}",
                status="candidate",
                user_id="user-B",
            )
        )
        s.add(
            SkillCrystal(
                id="crystal-null",
                skill_name="老技能",
                trigger_rule="老触发规则",
                procedure="老操作步骤",
                status="candidate",
                user_id=None,
            )
        )
        s.commit()


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _row(session_factory, model, row_id: str):
    with session_factory() as s:
        return s.get(model, row_id)


# ── 读侧：prompts ───────────────────────────────────────────────


class TestPromptReadOwnership:
    def test_list_excludes_other_users(self, pc_env):
        """A 列提示词看不到 B 的模板（断言落在 template 正文上）。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/prompts"))

        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = [r["id"] for r in rows]
        assert "prompt-B" not in ids, f"A 看到了 B 的提示词 id：{ids}"
        blob = repr(rows)
        assert SECRET not in blob, "A 读到了 B 的提示词正文"
        assert "B 的模板说明" not in blob, "A 读到了 B 的模板说明"

    def test_get_one_excludes_other_users(self, pc_env):
        """A 直接按 id 取 B 的提示词 → 拿不到 B 的模板正文。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/prompts/prompt-B"))

        assert resp.status_code in (200, 403, 404), resp.text
        assert SECRET not in resp.text, "A 按 id 读到了 B 的提示词正文"

    def test_null_owner_row_still_visible(self, pc_env):
        """NULL 属主的老模板对非 admin 仍可见（不能修废）。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/prompts"))

        ids = [r["id"] for r in resp.json()]
        assert "prompt-null" in ids, f"NULL 属主老模板不可见了：{ids}"

    def test_admin_sees_all(self, pc_env):
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/prompts"))

        ids = sorted(r["id"] for r in resp.json())
        assert ids == ["prompt-B", "prompt-null"], f"admin 看不到全部：{ids}"


# ── 读侧：crystals ──────────────────────────────────────────────


class TestCrystalReadOwnership:
    def test_list_excludes_other_users(self, pc_env):
        """A 列结晶看不到 B 的 procedure / trigger_rule。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/crystals"))

        assert resp.status_code == 200, resp.text
        rows = resp.json()["crystals"]
        ids = [r["id"] for r in rows]
        assert "crystal-B" not in ids, f"A 看到了 B 的结晶 id：{ids}"
        blob = repr(rows)
        assert SECRET not in blob, "A 读到了 B 的操作步骤/触发规则"
        assert "B 的技能" not in blob, "A 读到了 B 的技能名"

    def test_null_owner_row_still_visible(self, pc_env):
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/crystals"))

        ids = [r["id"] for r in resp.json()["crystals"]]
        assert "crystal-null" in ids, f"NULL 属主老结晶不可见了：{ids}"

    def test_admin_sees_all(self, pc_env):
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/crystals"))

        ids = sorted(r["id"] for r in resp.json()["crystals"])
        assert ids == ["crystal-B", "crystal-null"], f"admin 看不到全部：{ids}"


# ── 写侧 ────────────────────────────────────────────────────────


class TestWriteOwnership:
    def test_write_prompt_records_owner(self, pc_env):
        """A 写提示词 → 落库行 user_id 是 A（落库事实，不只是状态码）。"""
        sf = pc_env

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.put(
                    "/prompts/prompt-A",
                    json={"template": "A 的提示词", "description": "A 的说明"},
                ),
            )

        assert resp.status_code == 200, resp.text
        row = _row(sf, PromptTemplate, "prompt-A")
        assert row is not None, "写入没落库"
        assert row.user_id == "user-A", f"提示词没落归属：user_id={row.user_id}"

    def test_other_user_cannot_decide_crystal(self, pc_env):
        """A 不能替 B 裁决技能结晶 → 403，且 status 未变（403 不落库）。"""
        sf = pc_env
        _seed(sf)
        before = _row(sf, SkillCrystal, "crystal-B").status

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/crystals/crystal-B/decide",
                    json={"approve": False, "reason": "A 擅自拒绝"},
                ),
            )

        assert resp.status_code == 403, f"A 竟然能裁决 B 的结晶：{resp.status_code} {resp.text}"
        assert _row(sf, SkillCrystal, "crystal-B").status == before, "403 却改了 status"

    def test_owner_can_still_decide_own(self, pc_env):
        """owner 对自己的结晶照旧能裁决（不能修废）。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post(
                    "/crystals/crystal-B/decide",
                    json={"approve": False, "reason": "B 自己拒绝"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _row(sf, SkillCrystal, "crystal-B").status == "archived"

    def test_admin_can_decide_any(self, pc_env):
        """admin 照旧能裁决任意结晶（不能把运维修废）。"""
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.post(
                    "/crystals/crystal-B/decide",
                    json={"approve": False, "reason": "admin 拒绝"},
                ),
            )

        assert resp.status_code == 200, resp.text
        assert _row(sf, SkillCrystal, "crystal-B").status == "archived"

    def test_other_user_cannot_overwrite_prompt(self, pc_env):
        """A 不能覆写 B 的模板——403，且库里 B 的正文没变（403 不落库）。

        M6（update_prompt 去掉写侧校验）正是被这条抓住的：没有它，
        A 用自己的 id  PUT 一份就能把 B 的 template 覆盖掉。
        """
        sf = pc_env
        _seed(sf)

        from lantai.api.app import app

        with TestClient(app) as c:
            # 不取 resp：判据是库里 B 的正文没被改，不是状态码
            # （403 与"新建了 A 自己的行"都可能让 B 保持不变）
            _as(
                _principal("user-A"),
                lambda: c.put(
                    "/prompts/prompt-B",
                    json={"template": "A 篡改的模板", "description": "A 篡改的说明"},
                ),
            )

        b = _row(sf, PromptTemplate, "prompt-B")
        assert SECRET in (b.template or ""), f"A 改掉了 B 的模板正文：{b.template}"
        assert b.description == "B 的模板说明", f"A 改掉了 B 的说明：{b.description}"

    def test_detect_records_owner(self, pc_env):
        """结晶检测新建的候选落 principal 的 user_id，不造无主行。

        M9（检测新建不落 owner）正是被这条抓住的。
        走 service 直调（detect 需要 CRYSTAL_ENABLED 与聚类条件，
        路由层不好构造最小输入），但**不 mock 任何东西**——真写真读。
        """
        from unittest.mock import patch as _patch

        from lantai.core.settings import settings
        from lantai.models.tables import MemoryItem
        from lantai.services.crystal_service import run_crystal_detect_once

        sf = pc_env
        from lantai.core.time import utcnow

        with sf() as s:
            # 同一个 user 的多条相似记忆，够 CRYSTAL_MIN_CLUSTER 聚类
            for i in range(3):
                s.add(
                    MemoryItem(
                        id=f"mem-c{i}",
                        memory_type="text",
                        key=f"结晶密钥{i}",
                        content=f"关于结晶密钥{i} 的操作流程第 {i} 步",
                        lane="fact",
                        domain="user",
                        decay_score=1.0,
                        status="active",
                        user_id="user-A",
                        tenant_id=None,
                        namespace="default",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            s.commit()

        with _patch.object(settings, "CRYSTAL_ENABLED", True):
            run_crystal_detect_once(dry_run=False, principal=_principal("user-A"))

        with sf() as s:
            from sqlmodel import select

            rows = s.exec(select(SkillCrystal)).all()
        assert rows, "一条结晶都没造出来（这条测试没打到东西）"
        for r in rows:
            assert r.user_id == "user-A", f"结晶检测没落归属：user_id={r.user_id}"

    def test_internal_call_unfiltered(self, pc_env):
        """principal=None（内部/CLI）不加过滤，与改动前逐字一致。"""
        sf = pc_env
        _seed(sf)

        from lantai.services.prompt_service import list_prompts

        ids = sorted(r["id"] for r in list_prompts())
        assert ids == ["prompt-B", "prompt-null"], f"内部调用被收窄了：{ids}"
