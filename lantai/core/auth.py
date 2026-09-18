"""API Key 鉴权依赖 + 部署绑定安全检查

双密钥并存（与 README / Docker 部署口径一致）：
- 环境变量 `API_KEY`：请求头 `X-API-Key` 恒时比较，命中即 admin Principal；
- 库内 `api_keys`：请求头 `Authorization: Bearer`，命中即该 user/lane Principal；
- DEV MODE 仅允许：回环绑定 + 未配置 API_KEY + 库内无任何 key（本机开发）。
非回环或已配置 API_KEY 时禁止 DEV MODE 回退。
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.models.tables import ApiKey
from lantai.storage import db as db_module

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 与历史 DEV MODE 一致的默认 lane 集合
# distill（v022 咀华泳道）对默认密钥/DEV 可写可召回；显式受限 Bearer 密钥不受影响
DEFAULT_LANES = ["general", "fact", "rule", "experience", "preference", "chat", "default", "distill"]


def is_loopback_host(host: str | None = None) -> bool:
    return (host if host is not None else settings.HOST) in LOOPBACK_HOSTS


def assert_secure_binding() -> None:
    """非回环地址必须配置 API_KEY，否则拒绝启动（lifespan 调用）。"""
    if not is_loopback_host() and not settings.API_KEY:
        raise RuntimeError(
            f"Refusing to start: HOST={settings.HOST} is not loopback "
            "but API_KEY is empty. Set API_KEY or bind to 127.0.0.1."
        )


def match_env_api_key(x_api_key: str | None) -> bool:
    """X-API-Key 与环境变量 API_KEY 恒时比较；未配置 API_KEY 时恒 False。"""
    if not settings.API_KEY or not x_api_key:
        return False
    return hmac.compare_digest(x_api_key.encode("utf-8"), settings.API_KEY.encode("utf-8"))


def db_has_api_key() -> bool:
    with db_module.get_session() as s:
        return s.exec(select(ApiKey)).first() is not None


def dev_mode_allowed() -> bool:
    """DEV MODE 仅：回环 + 无环境 API_KEY + 库内无 key。"""
    if not is_loopback_host():
        return False
    if settings.API_KEY:
        return False
    try:
        return not db_has_api_key()
    except Exception:
        logger.exception("dev_mode_allowed: db check failed, deny DEV MODE")
        return False


async def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> str:
    """验证 X-API-Key header（恒时比较，防时序侧信道）。

    保留供旧调用方使用；业务路由统一走 `get_current_user`（双轨并入）。
    """
    if not settings.API_KEY:
        return "no-auth"

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if not match_env_api_key(x_api_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")

    return x_api_key


@dataclass(frozen=True)
class Principal:
    tenant_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    role: str = "user"
    allowed_lanes: list[str] | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "system")


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
        allowed_lanes=allowed_lanes or ["default"],
    )
    return raw_key, api_key


def get_current_user(request: Request) -> Principal:
    """统一鉴权：X-API-Key（环境 API_KEY）→ Bearer（库内 key）→ 受限 DEV MODE。"""
    auth_header = request.headers.get("Authorization")
    x_api_key = request.headers.get("X-API-Key")

    tenant_id = request.headers.get("X-Tenant-Id")
    agent_id = request.headers.get("X-Agent-Id")
    session_id = request.headers.get("X-Session-Id")

    def make_principal(user_id: str, lanes: list[str], role: str = "user") -> Principal:
        request.state.user_id = user_id
        from lantai.core.acl import active_bindings
        from lantai.core.acl import allowed_lanes as acl_allowed_lanes

        if agent_id and active_bindings():
            if agent_id not in active_bindings():
                raise HTTPException(status_code=403, detail="Agent not bound (ACL)")
            lanes = acl_allowed_lanes(agent_id) or []

        return Principal(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            role=role,
            allowed_lanes=lanes,
        )

    # A: 环境变量 API_KEY（X-API-Key）→ admin
    if match_env_api_key(x_api_key):
        return make_principal("api_key", list(DEFAULT_LANES), role="admin")

    # B: 库内 Bearer key（有凭证则严格校验，失败即 401，不落入 DEV）
    if auth_header:
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid Authorization header format")

        raw_key = auth_header[len("Bearer ") :]
        key_hash = hash_key(raw_key)

        with db_module.get_session() as s:
            api_key = s.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()
            if not api_key or not api_key.is_active:
                raise HTTPException(status_code=401, detail="Invalid API Key")

            return make_principal(api_key.user_id, api_key.allowed_lanes)

    # C: 受限 DEV MODE（仅回环 + 无 API_KEY + 空库）
    if dev_mode_allowed():
        logger.warning("No API Keys found. Entering DEV MODE with fallback context.")
        return make_principal("default", list(DEFAULT_LANES))

    if settings.API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Missing credentials: provide X-API-Key or Authorization Bearer",
        )
    raise HTTPException(status_code=401, detail="Missing Authorization header")
