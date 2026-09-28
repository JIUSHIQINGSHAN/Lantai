"""上下文卸载测试（借鉴 TencentDB Agent Memory offload_server/compact 窄版）。

- offload_filename / build_offload_inject：纯函数冒烟（不 mock）
- write/read 往返：真实 tmp_path 文件副作用（不 mock 文件系统）
- shell_hook 集成：超长记忆 → 落盘 + 上下文只注入摘要 + 路径（真实 SQLite）
- MCP 集成：offload_read 返回卸载全文；缺参 -32602
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HOOK_PATH = REPO_ROOT / "scripts" / "shell_hook.py"
MCP_PATH = REPO_ROOT / "lantai" / "cli" / "mcp.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_offload_filename_sanitizes():
    """纯函数冒烟：白名单字符保留；空值/路径穿越输入拒绝。"""
    from lantai.services.offload_service import offload_filename

    assert offload_filename("mem_1") == "mem_1.md"
    assert offload_filename("a b") == "ab.md"  # 空白被剥离
    with pytest.raises(ValueError):
        offload_filename("a b/c")  # 含斜杠 → 拒绝
    with pytest.raises(ValueError):
        offload_filename("")
    with pytest.raises(ValueError):
        offload_filename("..")
    with pytest.raises(ValueError):
        offload_filename("a/../etc")


def test_build_offload_inject_shape():
    """纯函数冒烟：注入块 = 摘要行 + 全文路径行；evidence 为截断摘要（同源）。"""
    from lantai.services.offload_service import build_offload_inject

    long_text = "用户喜欢 Python" + "很长的内容" * 20
    suffix = "…（已卸载全文）"
    off_path = Path("C:/offload/mem_1.md")
    block, summary = build_offload_inject(long_text, 0.93, 60, suffix, off_path)
    assert block.startswith("- [0.93] ")
    assert ("全文: " + str(off_path)) in block
    assert summary.endswith(suffix)
    assert summary in block
    # evidence 内容不超单条预算（suffix 附在预算外）
    assert len(list(summary)) <= 60 + len(list(suffix))


def test_write_read_roundtrip(monkeypatch, tmp_path):
    """集成：真实 tmp_path 落盘 + 读回（不 mock 文件系统）。

    **身份说明（票 `.scratch/mcp-identity-gaps/10`）**：本用例原是
    `read_offload_file("mem_7")`——靠 `principal=None` 绕过归属校验。
    10 号票把 None 从"不校验"改成经 `viewer_of` 收敛到 `"default"`
    后照常校验，于是这里必须显式给身份。**这不是"改测试让代码过"**：
    本用例的目的是验"write 之后 read 得回来"（写侧 roundtrip），
    不是验 None 语义；给它一个显式身份，断言与覆盖一字不减。
    （None 语义由 `tests/test_offload_none_principal.py` 单独覆盖。）
    """
    from lantai.core.acl import Principal
    from lantai.services import offload_service

    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    content = "长记忆全文 " + "内容" * 100
    path = offload_service.write_offload_file("mem_7", content)
    assert path.parent == tmp_path
    assert path.read_text(encoding="utf-8") == content
    # 反查归属用的 MemoryItem 行：conftest 已把 db.engine 指到隔离库，
    # 这里显式种一行 mem_7，属主就是下面读时用的 user_id。
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, create_engine

    import lantai.storage.db as db_module
    from lantai.core.time import utcnow
    from lantai.models.tables import MemoryItem

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(eng)
    orig = db_module.engine
    db_module.engine = eng
    try:
        now = utcnow()
        with Session(eng) as s:
            s.add(
                MemoryItem(
                    id="mem_7",
                    memory_type="semantic",
                    key="k-mem_7",
                    content=content[:20],
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
                    user_id="user-A",
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()
        who = Principal(
            tenant_id=None,
            user_id="user-A",
            agent_id=None,
            session_id=None,
            role="user",
            allowed_lanes=None,
        )
        result = offload_service.read_offload_file("mem_7", principal=who)
    finally:
        db_module.engine = orig
    assert result["content"] == content
    assert result["memory_id"] == "mem_7"
    assert Path(result["path"]).parent == tmp_path


def test_read_offload_missing_file(monkeypatch, tmp_path):
    """集成：文件不存在 → FileNotFoundError（真实 tmp_path）。

    文件都不存在时在归属校验**之前**就返回（路径检查先行），
    所以身份无所谓——但显式传一个，让"无身份"不再是无意中的默认路径。
    """
    from lantai.core.acl import Principal
    from lantai.services import offload_service

    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    who = Principal(tenant_id=None, user_id="user-A", agent_id=None, session_id=None, role="user")
    with pytest.raises(FileNotFoundError):
        offload_service.read_offload_file("mem_nope", principal=who)


def test_shell_hook_offload_injection(monkeypatch, tmp_path):
    """集成冒烟：超长记忆 → 落盘 + 上下文只注入摘要与路径，evidence 收窄。"""
    mod = _load_module(HOOK_PATH, "shell_hook_offload")
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    long_content = "用户长期偏好：用 Rust 构建 CLI 工具。" + "详细事实" * 100
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem

        s.add(MemoryItem(id="mem_offload_1", memory_type="semantic", key="k", content=long_content))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": "mem_offload_1", "distance": 0.05}]

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr(mod, "embed", lambda texts: [[0.1] * 8])
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_OFFLOAD_CHARS", 50)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_CHARS_PER_MEMORY", 60)
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_MAX_TOTAL_CHARS", 1500)
    monkeypatch.setattr(mod.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))

    out = mod.build_context("这是一个超过三字符的查询")
    assert "全文:" in out["context"]
    off_path = tmp_path / "mem_offload_1.md"
    assert off_path.exists()
    assert off_path.read_text(encoding="utf-8") == long_content
    ev = out["evidence"][0]
    assert len(list(ev["content"])) <= 60 + len(list(mod._OFFLOAD_SUFFIX))
    assert ev["id"] == "mem_offload_1"


def test_shell_hook_short_memory_no_offload(monkeypatch, tmp_path):
    """集成冒烟：短记忆不落盘（保持普通截断注入路径）。"""
    mod = _load_module(HOOK_PATH, "shell_hook_short")
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem

        s.add(
            MemoryItem(
                id="mem_short", memory_type="semantic", key="k", content="短记忆：用户喜欢 Python"
            )
        )
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5, filters=None):
            return [{"id": "mem_short", "distance": 0.1}]

    import lantai.storage.db as db_module

    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "get_vector_store", lambda: _FakeStore())
    monkeypatch.setattr(mod, "embed", lambda texts: [[0.1] * 8])
    monkeypatch.setattr(mod.settings, "SHELL_HOOK_OFFLOAD_CHARS", 50)
    monkeypatch.setattr(mod.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))

    out = mod.build_context("这是一个超过三字符的查询")
    assert "全文:" not in out["context"]
    assert not (tmp_path / "mem_short.md").exists()
    assert "短记忆：用户喜欢 Python" in out["context"]


def test_mcp_offload_read_tool(monkeypatch, tmp_path):
    """MCP 集成：offload_read 返回卸载全文；缺参 -32602。

    **身份说明（票 `.scratch/mcp-identity-gaps/10`）**：原用例的
    `arguments` 里没有 `user_id`，即 `principal=None`——那正是 10 号票
    要堵的洞的触发路径（宿主不透传身份）。修法之后 None 会收敛到
    `"default"` 并照常校验，所以这里补一个 `user_id`，并在库里种一行
    属主相同的 `mem_9`。**覆盖一字不减**：仍是同一个 handler 真入口、
    同样断言返回全文与 id、同样验缺参的 -32602。
    （None 口径由 `tests/test_offload_none_principal.py` 覆盖。）
    """
    mod = _load_module(MCP_PATH, "mcp_server_offload")
    from lantai.services import offload_service

    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    offload_service.write_offload_file("mem_9", "卸载全文内容")

    # 种一行 mem_9，属主与调用时传的 user_id 相同（conftest 已隔离 db.engine）
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import lantai.storage.db as db_module
    from lantai.core.time import utcnow
    from lantai.models.tables import MemoryItem

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    orig = db_module.engine
    db_module.engine = eng
    try:
        now = utcnow()
        with Session(eng) as s:
            s.add(
                MemoryItem(
                    id="mem_9",
                    memory_type="semantic",
                    key="k-mem_9",
                    content="卸载全文内容",
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
                    user_id="user-A",
                    scene_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.commit()

        resp = mod.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "offload_read",
                    "arguments": {"memory_id": "mem_9", "user_id": "user-A"},
                },
            }
        )
    finally:
        db_module.engine = orig
    import json as _json

    payload = _json.loads(resp["result"]["content"][0]["text"])
    assert payload["content"] == "卸载全文内容"
    assert payload["memory_id"] == "mem_9"

    resp2 = mod.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "offload_read", "arguments": {}},
        }
    )
    assert resp2["error"]["code"] == -32602
