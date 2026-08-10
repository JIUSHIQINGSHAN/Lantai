"""SSRF 防护测试：URL 校验规则"""
import pytest

from lantai.ingestion.safety import validate_api_url, validate_fetch_url


def test_scheme_whitelist():
    with pytest.raises(ValueError):
        validate_fetch_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        validate_fetch_url("ftp://example.com/x")


def test_loopback_blocked():
    with pytest.raises(ValueError):
        validate_fetch_url("http://127.0.0.1/health")
    with pytest.raises(ValueError):
        validate_fetch_url("http://localhost/health")
    with pytest.raises(ValueError):
        validate_fetch_url("http://[::1]/health")


def test_private_and_linklocal_blocked():
    with pytest.raises(ValueError):
        validate_fetch_url("http://192.168.1.1/x")
    with pytest.raises(ValueError):
        validate_fetch_url("http://10.0.0.1/x")
    with pytest.raises(ValueError):
        validate_fetch_url("http://169.254.169.254/latest/meta-data/")


def test_public_url_allowed():
    # 公网 IP 字面量（不依赖 DNS 可达性），仅校验判定函数
    validate_fetch_url("https://8.8.8.8/feed.xml")


def test_api_url_requires_https():
    with pytest.raises(ValueError):
        validate_api_url("http://api.openai.com/v1")


def test_api_url_host_allowlist():
    # 默认 allowlist 内 host 通过
    validate_api_url("https://api.openai.com/v1")
    validate_api_url("https://api.siliconflow.cn/v1")
    # 任意其他 host 拒绝
    with pytest.raises(ValueError):
        validate_api_url("https://evil.example.com/v1")
    with pytest.raises(ValueError):
        validate_api_url("https://169.254.169.254/v1")
