"""宿主帧适配（票 06 宿主矩阵 / 片 03）。

把兰台自有响应形状 `{context, evidence, event_id, request_id}` 翻译成各宿主
钩子协议要求的输出帧。**请求侧无需适配**：各宿主 `UserPromptSubmit` 帧均带
`prompt` 字段，已被归一化层的 query 回退覆盖（见 `host_protocol.parse_host_request`）。

事实依据（官方文档一手来源）：
- Claude Code 与 Codex CLI 的注入通道均为
  `{"hookSpecificOutput": {"hookEventName": "<事件>", "additionalContext": "..."}}`；
  Claude Code: code.claude.com/docs/en/hooks；
  Codex: learn.chatgpt.com/docs/config-file/config-reference（`additionalContextLimit`）。
- Hermes 走插件通道，直接消费兰台自有响应形状（直通）。

**只做读路径注入**：不涉及各宿主的阻塞/拒绝语义（Codex 的阻塞语义官方未完整载明，
不作为设计前提）。
"""

from __future__ import annotations

# 命令钩子矩阵中需翻译响应形状的宿主 → 注入事件名。
# Hermes 走插件通道不在此表（直通）。
HOST_EVENT: dict[str, str] = {
    "claude-code": "UserPromptSubmit",
    "codex": "UserPromptSubmit",
}


def adapt_response(host: str | None, result: dict) -> dict:
    """把标准响应帧翻译为该宿主钩子的输出帧。

    - 未登记宿主（含 Hermes / None）→ 直通（兰台自有形状）。
    - 已登记宿主 → `hookSpecificOutput.additionalContext` 形状。
    - **无 context 即无注入**：返回 `{}`（宿主视作无意见），绝不产出空串注入——
      空 `additionalContext` 会在宿主侧留下无意义的空上下文条目。

    **兰台元数据作为顶层兄弟字段带出**（`event_id`/`request_id`/`evidence`）：
    回执（backfill）需要 event_id，而 `additionalContext` 通道不承载它——宿主侧
    需一个薄 wrapper 捕获该字段供稍后回执。这是 CC/Codex 上回执可用的**必要**
    条件（Hermes 走自有形状，字段本就顶层）。未知顶层字段按 JSON 惯例被宿主
    忽略；如某宿主拒绝未知字段，须在接入时调整（见 `docs/host-hook-protocol.md`）。
    """
    event = HOST_EVENT.get(host) if host else None
    if event is None:
        return result

    # 只翻译**注入类**响应（带 context 的读路径）。回执/对话/底本等写路径结果
    # （`{ok, receipt_status, ...}`）须原样返回——它们是控制面应答，宿主侧 wrapper
    # 需要读到 `receipt_status` 等字段；强行包成 additionalContext 会把它们抹掉。
    context = result.get("context")
    if not context:
        return result
    out: dict = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }
    for key in ("event_id", "request_id", "evidence"):
        if result.get(key):
            out[key] = result[key]
    return out
