from lantai.security.secret_guard import find_secret_shapes


def test_detects_openai_key_shape():
    assert find_secret_shapes("key is sk-abcdefghijklmnopqrstuvwx") == ["openai_key"]


def test_detects_fullwidth_key_after_nfkc():
    assert find_secret_shapes("ｓｋ-abcdefghijklmnopqrstuvwx") == ["openai_key"]


def test_plain_text_has_no_hit():
    assert find_secret_shapes("just a normal sentence about code") == []


def test_detects_aws_access_key_shape():
    assert find_secret_shapes("AKIA" + "0" * 16) == ["aws_access_key"]
