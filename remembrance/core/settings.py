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


settings = Settings()
