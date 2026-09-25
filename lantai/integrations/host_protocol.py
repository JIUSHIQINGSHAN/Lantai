"""宿主协议归一化层（票 06 宿主矩阵 / 片 01）。

把 shell hook 的协议解析、字段校验与结果渲染从 `scripts/shell_hook.py` 抽出，
使该脚本退化为「读 stdin → 归一化 → 分发 → 渲染 → 写 stdout」的宿主实现之一。

设计约束：
- **纯函数、无 IO、无子进程**——可直测；超时包装不进本层（超时是进程边界的事）。
- **行为逐条平移**：判定顺序与结果须与抽取前的 `_handle_one` 一致，由
  `tests/test_shell_hook.py`（走 `_handle_one` 的既有断言）零改动通过来证明。
- 本层**不含业务逻辑**（不检索、不写库）；handler 侧**不含协议逻辑**。

契约的权威描述见 `docs/host-hook-protocol.md`；决策沿革见 ADR-0006。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lantai.core.text import normalize_session_id

# 动作集合：前四个按 `type` 字段精确匹配；无匹配时回落 query（现状语义）。
ACTION_QUERY = "query"
ACTION_DIALOGUE = "dialogue"
ACTION_BACKFILL = "backfill"
ACTION_CHECKPOINT = "checkpoint"
ACTION_CHECKPOINT_WRITE = "checkpoint_write"


@dataclass(frozen=True)
class HostRequest:
    """归一化后的宿主请求（不可变值对象）。

    各动作只填自己用得到的字段，其余保持缺省——调用方按 `action` 取用。
    `session_id` 用 `None`/`""` 区分语境（query 缺省 None、dialogue 缺省 ""），
    这是抽取前既有的差异，保留以免改变已落库的来源链值。
    """

    action: str
    query: str = ""
    session_id: str | None = None
    turn: int | None = None
    text: str = ""
    event_id: str = ""
    used_ids: tuple[str, ...] = ()
    request_id: str | None = None
    blocks: dict = field(default_factory=dict)


def parse_host_request(raw: str) -> HostRequest | None:
    """把宿主输入帧归一化为 `HostRequest`；无效帧返回 `None`（调用方降级为 `{}`）。

    无效的定义（宁 miss 不脏写）：空串/纯空白、非 JSON、**非对象 JSON**、
    或该动作的关键字段不合法（见各分支）。

    非对象 JSON（`[1,2]` / `null` / `123` / `"str"`）此前会让 `_handle_one`
    抛 `AttributeError`——在 `--serve` 常驻循环里一个畸形帧即打死进程。本层
    统一返回 `None`（坏帧静默降级，不拖垮宿主）。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    action = data.get("type")

    if action == ACTION_DIALOGUE:
        text = data.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return None
        # 来源链（P0 票02）：会话标识经单一真源归一化，缺省 ""（宁 miss 不脏写）
        session_id = normalize_session_id(data.get("session_id"), default="")
        # turn 契约 1-based：0/负数/非整数/布尔一律非法（布尔是 int 子类，须显式排除）
        turn = data.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
            turn = None
        return HostRequest(action=ACTION_DIALOGUE, text=text, session_id=session_id, turn=turn)

    if action == ACTION_BACKFILL:
        event_id = data.get("event_id")
        used_ids = data.get("used_ids")
        # 回执是整体覆盖语义：空表/非法表会抹掉既有弱标注，故整帧拒绝
        if not (isinstance(event_id, str) and event_id):
            return None
        if not isinstance(used_ids, list) or not used_ids:
            return None
        if not all(isinstance(x, str) for x in used_ids):
            return None
        rid = data.get("request_id")
        request_id = rid if isinstance(rid, str) and rid.strip() else None
        return HostRequest(
            action=ACTION_BACKFILL,
            event_id=event_id,
            used_ids=tuple(used_ids),
            request_id=request_id,
        )

    if action == ACTION_CHECKPOINT:
        return HostRequest(action=ACTION_CHECKPOINT)

    if action == ACTION_CHECKPOINT_WRITE:
        # 注意：此处 session_id 不过 normalize_session_id（抽取前的既有差异），
        # 保留原样以免改变已落库的来源链值；见 Comments「已知不一致」。
        session_id = data.get("session_id", "")
        blocks = data.get("blocks")
        if not isinstance(session_id, str) or not isinstance(blocks, dict):
            return None
        return HostRequest(action=ACTION_CHECKPOINT_WRITE, session_id=session_id, blocks=blocks)

    # 回落 query：无 type 或 type 未匹配（现状语义）。字段按 falsy 回退，
    # 非字符串（如 123）视为无效帧而非交由下游抛错。
    query = data.get("query") or data.get("message") or data.get("prompt") or ""
    if not isinstance(query, str):
        return None
    return HostRequest(
        action=ACTION_QUERY,
        query=query,
        session_id=normalize_session_id(data.get("session_id"), default=None),
    )


def render_host_response(result: dict) -> str:
    """把标准结果渲染为宿主输出帧（UTF-8 JSON，中文不转义）。"""
    return json.dumps(result, ensure_ascii=False)
