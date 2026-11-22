"""Explicit conversation capture and one-way Markdown downloads.

Routes stay registered when the feature is disabled. The request handler checks
its flag before validation or dependency resolution, and never echoes inputs in
validation, conflict, scope or storage errors.
"""

import re
import sqlite3
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message

from lantai.core.auth import Principal, get_current_user, match_env_api_key
from lantai.core.settings import settings
from lantai.services.conversation_service import ConversationConflict, MessageStore

_NO_STORE = {"Cache-Control": "no-store"}
_MAX_REQUEST_BYTES = 640 * 1024


def authenticate_conversation_request(request: Request) -> Principal:
    """Resolve real credentials before body consumption; tests may inject this seam."""
    return get_current_user(request)


def get_conversation_principal(request: Request) -> Principal:
    """Reuse the identity already authenticated by ConversationRoute."""
    principal = getattr(request.state, "conversation_principal", None)
    if principal is None:
        raise PermissionError("Conversation scope forbidden")
    return principal


async def _limited_request(request: Request) -> Request:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_REQUEST_BYTES:
            raise HTTPException(413, "Conversation request too large")
        body.extend(chunk)
    buffered: bytes | None = bytes(body)

    async def receive() -> Message:
        nonlocal buffered
        if buffered is None:
            return await request.receive()
        payload, buffered = buffered, None
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(request.scope, receive=receive)


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=_NO_STORE)


class ConversationRoute(APIRoute):
    """Keep errors local to this surface; global validation errors include input."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            if not settings.FEATURE_CONVERSATION_CAPTURE:
                return _error(404, "Conversation capture is disabled")
            authorization = (request.headers.get("Authorization") or "").strip()
            if not authorization and not match_env_api_key(request.headers.get("X-API-Key")):
                return _error(401, "Authentication required")
            # General auth accepts these labels from request headers. This new
            # surface only exposes the authenticated user's empty tenant/agent
            # scope until a trusted binding exists; workspace is never identity.
            if request.headers.get("X-Tenant-Id") or request.headers.get("X-Agent-Id"):
                return _error(403, "Conversation scope forbidden")
            try:
                authenticate = request.app.dependency_overrides.get(
                    authenticate_conversation_request, authenticate_conversation_request
                )
                principal = await run_in_threadpool(authenticate, request)
                if (
                    principal is None
                    or not isinstance(principal.user_id, str)
                    or not principal.user_id.strip()
                ):
                    raise PermissionError("Conversation scope forbidden")
                # Environment-key authentication reserves this scope. A regular
                # Bearer account with the same user label must never share it.
                if principal.user_id == "api_key" and not principal.is_admin:
                    raise PermissionError("Conversation scope forbidden")
                request.state.conversation_principal = principal
                response = await handler(await _limited_request(request))
            except RequestValidationError:
                return _error(422, "Invalid conversation request")
            except ConversationConflict:
                return _error(409, "Conversation message conflicts with existing record")
            except PermissionError:
                return _error(403, "Conversation scope forbidden")
            except ValueError:
                return _error(422, "Invalid conversation request")
            except (SQLAlchemyError, sqlite3.Error):
                return _error(503, "Conversation storage unavailable")
            except StarletteHTTPException as exc:
                detail = {
                    401: "Authentication required",
                    403: "Conversation scope forbidden",
                    404: "Conversation message not found",
                    413: "Conversation request too large",
                }.get(exc.status_code, "Conversation request failed")
                return _error(exc.status_code, detail)
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(prefix="/conversation", tags=["conversation"], route_class=ConversationRoute)


class ConversationCaptureReq(BaseModel):
    """Preserve input text; MessageStore owns validation and persistence rules."""

    model_config = ConfigDict(extra="forbid", strict=True)

    host_id: str
    conversation_id: str
    source_message_id: str
    workspace_id: str = ""
    role: str
    completion: str = "completed"
    content: str
    occurred_at: str | None = None


def get_message_store() -> MessageStore:
    """Resolve the active engine per request; tests override this dependency."""
    from lantai.storage import db

    return MessageStore(db.engine)


@router.post("/messages", status_code=201)
def capture_message(
    req: ConversationCaptureReq,
    response: Response,
    principal: Principal = Depends(get_conversation_principal),
    store: MessageStore = Depends(get_message_store),
) -> dict:
    """Return a receipt after the original message and its pending job commit."""
    receipt = store.capture(principal, req.model_dump())
    response.status_code = 200 if receipt["replayed"] else 201
    return receipt


@router.get("/messages")
def list_messages(
    host_id: str,
    conversation_id: str,
    workspace_id: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=2**63 - 1),
    principal: Principal = Depends(get_conversation_principal),
    store: MessageStore = Depends(get_message_store),
) -> dict:
    """Return a bounded page from one conversation in the caller's scope."""
    return store.list_messages(
        principal,
        host_id=host_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
    )


@router.get("/messages/{message_id}")
def get_message(
    message_id: str,
    principal: Principal = Depends(get_conversation_principal),
    store: MessageStore = Depends(get_message_store),
) -> dict:
    """Return the original message visible to this authenticated caller."""
    message = store.get(principal, message_id)
    if message is None:
        raise HTTPException(404, "Conversation message not found")
    return message


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: str,
    principal: Principal = Depends(get_conversation_principal),
    store: MessageStore = Depends(get_message_store),
) -> dict:
    """Clear the body and preserve the identity used to reject old replays."""
    message = store.delete(principal, message_id)
    if message is None:
        raise HTTPException(404, "Conversation message not found")
    return message


@router.get("/messages/{message_id}/markdown", response_class=Response)
def download_markdown(
    message_id: str,
    principal: Principal = Depends(get_conversation_principal),
    store: MessageStore = Depends(get_message_store),
) -> Response:
    """Download the original message as a fenced Markdown attachment."""
    message = store.get(principal, message_id)
    if message is None or message["status"] == "deleted":
        raise HTTPException(404, "Conversation message not found")
    content = message["content"]
    fence_length = max(3, max((len(run) + 1 for run in re.findall(r"`+", content)), default=3))
    fence = "`" * fence_length
    # Only bounded, validated labels are rendered outside the literal code block.
    # Raw text is not rendered as Markdown/HTML and its existing whitespace stays.
    markdown = (
        "# Conversation message\n\n"
        f"- Role: {message['role']}\n"
        f"- Completion: {message['completion']}\n"
        f"- Status: {message['status']}\n"
        f"- Occurred at: {message['occurred_at'] or 'unknown'}\n"
        f"- Received at: {message['received_at']}\n\n"
        "## Original text\n\n"
        f"{fence}text\n{content}\n{fence}\n"
    )
    return Response(
        markdown,
        media_type="text/markdown",
        headers={
            **_NO_STORE,
            "Content-Disposition": 'attachment; filename="conversation-message.md"',
            "X-Content-Type-Options": "nosniff",
        },
    )
