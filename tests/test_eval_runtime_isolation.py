"""Real configuration/path checks; no application imports, network or production I/O."""

import pytest

from scripts.eval_runtime import assert_separate_home, production_home, require_api_key


def test_production_home_is_forwardable_and_server_requires_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("REMEMBRANCE_PROD_HOME", raising=False)
    monkeypatch.delenv("LANTAI_HOME", raising=False)
    monkeypatch.delenv("REMEMBRANCE_HOME", raising=False)
    assert production_home({"LANTAI_HOME": str(tmp_path)}, repo_root=tmp_path / "repo") == tmp_path
    with pytest.raises(SystemExit, match="REMEMBRANCE_PROD_HOME"):
        production_home(require_explicit=True)
    monkeypatch.setenv("REMEMBRANCE_PROD_HOME", str(tmp_path))
    assert production_home(require_explicit=True) == tmp_path


def test_api_key_uses_configured_value_and_missing_fails_closed(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        require_api_key({})
    assert require_api_key({"API_KEY": "test-dotenv-key"}) == "test-dotenv-key"
    monkeypatch.setenv("API_KEY", "test-env-key")
    assert require_api_key({"API_KEY": "test-dotenv-key"}) == "test-env-key"


def test_home_overlap_rejects_before_creating_directories(tmp_path):
    production = tmp_path / "prod"
    for evaluation in (production, production / "child", tmp_path):
        with pytest.raises(SystemExit, match="双闸失败"):
            assert_separate_home(evaluation, production)
    sibling = tmp_path / "prod-evaluation"
    assert assert_separate_home(sibling, production) == sibling
    assert not production.exists() and not sibling.exists()
