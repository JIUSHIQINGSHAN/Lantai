"""上下文卸载服务（借鉴 TencentDB Agent Memory offload_server/compact 窄版落点）。

长记忆全文落文件 docs/memory-offload/{memory_id}.md，上下文只注入摘要 + 路径，
需要时经 MCP offload_read 取完整原文。纯函数与文件副作用分离（冒烟可测不 mock）。
"""

from pathlib import Path

from lantai.core.settings import settings
from lantai.core.text import truncate_codepoints

# 仓库根 = lantai/services/ → lantai/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OFFLOAD_DIR = _REPO_ROOT / "docs" / "memory-offload"


def offload_dir() -> Path:
    """卸载全文目录：settings.OFFLOAD_OUTPUT_DIR 为空时默认仓库 docs/memory-offload。"""
    return (
        Path(settings.OFFLOAD_OUTPUT_DIR) if settings.OFFLOAD_OUTPUT_DIR else _DEFAULT_OFFLOAD_DIR
    )


def offload_filename(memory_id: str) -> str:
    """记忆 id → 文件名（白名单字符；路径穿越输入抛 ValueError）。"""
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id must be a non-empty string")
    if "/" in memory_id or "\\" in memory_id or ".." in memory_id:
        raise ValueError("memory_id contains unsafe characters")
    safe = "".join(c for c in memory_id if c.isalnum() or c in "-_.") or "mem"
    return f"{safe}.md"


def build_offload_inject(
    content: str, score: float, max_chars: int, suffix: str, path: Path | str
) -> tuple[str, str]:
    """超长记忆 → (注入块, evidence 摘要)：摘要行 + 全文路径行（纯函数）。

    注入块与 evidence 同源（都是截断摘要），路径行让 Agent 按需取全文，
    与腾讯 offload 的「上下文只放摘要 + 引用」一致。
    """
    summary = truncate_codepoints(content, max_chars, suffix)
    block = f"- [{score}] {summary}\n  全文: {path}"
    return block, summary


def write_offload_file(memory_id: str, content: str) -> Path:
    """全文落盘（真实文件副作用）。返回写入路径。"""
    directory = offload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / offload_filename(memory_id)
    path.write_text(content, encoding="utf-8")
    return path


def read_offload_file(memory_id: str, principal=None) -> dict:
    """读取卸载全文（MCP offload_read 用）。

    路径安全：文件名白名单 + 解析后必须仍在卸载目录内（防穿越）。

    归属校验（票 `.scratch/mcp-identity-gaps/01b`）：文件系统没有归属列，
    判据按 `memory_id` 反查 `MemoryItem.user_id`（同 `resolve_probe_response`
    对 `ConflictEvent` 的过渡推导——资源本身无列时挂到它的载体上判）。
    admin/system 全权；非 admin 只能读自己记忆的全文；记忆不存在或
    无归属（老行）→ 不放行（宁 miss：卸载目录里躺着别人的全文比读不到
    自己的更危险）。`principal=None`（内部 worker / 脚本）不校验。
    """
    directory = offload_dir().resolve()
    filename = offload_filename(memory_id)
    path = (directory / filename).resolve()
    if directory != path.parent:
        raise ValueError("memory_id 解析路径超出卸载目录")
    if not path.is_file():
        raise FileNotFoundError(f"offload 文件不存在: {filename}")
    if principal is not None:
        from lantai.core.acl import ensure_can_delete
        from lantai.models.tables import MemoryItem
        from lantai.storage import db

        with db.get_session() as s:
            item = s.get(MemoryItem, memory_id)
        if item is None:
            # 记忆已不存在：无从判归属。卸载文件仍在 = 孤儿文件，
            # 按「不可见」处理（宁 miss 不脏写）
            raise FileNotFoundError(f"offload 文件不存在: {filename}")
        ensure_can_delete(
            principal,
            resource_user_id=item.user_id,
            resource_tenant_id=item.tenant_id,
            lane=item.lane,
        )
    return {"memory_id": memory_id, "path": str(path), "content": path.read_text(encoding="utf-8")}
