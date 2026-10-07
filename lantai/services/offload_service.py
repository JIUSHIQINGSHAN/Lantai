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
    content: str,
    score: float,
    max_chars: int,
    suffix: str,
    path: Path | str,
    time_str: str = "",
) -> tuple[str, str]:
    """超长记忆 → (注入块, evidence 摘要)：摘要行 + 全文路径行（纯函数）。

    注入块与 evidence 同源（都是截断摘要），路径行让 Agent 按需取全文，
    与腾讯 offload 的「上下文只放摘要 + 引用」一致。

    time_str 非空时行首带事件时间（与 shell_hook 注入行同形态——
    票据 .scratch/inject-frame-time/01）；空串 = 旧行为逐字不变。
    """
    summary = truncate_codepoints(content, max_chars, suffix)
    head = f"{time_str} | score {score}" if time_str else str(score)
    block = f"- [{head}] {summary}\n  全文: {path}"
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
    自己的更危险）。

    **`principal=None` 不再"不校验"（票 `.scratch/mcp-identity-gaps/10`）**：
    此前整段校验挂在 `if principal is not None:` 下，MCP `offload_read`
    在宿主不透传 `user_id` 时正是 None（`cli/mcp.py:575` 的
    `_principal_from_params` 返回 None），于是一次调用拿走别人的
    卸载全文——**而本函数最后一行是 `path.read_text()`：不限长度、
    不走 recall budget**。决定性实证
    （`.scratch/mcp-identity-gaps/probe_offload_none_semantics.py`）：

    ```
    S0 ensure_can_delete(None, resource_user_id='user-B') → 没抛错  ❌ 空操作
    S1 read_offload_file('m-B', principal=None)           → B 的全文 ❌ 洞
    S2 read_offload_file('m-legacy', None)  （NULL 老行）  → 仍可读   ✅ 单人部署
    S3/S4/S5 带身份与 admin                                   ✅ 护栏
    ```

    S0 是本票最该记住的一条：**`ensure_can_delete(None, ...)` 是彻底的空
    操作**——它的每个守卫都要求 principal 的某个字段非空
    （`getattr(principal,"is_admin",False)` → False 不放行；
    `p_user = getattr(principal,"user_id",None)` → None，于是
    `resource_user_id and p_user` 恒假）。**"调用了 ensure_can_delete"
    不等于"校验过了"**。

    修法同票 02 给 `_ensure_can_decide` 的形状：**收敛 principal 本身，
    不给 `ensure_can_delete` 加形参**（后者被 27 处写侧共用，是承重墙）。
    admin 早退必须在收敛之前（admin 的真实形态正是 `user_id=None`，
    `viewer_of` 会把它变成 `"default"`）。

    **为什么这个形状而不是票 07/09 的 `not is_admin`**：07/09 下游是
    `viewer_of` 收敛 + `OR IS NULL` 的读侧口径（NULL 老行可见）；
    本票下游是 `ensure_can_delete`，它的既定语义是"资源标了 user_id
    且与主体不同 → 403；**资源无归属 → 不视为越权**"——NULL 老行
    **天然放行**，所以不需要 `OR IS NULL`，只需要让 `p_user` 非空。
    判据仍是"下游有没有现成的收敛"，只是收敛的落点不同。
    """
    directory = offload_dir().resolve()
    filename = offload_filename(memory_id)
    path = (directory / filename).resolve()
    if directory != path.parent:
        raise ValueError("memory_id 解析路径超出卸载目录")
    if not path.is_file():
        raise FileNotFoundError(f"offload 文件不存在: {filename}")
    from lantai.core.acl import Principal, ensure_can_delete, viewer_of
    from lantai.models.tables import MemoryItem
    from lantai.storage import db

    with db.get_session() as s:
        item = s.get(MemoryItem, memory_id)
    if item is None:
        # 记忆已不存在：无从判归属。卸载文件仍在 = 孤儿文件，
        # 按「不可见」处理（宁 miss 不脏写）
        raise FileNotFoundError(f"offload 文件不存在: {filename}")
    if getattr(principal, "is_admin", False):
        return {
            "memory_id": memory_id,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }
    # `viewer_of(None)` → "default"。注意 admin 的真实形态正是
    # `user_id=None`，故 admin 判定必须在上一步先做（它靠 `is_admin`，
    # 不靠 user_id，收敛不会误伤）。
    viewer = viewer_of(principal)
    if viewer != getattr(principal, "user_id", None):
        # 只在 None 时构造收敛后的 principal；已有身份的走原对象，
        # 不改变任何现有行为（含 tenant / agent / allowed_lanes）。
        principal = Principal(
            tenant_id=getattr(principal, "tenant_id", None),
            user_id=viewer,
            agent_id=getattr(principal, "agent_id", None),
            session_id=getattr(principal, "session_id", None),
            role=getattr(principal, "role", "user"),
            allowed_lanes=getattr(principal, "allowed_lanes", None),
        )
    ensure_can_delete(
        principal,
        resource_user_id=item.user_id,
        resource_tenant_id=item.tenant_id,
        lane=item.lane,
    )
    return {"memory_id": memory_id, "path": str(path), "content": path.read_text(encoding="utf-8")}
