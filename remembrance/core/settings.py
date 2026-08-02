import warnings
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根 = remembrance/core/settings.py → core/ → remembrance/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据根目录——为空时通过 __file__ 自解析仓库根
    REMEMBRANCE_HOME: str = ""

    PORT: int = 8767
    DATABASE_URL: str = ""  # 为空时从 REMEMBRANCE_HOME 自动推导

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    EMBED_MODEL: str = "BAAI/bge-m3"

    INGEST_CRON_MINUTES: int = 60
    EVOLVE_CRON_MINUTES: int = 30
    FORGET_CRON_HOURS: int = 24

    GATE_MIN_EXTRACTOR_CONF: float = 0.55
    PROMOTE_SEMANTIC_MIN_SOURCES: int = 2
    PROCEDURAL_ROLLBACK_HOURS: int = 24
    WORKING_MEMORY_TTL_DAYS: int = 60

    # Lane 分轨衰减：每类记忆的基础保留强度与重要性放大系数
    # base_s = 记忆半衰期（天），importance_boost = 重要性加权分数
    LANE_DECAY_PROFILES: dict = {
        "fact":       {"base_s": 30, "importance_boost": 40},
        "rule":       {"base_s": 60, "importance_boost": 30},
        "experience": {"base_s": 10, "importance_boost": 15},
        "preference": {"base_s": 15, "importance_boost": 20},
        "chat":       {"base_s": 3,  "importance_boost": 5},
        "general":   {"base_s": 10, "importance_boost": 15},
    }
    # 检索时 lane 权重提升系数
    LANE_RETRIEVAL_BOOST: dict = {
        "fact": 1.3, "rule": 1.2, "experience": 1.0,
        "preference": 1.1, "chat": 0.7, "general": 1.0,
    }
    # 默认 lane（修 P0: promoter.py AttributeError）
    DEFAULT_LANE: str = "general"

    # 安全
    API_KEY: str = ""

    # Reranker 配置（硅基流 /v1/rerank）
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_BASE_URL: str = "https://api.siliconflow.cn/v1"
    RERANKER_ENABLED: bool = True
    RERANKER_TIMEOUT: int = 30  # 秒
    RERANKER_RETRY_DELAY: float = 1.0  # 重试等待秒数
    RERANKER_CANDIDATE_MULTIPLIER: int = 2  # 混合检索返回候选数 = top_k * multiplier
    # 意图分类 → 候选集大小
    INTENT_CANDIDATE_SIZES: dict = {
        "fact_lookup": 10,
        "procedural": 15,
        "exploratory": 20,
    }
    DEFAULT_INTENT: str = "fact_lookup"

    # 向量存储配置（默认 Chromadb 内嵌，无需外部依赖）
    VECTOR_STORE_TYPE: str = "chromadb"
    CHROMADB_PATH: str = ""  # 为空时从 REMEMBRANCE_HOME 自动推导

    # 闸门配置
    GATE_CACHE_TTL: float = 15.0  # 热缓存秒数

    def model_post_init(self, __context):
        """DATABASE_URL / CHROMADB_PATH 未显式设置时从 REMEMBRANCE_HOME 推导。"""
        home = Path(self.REMEMBRANCE_HOME) if self.REMEMBRANCE_HOME else _REPO_ROOT
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"sqlite:///{home / 'remembrance.db'}"
        if not self.CHROMADB_PATH:
            self.CHROMADB_PATH = str(home / ".chromadb")

    def validate_config(self):
        """轻量校验——只 warn 不 crash。"""
        if not self.OPENAI_API_KEY:
            warnings.warn("OPENAI_API_KEY not set — LLM features will fail")
        if self.RERANKER_ENABLED and not self.OPENAI_API_KEY:
            warnings.warn(
                "Reranker enabled but no API key — will fall back to no rerank"
            )


settings = Settings()
