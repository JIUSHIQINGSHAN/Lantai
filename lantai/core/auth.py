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
