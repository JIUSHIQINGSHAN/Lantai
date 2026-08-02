from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PORT: int = 8767
    DATABASE_URL: str = "sqlite:///./remembrance.db"

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    EMBED_MODEL: str = "text-embedding-3-small"

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
    VECTOR_STORE_TYPE: str = "chromadb"  # chromadb / pgvector
    VECTOR_DIMENSION: int = 1024  # bge-m3 输出维度
    CHROMADB_PATH: str = "./.chromadb"

    # 闸门配置
    GATE_CACHE_TTL: float = 15.0  # 热缓存秒数


settings = Settings()
