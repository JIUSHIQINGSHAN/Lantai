"""`/persona/*` 读写两侧归属：票 .scratch/readside-gaps/06

**两件事。**

读侧：6 个 handler 一个身份都不取。`PersonaProfile` 的 E 层
`epistemic_facts` 模型 docstring 自称"认知底色与核心事实"，真实库那一行
写的是硬件环境、人物关系这类个人化信息。`GET /persona/list` 一次全吐。
第四轮动态探测实证 4 条读端点泄漏（`OBSERVED4_SUMMARY.md`）。

写侧（更该修）：`POST /persona` 与 `POST /persona/{id}/activate`
没有归属校验——A 能改名/改 B 的 persona，能停用 B 的、激活自己的。
同票 02 的性质：**别人替你做决定**。

**归属列一直在**（纠正第二轮审计的误记）：`PRAGMA table_info(persona_profile)`
有完整四元组，`tables.py:666` 的模型类同样有。列从不被用于校验而已。

**NULL 口径用 `OR IS NULL`**（票 06 修法口径 2，同票 03/04）：
真实库唯一一行 persona 是 NULL 属主（单人部署事实）。判"不可见"会让
`/persona/active` 对唯一真实用户返回空，人格基座直接失效。
NULL 是「未记录」不是「属于所有人」。

**`is_active` 是全局单例**（既有设计，本票不改）：`set_persona` /
`activate_persona` 激活一条时会取消**其他所有** active。所以写侧校验的
判据不是"这条是不是我的"，而是"**我有没有资格动这条**"——
A 激活自己的 persona 时顺带停掉 B 的 active，那是单例语义的必然，
不算越权；但 A 直接 activate B 的 persona、或改写 B 的三层字段，是越权。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import PersonaProfile

SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def persona_env():
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
    """A/B 各一条 persona + 一条 NULL 属主的老行（真实库就是 NULL）。"""
    with session_factory() as s:
        s.add(
            PersonaProfile(
                id="persona-A",
                user_id="user-A",
                name="A 的画像",
                is_active=True,
                linguistic_style="A 的风格",
                guidelines="A 的准则",
                epistemic_facts="A 的认知底色",
            )
        )
        s.add(
            PersonaProfile(
                id="persona-B",
                user_id="user-B",
                name="B 的画像",
                is_active=False,
                linguistic_style="B 的风格",
                guidelines="B 的准则",
                epistemic_facts=SECRET,
            )
        )
        s.add(
            PersonaProfile(
                id="persona-null",
                user_id=None,
                name="老画像",
                is_active=False,
                epistemic_facts="未归属的老认知底色",
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


def _row(session_factory, persona_id: str) -> PersonaProfile | None:
    with session_factory() as s:
        return s.get(PersonaProfile, persona_id)


# ── 读侧 ────────────────────────────────────────────────────────


class TestPersonaReadOwnership:
    def test_list_excludes_other_users_persona(self, persona_env):
        """A 列 persona 看不到 B 的三层字段与 id（断言落在具体字段上）。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/persona/list"))

        assert resp.status_code == 200, resp.text
        rows = resp.json()
        ids = [r["id"] for r in rows]
        assert "persona-B" not in ids, f"A 看到了 B 的 persona id：{ids}"
        blob = repr(rows)
        assert SECRET not in blob, "A 读到了 B 的 epistemic_facts 全文"
        assert "B 的画像" not in blob, "A 读到了 B 的 persona 名称"
        assert "B 的准则" not in blob, "A 读到了 B 的 guidelines"

    def test_owner_still_sees_own(self, persona_env):
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/persona/list"))

        rows = resp.json()
        ids = [r["id"] for r in rows]
        assert "persona-A" in ids, f"A 看不到自己的 persona：{ids}"
        mine = [r for r in rows if r["id"] == "persona-A"][0]
        assert mine["epistemic_facts"] == "A 的认知底色"

    def test_null_owner_row_still_visible(self, persona_env):
        """NULL 属主老行对非 admin 仍可见——单人部署不能让人格基座失效。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/persona/list"))

        ids = [r["id"] for r in resp.json()]
        assert "persona-null" in ids, f"NULL 属主老行不可见了（人格基座会失效）：{ids}"

    def test_active_excludes_other_users(self, persona_env):
        """`/persona/active` 只返回自己的激活画像（不吐 B 的）。

        判据必须落在真正会变的量上：**只把 B 的那条置为 active**，
        A 自己一条都不 active。这样收窄没生效时 `/persona/active`
        必然返回 B 的画像；收窄生效则 A 看不到任何 active。
        （早先的写法是"A、B 都 active"，那时函数返回 A 那条，
        断言恒真——那是假绿，不是通过。）
        """
        session_factory = persona_env
        _seed(session_factory)
        with session_factory() as s:
            a = s.get(PersonaProfile, "persona-A")
            a.is_active = False
            s.add(a)
            b = s.get(PersonaProfile, "persona-B")
            b.is_active = True
            s.add(b)
            s.commit()

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/persona/active"))

        assert resp.status_code == 200, resp.text
        blob = resp.text
        assert SECRET not in blob, "A 从 /persona/active 读到了 B 的认知底色"
        assert "B 的画像" not in blob, "A 从 /persona/active 读到了 B 的画像名"

    def test_context_excludes_other_users(self, persona_env):
        """`/persona/context` 渲染出的 prompt 块不含 B 的画像。

        同 `test_active_excludes_other_users`：只让 B active，
        否则断言恒真（假绿）。
        """
        session_factory = persona_env
        _seed(session_factory)
        with session_factory() as s:
            a = s.get(PersonaProfile, "persona-A")
            a.is_active = False
            s.add(a)
            b = s.get(PersonaProfile, "persona-B")
            b.is_active = True
            s.add(b)
            s.commit()

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/persona/context"))

        assert resp.status_code == 200, resp.text
        blob = resp.text
        assert SECRET not in blob, "A 读到了渲染后的 B 认知底色 prompt"
        assert "B 的准则" not in blob, "A 读到了渲染后的 B 行为准则 prompt"

    def test_admin_sees_all(self, persona_env):
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(_principal(None, role="admin"), lambda: c.get("/persona/list"))

        ids = sorted(r["id"] for r in resp.json())
        assert ids == ["persona-A", "persona-B", "persona-null"], f"admin 看不到全部：{ids}"


# ── 写侧 ────────────────────────────────────────────────────────


class TestPersonaWriteOwnership:
    def test_other_user_cannot_activate(self, persona_env):
        """A activate B 的 persona → 403，且库里 is_active 未变（403 不落库）。"""
        session_factory = persona_env
        _seed(session_factory)
        before = _row(session_factory, "persona-B").is_active

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/persona/persona-B/activate"),
            )

        assert resp.status_code == 403, f"A 竟然能激活 B 的 persona：{resp.status_code} {resp.text}"
        assert _row(session_factory, "persona-B").is_active == before, "403 却改了 is_active"

    def test_other_user_cannot_overwrite_fields(self, persona_env):
        """A 用自己的名字写 B 的三层字段 → 403 或不落到 B 行上。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            # 不取 resp：本测试的判据是**库里 B 的行没被改**，
            # 不是状态码（403 与 200 都可能在"没落到 B 行上"时出现）
            _as(
                _principal("user-A"),
                lambda: c.post(
                    "/persona",
                    json={
                        "name": "B 的画像",
                        "linguistic_style": "A 篡改的风格",
                        "guidelines": "A 篡改的准则",
                        "epistemic_facts": "A 篡改的认知底色",
                        "is_active": False,
                    },
                ),
            )

        b = _row(session_factory, "persona-B")
        assert b.linguistic_style == "B 的风格", f"A 改掉了 B 的言语风格：{b.linguistic_style}"
        assert b.guidelines == "B 的准则", f"A 改掉了 B 的行为准则：{b.guidelines}"
        assert b.epistemic_facts == SECRET, f"A 改掉了 B 的认知底色：{b.epistemic_facts}"

    def test_owner_can_still_write_own(self, persona_env):
        """owner 对自己的 persona 照旧能改（不能修废）。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/persona",
                    json={
                        "name": "A 的画像",
                        "linguistic_style": "A 改后的风格",
                        "guidelines": "A 的准则",
                        "epistemic_facts": "A 的认知底色",
                        "is_active": False,
                    },
                ),
            )

        assert resp.status_code == 200, resp.text
        a = _row(session_factory, "persona-A")
        assert a.linguistic_style == "A 改后的风格", (
            f"owner 改不动自己的 persona：{a.linguistic_style}"
        )

    def test_new_persona_records_owner(self, persona_env):
        """新建 persona 落 principal 的 user_id，不造谁都不该看见的行。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/persona",
                    json={
                        "name": "A 的新画像",
                        "linguistic_style": "新风格",
                        "guidelines": "新准则",
                        "epistemic_facts": "新认知底色",
                        "is_active": False,
                    },
                ),
            )

        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]
        row = _row(session_factory, new_id)
        assert row.user_id == "user-A", f"新建 persona 没落归属：user_id={row.user_id}"

    def test_admin_can_activate_any(self, persona_env):
        """admin 照旧能激活任意 persona（不能把运维修废）。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.api.app import app

        with TestClient(app) as c:
            resp = _as(
                _principal(None, role="admin"),
                lambda: c.post("/persona/persona-B/activate"),
            )

        assert resp.status_code == 200, resp.text
        assert _row(session_factory, "persona-B").is_active is True

    def test_internal_call_unfiltered(self, persona_env):
        """principal=None（内部/CLI）不加过滤，与改动前逐字一致。"""
        session_factory = persona_env
        _seed(session_factory)

        from lantai.services.persona_service import list_personas

        ids = sorted(p.id for p in list_personas())
        assert ids == ["persona-A", "persona-B", "persona-null"], f"内部调用被收窄了：{ids}"
