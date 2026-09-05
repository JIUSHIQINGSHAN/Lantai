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
MCP_PATH = REPO_ROOT / "scripts" / "mcp_server.py"


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
    block, summary = build_offload_inject(
        long_text, 0.93, 60, suffix, off_path)
    assert block.startswith("- [0.93] ")
    assert ("全文: " + str(off_path)) in block
    assert summary.endswith(suffix)
    assert summary in block
    # evidence 内容不超单条预算（suffix 附在预算外）
    assert len(list(summary)) <= 60 + len(list(suffix))


def test_write_read_roundtrip(monkeypatch, tmp_path):
    """集成：真实 tmp_path 落盘 + 读回（不 mock 文件系统）。"""
    from lantai.services import offload_service
    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    content = "长记忆全文 " + "内容" * 100
    path = offload_service.write_offload_file("mem_7", content)
    assert path.parent == tmp_path
    assert path.read_text(encoding="utf-8") == content
    result = offload_service.read_offload_file("mem_7")
    assert result["content"] == content
    assert result["memory_id"] == "mem_7"
    assert Path(result["path"]).parent == tmp_path


def test_read_offload_missing_file(monkeypatch, tmp_path):
    """集成：文件不存在 → FileNotFoundError（真实 tmp_path）。"""
    from lantai.services import offload_service
    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        offload_service.read_offload_file("mem_nope")


def test_shell_hook_offload_injection(monkeypatch, tmp_path):
    """集成冒烟：超长记忆 → 落盘 + 上下文只注入摘要与路径，evidence 收窄。"""
    mod = _load_module(HOOK_PATH, "shell_hook_offload")
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    long_content = "用户长期偏好：用 Rust 构建 CLI 工具。" + "详细事实" * 100
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem
        s.add(MemoryItem(id="mem_offload_1", memory_type="semantic", key="k",
                         content=long_content))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
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
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        from lantai.models.tables import MemoryItem
        s.add(MemoryItem(id="mem_short", memory_type="semantic", key="k",
                         content="短记忆：用户喜欢 Python"))
        s.commit()

    class _FakeStore:
        def search(self, qv, top_k=5):
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
    """MCP 集成：offload_read 返回卸载全文；缺参 -32602。"""
    mod = _load_module(MCP_PATH, "mcp_server_offload")
    from lantai.services import offload_service
    monkeypatch.setattr(offload_service.settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path))
    offload_service.write_offload_file("mem_9", "卸载全文内容")

    resp = mod.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "offload_read",
                                  "arguments": {"memory_id": "mem_9"}}})
    import json as _json
    payload = _json.loads(resp["result"]["content"][0]["text"])
    assert payload["content"] == "卸载全文内容"
    assert payload["memory_id"] == "mem_9"

    resp2 = mod.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "offload_read", "arguments": {}}})
    assert resp2["error"]["code"] == -32602