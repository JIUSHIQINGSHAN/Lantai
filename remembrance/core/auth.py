"""API Key 鉴权依赖"""
from fastapi import Depends, HTTPException, Header
from remembrance.core.settings import settings


async def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> str:
    """验证 X-API-Key header"""
    if not settings.API_KEY:
        return "no-auth"

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")

    return x_api_key


# 公共端点前缀（不需要鉴权）
PUBLIC_PATHS = {"/health", "/api/memory/health", "/docs", "/openapi.json"}


def is_public_path(path: str) -> bool:
    """判断是否为公共端点"""
    return path.startswith("/health") or path.startswith("/docs") or path == "/openapi.json"
