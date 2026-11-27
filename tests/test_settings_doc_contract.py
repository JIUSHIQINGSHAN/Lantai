"""阶段 6-C：真实配置解析、文档示例与精排凭据诊断契约。"""

from __future__ import annotations

import ast
import json
import re
import warnings
from pathlib import Path
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from lantai.core.settings import Settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def isolated_settings_home(tmp_path, monkeypatch):
    """只改变环境与用户目录边界，目录选择与配置计算使用真实实现。"""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _historical_home(home: Path) -> Path:
    import sys

    return (
        home / "AppData" / "Local" / "remembrance-data"
        if sys.platform == "win32"
        else home / ".remembrance-data"
    )


@pytest.mark.parametrize("has_database", [False, True])
def test_data_home_historical_probe_requires_database(isolated_settings_home, has_database):
    old = _historical_home(isolated_settings_home)
    old.mkdir(parents=True)
    if has_database:
        (old / "remembrance.db").touch()

    config = Settings(_env_file=None)
    expected = old if has_database else REPO
    assert config.data_home == expected
    assert f"sqlite:///{expected / 'remembrance.db'}" == config.DATABASE_URL
    assert str(expected / ".chromadb") == config.CHROMADB_PATH


@pytest.mark.parametrize("source", ["LANTAI_HOME", "REMEMBRANCE_HOME"])
def test_explicit_home_precedes_historical_probe(isolated_settings_home, monkeypatch, source):
    old = _historical_home(isolated_settings_home)
    old.mkdir(parents=True)
    (old / "remembrance.db").touch()
    explicit = isolated_settings_home / "explicit"
    monkeypatch.setenv(source, str(explicit))

    config = Settings(_env_file=None)
    assert str(explicit) == config.LANTAI_HOME
    assert config.data_home == explicit


def test_environment_overrides_dotenv_and_defaults(isolated_settings_home, monkeypatch):
    env_file = isolated_settings_home / ".env"
    env_file.write_text(
        "LANTAI_HOME=from-file\nREMEMBRANCE_HOME=old-from-file\nPORT=12345\n"
        "RERANKER_ENABLED=false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LANTAI_HOME", str(isolated_settings_home / "from-env"))
    monkeypatch.setenv("PORT", "23456")

    config = Settings(_env_file=env_file)
    assert config.data_home == isolated_settings_home / "from-env"
    assert config.PORT == 23456
    assert config.RERANKER_ENABLED is False
    assert Settings.model_fields["HOST"].default == config.HOST


def test_explicit_storage_paths_are_not_replaced(isolated_settings_home):
    config = Settings(
        _env_file=None,
        LANTAI_HOME=str(isolated_settings_home / "data"),
        DATABASE_URL="sqlite:///:memory:",
        CHROMADB_PATH=str(isolated_settings_home / "vectors"),
    )
    assert config.DATABASE_URL == "sqlite:///:memory:"
    assert str(isolated_settings_home / "vectors") == config.CHROMADB_PATH


def test_env_example_keys_and_values_parse_with_real_settings(isolated_settings_home):
    example = REPO / ".env.example"
    values = dotenv_values(example)
    assert values
    assert set(values) <= set(Settings.model_fields), "示例不得含被 extra=ignore 静默吞掉的键"
    config = Settings(_env_file=example)
    for name, raw in values.items():
        value = getattr(config, name)
        if raw == "" and name == "LANTAI_HOME":
            assert config.data_home == REPO
        elif isinstance(value, bool):
            assert value is (raw.lower() == "true")
        elif isinstance(value, dict):
            assert value == json.loads(raw)
        elif isinstance(value, (int, float)):
            assert value == float(raw)
        else:
            assert value == raw


