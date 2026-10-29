"""
lantai/runtime/middleware.py
Cognitive Middleware（v0.4 轻量版）

从 query/content 字段自动推断 task 上下文，
在 MCP 响应中附加认知摘要（rules + failures），
Agent 无需显式调用 cognitive_context 工具。

原则：
- 不阻塞主链路：try/except 兜底，失败静默跳过（宁 miss 不脏写）
- 只注入最相关的 1-2 条 Rule + 1 条 Failure（≤ 300 字符）
- 无 LLM 调用（纯数据库检索）
"""

from __future__ import annotations

import base64
import urllib.parse


def _rid(item) -> str | None:
    """取记录 id（dict 或 ORM 对象皆可），无则 None（围栏元数据用）。"""
    if isinstance(item, dict):
        return item.get("id")
    return getattr(item, "id", None)


def build_cognitive_summary(
    task: str,
    max_rules: int = 2,
    max_failures: int = 1,
    user_id: str | None = None,
    tenant_id: str | None = None,
    max_probes: int = 1,
    char_budget: int = 350,
) -> str:
    """
    根据 task 描述生成自适应认知摘要字符串（纯文本，基于润物 RunwuEngine）。

    返回格式：
    "相关规则: [rule1] | 已知教训: [failure1.lesson] | 待求证: [probe]"
    或空字符串（无认知数据时）。

    不阻塞调用者：内部捕获所有异常（宁 miss 不脏写）。
    """
    try:
        from lantai.runtime.runwu import RunwuEngine
        from lantai.storage import db as db_module

        with db_module.get_session() as session:
            engine = RunwuEngine(db=session, default_char_budget=char_budget)
            summary = engine.extract_summary(
                task=task,
                user_id=user_id,
                tenant_id=tenant_id,
                max_rules=max_rules,
                max_failures=max_failures,
                max_probes=max_probes,
                char_budget=char_budget,
            )
            return engine.format_compact(summary, budget=char_budget)
    except Exception:
        return ""


def encode_cognitive_header(summary: str) -> str:
    """将认知摘要编码为 Base64（适合 HTTP header 传输）。"""
    if not summary:
        return ""
    return base64.b64encode(summary.encode("utf-8")).decode("ascii")


class CognitiveMiddleware:
    """
    ASGI 中间件（v0.4 轻量版）：
    - 检测 X-Task 请求 header（可选）或从 URL query 参数推断
    - 在响应中附加 X-Cognitive-Context header（Markdown 摘要，Base64）
    - 失败静默跳过

    注意：轻量版仅在有 X-Task header 时触发，避免对每个请求都查询 DB。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # 提取 X-Task header（支持 URL 解码或原始文本）
            headers = dict(scope.get("headers", []))
            raw_task = headers.get(b"x-task", b"").decode("utf-8", errors="ignore").strip()

            if raw_task:
                task_header = urllib.parse.unquote(raw_task)
                # 尝试提取归属信息
                raw_user = (
                    headers.get(b"x-user-id", b"").decode("utf-8", errors="ignore").strip() or None
                )
                raw_tenant = (
                    headers.get(b"x-tenant-id", b"").decode("utf-8", errors="ignore").strip()
                    or None
                )
                summary = build_cognitive_summary(
                    task=task_header, user_id=raw_user, tenant_id=raw_tenant
                )
                encoded = encode_cognitive_header(summary)

                async def send_with_cognitive(message):
                    if message["type"] == "http.response.start" and encoded:
                        # 追加 X-Cognitive-Context header
                        headers_list = list(message.get("headers", []))
                        headers_list.append((b"x-cognitive-context", encoded.encode("ascii")))
                        message = {**message, "headers": headers_list}
                    await send(message)

                await self.app(scope, receive, send_with_cognitive)
                return

        await self.app(scope, receive, send)
