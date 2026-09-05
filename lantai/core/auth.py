"""API Key 鉴权依赖 + 部署绑定安全检查"""
import hmac

from fastapi import Header, HTTPException

from lantai.core.settings import settings

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def assert_secure_binding() -> None:
    """非回环地址必须配置 API_KEY，否则拒绝启动（lifespan 调用）。"""
    if settings.HOST not in LOOPBACK_HOSTS and not settings.API_KEY:
        raise RuntimeError(
            f"Refusing to start: HOST={settings.HOST} is not loopback "
            "but API_KEY is empty. Set API_KEY or bind to 127.0.0.1."
        )


async def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> str:
    """验证 X-API-Key header（恒时比较，防时序侧信道）"""
    if not settings.API_KEY:
        return "no-auth"

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if not hmac.compare_digest(x_api_key.encode("utf-8"),
                               settings.API_KEY.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid API Key")

    return x_api_key


import hashlib
import secrets
from pydantic import BaseModel
from sqlmodel import select
from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.models.tables import ApiKey
from fastapi import Request
from lantai.storage import db as db_module


class SecurityContext(BaseModel):
    user_id: str
    allowed_lanes: list[str]


def hash_key(raw_key: str) -> str:
    """Hash an API key using SHA-256 for secure storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(user_id: str, allowed_lanes: list[str] = None) -> tuple[str, ApiKey]:
    """Create a new API key and return the raw key and the DB object."""
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    api_key = ApiKey(
        id=new_id("apikey"),
        key_hash=hash_key(raw_key),
        user_id=user_id,
        allowed_lanes=allowed_lanes or ["default"]
    )
    return raw_key, api_key


def get_current_user(request: Request) -> SecurityContext:
    """FastAPI dependency to extract and validate the Bearer token."""
    auth_header = request.headers.get("Authorization")
    
    if not auth_header:
        # F13 ADR-0040: Dev fallback mode if no keys exist
        with db_module.get_session() as s:
            if s.exec(select(ApiKey)).first() is None:
                # DB has no API keys, seed dev mode
                logger.warning("No API Keys found. Entering DEV MODE with fallback context.")
                request.state.user_id = "default"
                return SecurityContext(user_id="default", allowed_lanes=["general", "fact", "rule", "experience", "preference", "chat", "default"])
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    raw_key = auth_header[len("Bearer "):]
    key_hash = hash_key(raw_key)

    with db_module.get_session() as s:
        api_key = s.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()
        
        if not api_key or not api_key.is_active:
            raise HTTPException(status_code=401, detail="Invalid API Key")
        
        ctx = SecurityContext(user_id=api_key.user_id, allowed_lanes=api_key.allowed_lanes)
        request.state.user_id = ctx.user_id
        return ctx
