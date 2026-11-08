"""票 41：REST 错误语义面三组病灶。

根因（立案基线 `d2d784fe`，现 HEAD 亲核行号已漂移但形态不变）：
1. **模板病**（4 文件 9 处）：`except Exception → 422` 把 DB 写锁、Chroma
   宕机、引擎内部 bug 全部说成「你的请求有问题」（4xx 监控口径失真），
   `str(e)` 还把内部异常文本泄进响应体。对照正确模板 routes_kaogong/
   routes_pisha（只捕 PermissionError/ValueError，其余放 500 全局 handler）。
2. **可校验输入裸奔 → 500**：checkpoint cleanup（max_sessions=0）、
   terminal graph（limit 越界）、obsidian sync（schema 50 万 vs service
   20 万两层阈值矛盾）——路由无兜 + service raise ValueError → 500。
   （checkpoint write 的 session_id<3 已被票 54 的 SESSION_ID_PATTERN
   `{3,128}` 在边界拦成 422——本文件以现状锁实证其已收口。）
3. **票 01 同病残留**：POST /candidates/{id}/review 的 approve 路径在
   propose 返回 None（LLM 不可用/target 不可寻址）时 service 返回
   ok:False，路由原样 200——`.scratch/api-error-status/01` 的同病漏网。

八臂：模板故障注入臂（红：RuntimeError→应为 500 现状 422）+ 模板 AST
哨兵（红：9 处存量）+ 四个 500→422 臂（红/现状锁）+ review 200→409 臂
（红）+ 对照臂。TestClient 真路由 + 真服务链 + 真内存 SQLite，禁 mock
被测链路（故障注入为工单 TDD 明示形态：注入故障而非替身被测链路）。
"""

import ast
import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.auth import get_current_user
from lantai.models.tables import MemoryCandidate


def _principal(user_id: str, *, role: str = "user"):
    return Principal(user_id=user_id, tenant_id=None, allowed_lanes=None, role=role)


@pytest.fixture()
def api_env():
    """真内存 SQLite + 真 app（依赖覆写身份）。"""
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


@contextlib.contextmanager
def _client(principal) -> TestClient:
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── 病灶 1：模板病（mokan 代表深测 + AST 哨兵扫全部 9 处）──


def test_mokan_server_fault_is_500_not_422(api_env, monkeypatch):
    """故障注入臂（红）：service 层 RuntimeError（服务器故障）→ 应 500；
    现状模板 `except Exception → 422` 把它说成「你的请求有问题」。"""
    import lantai.services.mokan_service as mokan_mod

    def _boom(*a, **kw):
        raise RuntimeError("内部故障：database is locked (/tmp/lantai/x.sql)")

    monkeypatch.setattr(mokan_mod, "mokan_verify_service", _boom)
    admin = _principal("admin-user", role="admin")
    with _client(admin) as c:
        resp = c.post("/mokan/verify", json={"memory_ids": ["mem_x"]})
    assert resp.status_code == 500, (
        f"服务器故障应 500，现状 {resp.status_code}（模板病：except Exception → 422）；"
        f"body={resp.text[:120]}"
    )
    assert "database is locked" not in resp.text, "内部异常文本泄进响应体"


def test_mokan_permission_error_is_403(api_env, monkeypatch):
    """PermissionError 臂（现状锁，绿）：403 语义两版模板都有，不得回退。"""
    import lantai.services.mokan_service as mokan_mod

    def _deny(*a, **kw):
        raise PermissionError("not your memory")

    monkeypatch.setattr(mokan_mod, "mokan_verify_service", _deny)
    user = _principal("user-A")
    with _client(user) as c:
        resp = c.post("/mokan/verify", json={"memory_ids": ["mem_x"]})
    assert resp.status_code == 403, f"PermissionError 应 403；实得 {resp.status_code}"


def test_no_except_exception_422_template_in_generated_routes():
    """模板 AST 哨兵（红）：4 个生成系路由文件（gouchen/mokan/scratchpad/
    zhiming）不得存在 `except Exception` 兜底翻译成 422 的形态——其余异常
    必须放给 500 全局 handler（kaogong 版模板口径，票 41 修复目标 1）。"""
    banned = {"routes_gouchen.py", "routes_mokan.py", "routes_scratchpad.py", "routes_zhiming.py"}
    api_dir = Path("lantai/api")
    offenders: list[str] = []
    for name in sorted(banned):
        path = api_dir / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Raise)
                    and inner.exc is not None
                    and isinstance(inner.exc.func, ast.Name)
                    and inner.exc.func.id == "HTTPException"
                ):
                    args = inner.exc.args
                    if args and isinstance(args[0], ast.Constant) and args[0].value == 422:
                        offenders.append(f"{name}:{node.lineno}")
    assert not offenders, (
        f"模板病存量 {len(offenders)} 处（except Exception → 422，服务器故障计入"
        f"调用方滥用且 str(e) 泄内部文本）：{offenders}"
    )


# ── 病灶 2：可校验输入 → 500 收口 ──


