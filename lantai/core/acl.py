"""lane 级访问控制（借鉴 TencentDB Memory Hub Fixed Binding + ACL 窄版）。

AGENT_LANE_BINDINGS = {"agent-a": ["fact", "rule"]}：绑定过的 agent 只能
检索/写入绑定 lane 的记忆；未启用（空字典）时全部放行（现状行为零变化）。

MCP 工具直连 service 不经 REST，agent 身份传递记为后续项（票据 08 注明）。
"""
from fastapi import Header, HTTPException

from lantai.core.settings import settings


def active_bindings() -> dict:
    """当前生效的绑定表（零硬编码：空表 = 不启用 ACL）。"""
    return dict(settings.AGENT_LANE_BINDINGS or {})


def allowed_lanes(agent_id: str) -> list[str] | None:
    """检索侧收窄：绑定表查 agent 允许的 lane 集。

    None = 未启用（不受限）；[] = 已绑定但无可读 lane（放行空集）。
    """
    bindings = active_bindings()
    if not bindings:
        return None
    return bindings.get(agent_id) or []


def lane_allowed(agent_id: str, lane: str) -> bool:
    """写入侧校验：ACL 未启用 → True；启用后 lane 必须在绑定集内。"""
    lanes = allowed_lanes(agent_id)
    if lanes is None:
        return True
    return lane in lanes


def filter_results_by_lanes(results: list, lanes: list[str] | None) -> list:
    """检索结果按允许 lane 集过滤（纯函数）。

    lanes=None（未启用）→ 原样返回；结果 item 兼容两种形态：
    {"memory": {..., "lane": ...}} 与 {"document": ...}（FTS 兜底无 lane，
    视为默认 lane（RAW_MEMORY_DEFAULT_LANE），不在绑定集则宁 miss 不放行）。
    """
    if lanes is None:
        return results
    allowed = set(lanes)

    def _lane(r) -> str:
        if isinstance(r, dict) and isinstance(r.get("memory"), dict):
            return r["memory"].get("lane") or settings.RAW_MEMORY_DEFAULT_LANE
        return settings.RAW_MEMORY_DEFAULT_LANE

    return [r for r in results if _lane(r) in allowed]


def verify_agent(x_agent_id: str | None = Header(None, alias="X-Agent-Id")) -> str:
    """FastAPI 依赖：ACL 启用时强制 X-Agent-Id 且已绑定，否则 403。"""
    if not active_bindings():
        return "no-acl"
    if not x_agent_id or x_agent_id not in active_bindings():
        raise HTTPException(status_code=403, detail="Agent not bound (ACL)")
    return x_agent_id
