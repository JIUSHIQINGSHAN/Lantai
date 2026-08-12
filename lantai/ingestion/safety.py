"""外部抓取安全：URL 校验 + 限长 + 重定向逐跳复验（SSRF 防护）"""
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from lantai.core.logger import logger
from lantai.core.settings import settings


def validate_fetch_url(url: str) -> str:
    """校验外部抓取 URL。

    规则：协议白名单（http/https）；host 必须存在；解析出的每个 IP 必须
    非私网/回环/link-local/组播。校验失败抛 ValueError。返回原 URL。
    """
    parsed = urlparse(url)
    if parsed.scheme not in settings.SSRF_ALLOWED_SCHEMES:
        raise ValueError(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("missing host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"dns resolve failed: {host}") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast):
            raise ValueError(f"blocked address: {ip} (host={host})")
    return url


def validate_media_url(url: str) -> str:
    """校验目识（vision）图片地址：协议白名单 http/https/data。

    图片由上游 Vision API 取回（兰台不直接 fetch，无 SSRF 面），此处仅
    防协议绕过与空值；data URI 供本地文件转 base64 后直传。
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("media_url must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "data"):
        raise ValueError(f"media_url scheme not allowed: {parsed.scheme!r}")
    if parsed.scheme in ("http", "https") and not parsed.hostname:
        raise ValueError("media_url missing host")
    return url


def validate_api_url(url: str) -> str:
    """校验 API 端点 host 在 allowlist（审计 M7）。

    防止配置被篡改后，把 Bearer 密钥与记忆全文发送到任意地址。
    强制 https + host ∈ settings.ALLOWED_API_HOSTS。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"api url must be https: {url}")
    host = (parsed.hostname or "").lower()
    allowed = set(settings.ALLOWED_API_HOSTS)
    if host not in allowed:
        raise ValueError(f"api host not allowed: {host}")
    return url


def fetch_with_safety(url: str, max_bytes: int | None = None,
                      timeout: float | None = None,
                      max_redirects: int | None = None) -> bytes:
    """带 SSRF 防护的抓取：逐跳校验 + 响应限长。返回响应体 bytes。"""
    max_bytes = max_bytes or settings.SSRF_MAX_BYTES
    timeout = timeout or settings.RSS_TIMEOUT
    max_redirects = max_redirects or settings.SSRF_MAX_REDIRECTS

    current = validate_fetch_url(url)
    for _ in range(max_redirects + 1):
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            r = client.get(current, headers={"User-Agent": "lantai/0.3.3"})
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location")
            if not loc:
                break
            current = validate_fetch_url(str(httpx.URL(current).join(loc)))
            continue
        r.raise_for_status()
        if len(r.content) > max_bytes:
            raise ValueError(f"response too large: {len(r.content)} bytes > {max_bytes}")
        return r.content
    raise ValueError("too many redirects")