def test_checkpoint_write_short_session_id_422(api_env):
    """现状锁（票 54 的 SESSION_ID_PATTERN `{3,128}` 已在边界拦）：
    session_id="ab" 应 422 而非 500——实证该子点已收口（工单病灶 2 第一
    条的现树状态）。"""
    admin = _principal("admin-user", role="admin")
    with _client(admin) as c:
        resp = c.post("/checkpoint", json={"session_id": "ab", "blocks": {}})
    assert resp.status_code == 422, (
        f"session_id<3 应 422；实得 {resp.status_code}（若 500 则票 54 边界已破）"
    )


def test_checkpoint_cleanup_max_sessions_zero_422(api_env):
    """红：max_sessions=0 是可校验输入（service raise ValueError）→
    应 422；现状路由无兜 → 500。"""
    admin = _principal("admin-user", role="admin")
    with _client(admin) as c:
        resp = c.post("/checkpoint/cleanup", params={"max_sessions": 0})
    assert resp.status_code == 422, (
        f"max_sessions=0 应 422（可校验输入）；现状 {resp.status_code}（500 状态码撒谎）"
    )


def test_terminal_graph_limit_out_of_range_422(api_env):
    """红：limit=150 越界（service 上限 100）→ 应 422；现状裸传 → 500。
    合法 limit 对照臂：100 → 200。"""
    admin = _principal("admin-user", role="admin")
    with _client(admin) as c:
        bad = c.get("/terminal/graph", params={"limit": 150})
        ok = c.get("/terminal/graph", params={"limit": 100})
    assert bad.status_code == 422, (
        f"limit=150 应 422（可校验输入）；现状 {bad.status_code}（500 状态码撒谎）"
    )
    assert ok.status_code == 200, f"合法 limit=100 对照臂行为不变；实得 {ok.status_code}"


def test_obsidian_sync_content_over_verbatim_cap_422(api_env):
    """红：content 25 万字符（schema 50 万放行、service 20 万拒绝）→
    应 422；现状两层阈值矛盾 + 路由无兜 → 500。"""
    admin = _principal("admin-user", role="admin")
    with _client(admin) as c:
        resp = c.post(
            "/obsidian/sync",
            json={"title": "票41", "content": "x" * 250_000},
        )
    assert resp.status_code == 422, (
        f"content 超verbatim上限应 422；现状 {resp.status_code}"
        f"（schema 50 万放行 + service 20 万拒绝 = 两层阈值矛盾）"
    )


def test_obsidian_schema_and_service_thresholds_agree():
    """单真源锁（红）：ObsidianSyncReq.content 上限必须等于 service 的
    verbatim 上限——两层阈值矛盾是独立于异常处理的病。"""
    from lantai.models.schemas import ObsidianSyncReq
    from lantai.services.obsidian_service import _VERBATIM_MAX_CHARS

    fi = ObsidianSyncReq.model_fields["content"]
    # pydantic ≥2.11 把约束放进 metadata（annotated_types.MaxLen），旧版在
    # FieldInfo.max_length——版本容忍读取
    schema_cap = getattr(fi, "max_length", None)
    if schema_cap is None:
        import annotated_types

        schema_cap = next(
            m.max_length for m in fi.metadata if isinstance(m, annotated_types.MaxLen)
        )
    assert schema_cap == _VERBATIM_MAX_CHARS, (
        f"schema 上限 {schema_cap} ≠ service 上限 {_VERBATIM_MAX_CHARS}——两层阈值矛盾"
    )


# ── 病灶 3：candidates review 200 + ok:False ──


def test_candidates_review_discarded_returns_4xx(api_env, monkeypatch):
    """红：approve 路径 propose 返回 None（真链：候选 pending_review +
    LLM 缺席 → chat_json 失败 → propose 返回 None）→ service 返回 ok:False
    → 现状路由原样 200。修复后 4xx（409 Conflict）。"""
    sf = api_env
    with sf() as s:
        s.add(
            MemoryCandidate(
                id="cand-t41",
                document_id="doc-t41",
                user_id="user-A",
                status="pending_review",
                summary="票41种子候选",
            )
        )
        s.commit()

    # chat_json 替身（外部 LLM 服务边界，纪律允许面——同 embed 替身口径）：
    # 确定性返回 update 提案、target_key 指向不存在记忆 → _resolve_target_id
    # 解析不到唯一 active 目标 → propose 整条丢弃 → review ok:False。
    # （不替身则本机 .env 的真实 LLM 端点使链路不确定。）
    import lantai.evolution.proposer as proposer_mod

    def _fake_chat_json(*a, **kw):
        return {"proposal_type": "update", "target_key": "ghost-key-404", "confidence": 0.9}

    monkeypatch.setattr(proposer_mod, "chat_json", _fake_chat_json)

    owner = _principal("user-A")
    with _client(owner) as c:
        resp = c.post(
            "/candidates/cand-t41/review",
            json={"approve": True, "reason": "票41:approve"},
        )
    assert resp.status_code != 200 or resp.json().get("ok") is not False, (
        f"approve 失败路径返回 200 + ok:False（票 01 同病残留）；"
        f"status={resp.status_code} body={resp.text[:160]}"
    )
    assert 400 <= resp.status_code < 500, (
        f"ok:False 应译 4xx；实得 {resp.status_code} body={resp.text[:160]}"
    )