@pytest.mark.parametrize(
    ("llm_url", "rerank_url", "llm_key", "rerank_key", "enabled", "expected"),
    [
        ("https://api.openai.com/v1", "https://api.siliconflow.cn/v1", "test-llm", "", True, True),
        (
            "https://api.openai.com/v1",
            "https://api.siliconflow.cn/v1",
            "",
            "test-rerank",
            True,
            False,
        ),
        (
            "https://api.siliconflow.cn/v1",
            "https://api.siliconflow.cn/v1",
            "test-llm",
            "",
            True,
            False,
        ),
        ("https://api.openai.com/v1", "https://api.openai.com/v1", "test-llm", "", True, False),
        ("https://api.openai.com/v1", "https://api.openai.com/v1", "", "", True, True),
        ("https://api.openai.com/v1", "https://api.siliconflow.cn/v1", "", "", False, False),
    ],
)
def test_reranker_warning_matches_usable_credentials(
    isolated_settings_home, llm_url, rerank_url, llm_key, rerank_key, enabled, expected
):
    config = Settings(
        _env_file=None,
        OPENAI_BASE_URL=llm_url,
        RERANKER_BASE_URL=rerank_url,
        OPENAI_API_KEY=llm_key,
        RERANKER_API_KEY=rerank_key,
        RERANKER_ENABLED=enabled,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config.validate_config()
    rerank_warnings = [warning for warning in caught if "Reranker" in str(warning.message)]
    assert bool(rerank_warnings) is expected
    llm_warnings = [warning for warning in caught if "LLM features" in str(warning.message)]
    assert bool(llm_warnings) is (not bool(llm_key))


def test_readme_documents_home_probe_and_rerank_key_contract():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    home_row = next(line for line in readme.splitlines() if line.startswith("| `LANTAI_HOME` |"))
    assert "自动探测" in home_row
    assert "remembrance-data" in readme
    key_row = next(
        (line for line in readme.splitlines() if line.startswith("| `RERANKER_API_KEY` |")), ""
    )
    assert "同 host" in key_row
    assert "跨" in key_row


def test_readme_literal_defaults_match_settings_model():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    section = text.split("## 环境变量", 1)[1].split("## 测试", 1)[0]
    checked = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        names = re.findall(r"`([A-Z][A-Z_]+)`", cells[0])
        values = cells[1].replace("`", "").split(" / ")
        if len(names) != len(values):
            continue
        for name, value in zip(names, values, strict=True):
            default = Settings.model_fields[name].default
            if isinstance(default, bool):
                assert value == str(default).lower(), name
            elif isinstance(default, (int, float)):
                assert float(value) == default, name
            elif isinstance(default, str) and default:
                assert value == default, name
            elif isinstance(default, list):
                assert json.loads(value) == default, name
            else:
                continue  # 空路径需推导、集合及密钥使用描述，另有行为测试。
            checked.add(name)
    assert {
        "HOST",
        "PORT",
        "OPENAI_BASE_URL",
        "LLM_MODEL",
        "EMBED_MODEL",
        "RERANKER_BASE_URL",
        "RERANKER_ENABLED",
        "DEDUP_MERGE_THRESHOLD",
        "DEDUP_UPDATE_THRESHOLD",
        "ALLOWED_API_HOSTS",
        "GATE_CACHE_TTL",
        "COALESCE_ENABLED",
        "MONITOR_ENABLED",
        "MONITOR_PERSIST_SLOW_MS",
        "MONITOR_PERSIST_SAMPLE",
        "MONITOR_RETENTION_DAYS",
        "CONSOLIDATION_AUDIT_MODE",
        "REFLECT_AUTO_APPLY",
    } <= checked


def test_agent_release_gate_count_matches_implementation_and_runbook():
    tree = ast.parse((REPO / "scripts/release_check.py").read_text(encoding="utf-8"))
    names = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "check"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert names
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    release = (REPO / "docs/release-process.md").read_text(encoding="utf-8")
    assert f"{len(names)} 项" in agents
    assert f"{len(names)} 项" in release


@pytest.mark.parametrize("url", ["", "not-a-url", "https://[invalid"])
def test_invalid_rerank_url_keeps_diagnostics_warning_only(isolated_settings_home, url):
    config = Settings(
        _env_file=None,
        OPENAI_API_KEY="test-llm",
        RERANKER_BASE_URL=url,
        RERANKER_API_KEY="",
    )
    with pytest.warns(UserWarning, match="Reranker"):
        config.validate_config()


@pytest.mark.parametrize("dedicated_key", ["", "test-dedicated"])
def test_same_provider_rerank_uses_expected_key(isolated_settings_home, monkeypatch, dedicated_key):
    from lantai.retrieval import reranker

    config = Settings(
        _env_file=None,
        OPENAI_BASE_URL="https://api.siliconflow.cn/v1",
        OPENAI_API_KEY="test-shared",
        RERANKER_BASE_URL="https://API.SILICONFLOW.CN/v1",
        RERANKER_API_KEY=dedicated_key,
    )
    monkeypatch.setattr(reranker, "settings", config)
    response = Mock()
    response.json.return_value = {"results": [{"index": 0, "score": 0.9}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(reranker.requests, "post", post)

    result = reranker.rerank("query", ["document"], top_k=1)

    assert result == [{"index": 0, "score": 0.9, "document": "document"}]
    post.assert_called_once()
    assert post.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer " + (dedicated_key or "test-shared")
    )
