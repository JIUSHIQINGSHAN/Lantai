"""测试环境契约（现状整改票 02）：部署 .env 不得渗入测试进程。"""

from lantai.core.settings import settings


def test_api_key_sanitized_in_test_env():
    """DEV MODE 免认证测试类的前提：settings.API_KEY 在测试进程中恒为空。

    conftest autouse 消毒 fixture 兜底；本测试把它钉成显式契约——
    将来 conftest 撤掉消毒时这里先红，而不是 70 个用例集体 401。
    """
    assert settings.API_KEY == ""
