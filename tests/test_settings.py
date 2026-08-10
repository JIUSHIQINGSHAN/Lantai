"""
T01: P0 修复 + 零硬编码 测试

验证项:
- DEFAULT_LANE 存在（修 P0: promoter.py AttributeError）
- VECTOR_DIMENSION 已删除（ChromaDB 自推断）
- LANTAI_HOME 环境变量 + __file__ 自解析
- validate_config() 只 warn 不 crash
"""
import warnings
from pathlib import Path

import pytest
from lantai.core.settings import Settings, settings


class TestDefaultLane:
    """P0 bug 修复: settings.DEFAULT_LANE"""

    def test_default_lane_exists(self):
        """DEFAULT_LANE 属性存在"""
        assert hasattr(settings, "DEFAULT_LANE")

    def test_default_lane_value(self):
        """DEFAULT_LANE 默认值为 general"""
        assert settings.DEFAULT_LANE == "general"


class TestVectorDimensionDeleted:
    """VECTOR_DIMENSION 已删除"""

    def test_vector_dimension_not_in_fields(self):
        """VECTOR_DIMENSION 不在 settings 字段中"""
        assert not hasattr(settings, "VECTOR_DIMENSION")

    def test_vector_dimension_not_in_class(self):
        """VECTOR_DIMENSION 不在 Settings 类定义中"""
        assert "VECTOR_DIMENSION" not in Settings.model_fields


class TestLantaiHome:
    """LANTAI_HOME 环境变量 + __file__ 自解析"""

    def test_field_exists(self):
        """LANTAI_HOME 字段存在"""
        assert hasattr(settings, "LANTAI_HOME")

    def test_database_url_not_relative(self):
        """DATABASE_URL 不以 ./ 开头（非 CWD 相对路径）"""
        assert not settings.DATABASE_URL.startswith("sqlite:///./")

    def test_database_url_contains_remembrance_db(self):
        """DATABASE_URL 包含 remembrance.db"""
        assert "remembrance.db" in settings.DATABASE_URL

    def test_chromadb_path_not_relative(self):
        """CHROMADB_PATH 不以 ./ 开头（非 CWD 相对路径）"""
        assert not settings.CHROMADB_PATH.startswith("./")

    def test_chromadb_path_contains_chromadb(self):
        """CHROMADB_PATH 包含 .chromadb"""
        assert ".chromadb" in settings.CHROMADB_PATH

    def test_env_override(self, monkeypatch):
        """环境变量 LANTAI_HOME 覆盖默认路径"""
        monkeypatch.setenv("LANTAI_HOME", "/tmp/test_rem_home")
        s = Settings()
        assert "test_rem_home" in s.DATABASE_URL
        assert "test_rem_home" in s.CHROMADB_PATH

    def test_auto_resolve_from_file(self):
        """无环境变量时，从 __file__ 自解析为绝对路径"""
        s = Settings(LANTAI_HOME="")
        # DATABASE_URL 应该是绝对路径，包含 remembrance.db
        assert "remembrance.db" in s.DATABASE_URL
        assert not s.DATABASE_URL.startswith("sqlite:///./")
        # CHROMADB_PATH 应该是绝对路径
        assert Path(s.CHROMADB_PATH).is_absolute()

    def test_legacy_env_fallback(self, monkeypatch):
        """旧环境变量 REMEMBRANCE_HOME 在 LANTAI_HOME 未设置时仍生效"""
        monkeypatch.delenv("LANTAI_HOME", raising=False)
        monkeypatch.setenv("REMEMBRANCE_HOME", "/tmp/test_legacy_home")
        s = Settings(_env_file=None)
        assert "test_legacy_home" in s.DATABASE_URL


class TestValidateConfig:
    """validate_config() 只 warn 不 crash"""

    def test_validate_config_does_not_crash(self):
        """validate_config() 不抛异常"""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            settings.validate_config()

    def test_validate_config_warns_on_missing_api_key(self):
        """缺少 OPENAI_API_KEY 时 warn 而非 crash"""
        s = Settings(OPENAI_API_KEY="")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            s.validate_config()
            assert len(caught) > 0

    def test_validate_config_no_warning_with_full_config(self):
        """完整配置时无 warning"""
        s = Settings(OPENAI_API_KEY="sk-test", RERANKER_ENABLED=False)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            s.validate_config()
            assert len(caught) == 0
