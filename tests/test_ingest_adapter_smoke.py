"""票 17 子项 2：arxiv IP 边界判据与 safety 对账（判据级，双真实函数）。

`safety.validate_fetch_url` 是 RSS 主链的 SSRF 参考判据（`not is_global`，
safety.py:32）；`arxiv._assert_public_arxiv_target` 是纵深第二层（host 白名单
+ IP 边界，arxiv.py:31）。审计曾记「arxiv 漏 multicast/unspecified」——
venv 3.11.15 实测翻案：multicast（224.0.0.1/ff02::1）与 unspecified
（0.0.0.0/::）两处判据一致；真实漂移是 **CGNAT 100.64.0.0/10**（safety 拦、
arxiv 四标志放行——内层松于外层）。

对账口径（宁严勿松）：arxiv 拦截集 ⊇ safety 拦截集；arxiv 因 is_reserved
更严的段（NAT64 64:ff9b::/32）如实登记为刻意保守面，不算漂移。
`socket.getaddrinfo` 替身 = 网络边界（AGENTS.md 纪律允许面）；两个被测函数
本体（scheme/host 白名单、IP 谓词）全部真实执行，不 mock。
"""

import ipaddress
import socket

import pytest

from lantai.ingestion.arxiv import _assert_public_arxiv_target
from lantai.ingestion.safety import validate_fetch_url

# (IP 样本, safety 是否拦截)。覆盖审计点名的 multicast/unspecified（实测
# 两处判据一致）与实测漂移段 100.64.0.0/10，外加常规私网/保留段对照。
_SAMPLES = [
    ("8.8.8.8", False),
    ("1.1.1.1", False),
    ("10.0.0.1", True),
    ("172.16.0.1", True),
    ("192.168.1.1", True),
    ("127.0.0.1", True),
    ("169.254.1.1", True),
    ("0.0.0.0", True),
    ("240.0.0.1", True),
    ("255.255.255.255", True),
    ("224.0.0.1", False),  # 审计记「arxiv 漏 multicast」——实测 safety 同样不拦，判据一致
    ("100.64.0.1", True),  # CGNAT：safety 拦（is_global=False），arxiv 修复前放行——真实漂移
    ("::", True),  # unspecified：实测两处一致（is_private 覆盖，非漂移）
    ("::1", True),
    ("fe80::1", True),
    ("fd00::1", True),
    ("ff02::1", False),  # IPv6 multicast：同上，判据一致
    ("2620:0:2d0:200::7", False),
]


def _stub_dns(monkeypatch, ip_str: str) -> None:
    """把两个被测函数看到的 DNS 固定为同一解析结果（网络替身）。"""
    ip = ipaddress.ip_address(ip_str)
    if ip.version == 6:
        family, sockaddr = socket.AF_INET6, (ip_str, 0, 0, 0)
    else:
        family, sockaddr = socket.AF_INET, (ip_str, 0)

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("ip_str,safety_blocks", _SAMPLES)
def test_arxiv_ip_boundary_covers_safety_predicate(monkeypatch, ip_str, safety_blocks):
    """对账：同组 IP 样本过两个真实函数，arxiv 拦截集 ⊇ safety 拦截集。

    safety_blocks=True 而 arxiv 放行即内层松于外层（SSRF 纵深洞）——
    100.64.0.1 在修复前正是此形态（红）。
    """
    _stub_dns(monkeypatch, ip_str)

    def _safety_blocked() -> bool:
        try:
            validate_fetch_url(f"http://{ip_str}/f")
            return False
        except ValueError:
            return True

    def _arxiv_blocked() -> bool:
        try:
            _assert_public_arxiv_target("http://export.arxiv.org/api/query")
            return False
        except ValueError:
            return True

    assert _safety_blocked() is safety_blocks, f"safety 参考判据对 {ip_str} 的判定漂移"
    if safety_blocks:
        assert _arxiv_blocked(), (
            f"{ip_str} 被 safety 拦截（参考判据）却被 arxiv 放行——SSRF 纵深内层松于外层"
        )


def test_arxiv_ip_boundary_keeps_conservative_extras(monkeypatch):
    """宁严勿松：arxiv 原四标志拦下、safety 放行的段（NAT64 64:ff9b::/32，
    is_reserved）对齐后不得放开——对齐是收窄不是互换。"""
    _stub_dns(monkeypatch, "64:ff9b::1.2.3.4")
    # safety 放行（对照臂，现状即绿——is_global=True）
    assert validate_fetch_url("http://8.8.8.8/f") == "http://8.8.8.8/f"
    # arxiv 因 is_reserved 仍拦（刻意保守面）
    with pytest.raises(ValueError):
        _assert_public_arxiv_target("http://export.arxiv.org/api/query")


def test_arxiv_target_rejects_bad_scheme_and_host():
    """host 白名单与 scheme 白名单先行（不触发 DNS）——现状锁。"""
    with pytest.raises(ValueError):
        _assert_public_arxiv_target("ftp://export.arxiv.org/api/query")
    with pytest.raises(ValueError):
        _assert_public_arxiv_target("http://evil.example.com/api/query")
