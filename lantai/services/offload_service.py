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
    return Path(settings.OFFLOAD_OUTPUT_DIR) if settings.OFFLOAD_OUTPUT_DIR \
        else _DEFAULT_OFFLOAD_DIR


def offload_filename(memory_id: str) -> str:
    """记忆 id → 文件名（白名单字符；路径穿越输入抛 ValueError）。"""
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id must be a non-empty string")
    if "/" in memory_id or "\\" in memory_id or ".." in memory_id:
        raise ValueError("memory_id contains unsafe characters")
    safe = "".join(c for c in memory_id if c.isalnum() or c in "-_.") or "mem"
    return f"{safe}.md"


def build_offload_inject(content: str, score: float, max_chars: int,
                         suffix: str, path: Path | str) -> tuple[str, str]:
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


def read_offload_file(memory_id: str) -> dict:
    """读取卸载全文（MCP offload_read 用）。

    路径安全：文件名白名单 + 解析后必须仍在卸载目录内（防穿越）。
    """
    directory = offload_dir().resolve()
    filename = offload_filename(memory_id)
    path = (directory / filename).resolve()
    if directory != path.parent:
        raise ValueError("memory_id 解析路径超出卸载目录")
    if not path.is_file():
        raise FileNotFoundError(f"offload 文件不存在: {filename}")
    return {"memory_id": memory_id, "path": str(path),
            "content": path.read_text(encoding="utf-8")}