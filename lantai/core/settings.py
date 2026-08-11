import logging
import os
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根 = lantai/core/settings.py → core/ → lantai/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据根目录——为空时通过 __file__ 自解析仓库根
    LANTAI_HOME: str = ""
    # 兼容旧配置：REMEMBRANCE_HOME 仍在 .env/环境变量时自动沿用（LANTAI_HOME 优先）
    REMEMBRANCE_HOME: str = ""

    HOST: str = "127.0.0.1"  # 默认只监听回环；非回环部署必须同时设置 API_KEY
    PORT: int = 8767
    DATABASE_URL: str = ""  # 为空时从 LANTAI_HOME 自动推导

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

    # 候选可见队列（Ticket 02）：reject 进待审队列，超龄自动归档
    CANDIDATE_TTL_DAYS: int = 7

    # 对话写通道（Ticket 01）：分 lane 自动提取 vs 闲聊入队
    DIALOGUE_ENABLED: bool = True
    DIALOGUE_MIN_CHARS: int = 8
    # 对话通道专用提取置信度门槛：低于此值（含提取失败兜底 0.3）进待审队列
    DIALOGUE_MIN_EXTRACTOR_CONF: float = 0.55
    CANDIDATE_TTL_CRON_HOURS: int = 24

    # Daily Digest（Ticket 03）：每日盘点报告 docs/memory-digest/YYYY-MM-DD.md
    DIGEST_ENABLED: bool = True
    DIGEST_CRON_HOUR: int = 22  # UTC 22:00 = 本地(Asia/Shanghai) 06:00 早晨
    DIGEST_OUTPUT_DIR: str = ""  # 为空时 = 仓库根 docs/memory-digest

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
    # Raw Drawer 原文直存（P0-1）：verbatim 记忆默认 lane（零 LLM、直接写 MemoryItem）
    RAW_MEMORY_DEFAULT_LANE: str = "general"

    # 冲突消解确定性层（P0-2）：互斥规则集——pair 内两项互斥，规则命中即确定性冲突
    CONFLICT_RULES_ENABLED: bool = True
    CONFLICT_MUTEX_RULES: list = [
        {"name": "status_switch", "pair": ["启用", "禁用"]},
        {"name": "toggle", "pair": ["已开启", "已关闭"]},
        {"name": "version_change", "pair": ["版本 1", "版本 2"]},
    ]

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
    CHROMADB_PATH: str = ""  # 为空时从 LANTAI_HOME 自动推导

    # 闸门配置
    GATE_CACHE_TTL: float = 15.0  # 热缓存秒数

    # Coalesce 潮波合并配置
    COALESCE_ENABLED: bool = False  # 默认 false 向后兼容
    LANE_COALESCE_PROFILES: dict = {
        "fact":       {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
        "rule":       {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
        "experience": {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
        "preference": {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
        "chat":       {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
        "general":    {"idle_timeout": 4.0, "window": 12.0, "max_parts": 8, "max_chars": 2000},
    }

    # 遗忘配置
    ARCHIVE_DECAY_THRESHOLD: float = 0.01  # decay 低于此值自动 archived

    # 去重配置
    DEDUP_MERGE_THRESHOLD: float = 0.80  # 余弦相似度 > 此值 → merge
    DEDUP_UPDATE_THRESHOLD: float = 0.65  # 余弦相似度 > 此值 → update

    # Shell Hook 配置
    SHELL_HOOK_TIMEOUT: int = 2  # 秒
    SHELL_HOOK_DIALOGUE_TIMEOUT: float = 30.0  # 对话写入通道超时（秒，含 LLM 提取）
    SHELL_HOOK_TOP_K: int = 5
    SHELL_HOOK_MIN_CHARS: int = 3

    # SSRF 防护
    SSRF_ALLOWED_SCHEMES: tuple = ("http", "https")
    SSRF_MAX_REDIRECTS: int = 3
    SSRF_MAX_BYTES: int = 5 * 1024 * 1024  # 5MB
    RSS_TIMEOUT: int = 30

    # 备份恢复
    BACKUP_MANIFEST_VERSION: str = "0.3.3"

    # 混合检索权重（ADR-0008）
    RETRIEVAL_W_VECTOR: float = 0.6
    RETRIEVAL_W_BM25: float = 0.25
    RETRIEVAL_W_FTS: float = 0.05
    RETRIEVAL_W_DECAY: float = 0.1
    FTS_RECALL_TOP_K: int = 20  # FTS 子串召回上限

    # API 端点 allowlist（审计 M7）：LLM/reranker 客户端只允许访问这些 host
    ALLOWED_API_HOSTS: list = ["api.openai.com", "api.siliconflow.cn"]
    # reranker 独立最小权限密钥；为空时回退 OPENAI_API_KEY（并记录警告）
    RERANKER_API_KEY: str = ""

    # 参数建议系统（论文驱动优化·辅助模式）——自身不可被论文建议修改
    PARAM_ADVICE_ENABLED: bool = True
    PARAM_ADVICE_CRON_MINUTES: int = 30          # advice worker 兜底调度间隔
    PARAM_ADVICE_MIN_PAPERS: int = 5             # 批量窗口：未处理论文数阈值
    PARAM_ADVICE_MAX_WAIT_DAYS: int = 7          # 批量窗口：最老论文最大等待天数
    PARAM_ADVICE_MAX_BATCH_SIZE: int = 10        # 单批最多论文数
    PARAM_ADVICE_MIN_CONFIDENCE: float = 0.85    # 建议置信度阈值
    PARAM_ADVICE_MAX_CHANGES: int = 6            # 单条建议最大变更数
    PARAM_ADVICE_MAX_RETRIES: int = 3            # 网络失败重试上限（论文级）
    PARAM_ADVICE_PROCESSING_STALE_MINUTES: int = 120  # 卡死 claim 恢复阈值
    PARAM_OVERRIDE_REFRESH_SECONDS: float = 5.0  # 跨进程参数刷新轮询间隔

    # 论文质量信号（可信度体系 L0，方向一）
    PAPER_SIGNAL_ENABLED: bool = True
    PAPER_SEASONED_DAYS: int = 60                # v1 预印本存活过初筛的天数阈值
    TIER_WEIGHT: dict = {"A": 1.00, "B": 0.97, "C": 0.93, "D": 0.00}
    QUORUM_BY_TIER: dict = {"A": 1, "B": 1, "C": 2}
    DELTA_BUDGET_FACTOR: dict = {"A": 1.0, "B": 0.7, "C": 0.5}
    OBSERVATION_DAYS_BY_TIER: dict = {"A": 3, "B": 5, "C": 7}
    # 论文时效（方向三）
    PAPER_STALE_WARN_MONTHS: int = 18
    PAPER_STALE_BLOCK_MONTHS: int = 36
    SUGGESTION_PENDING_TTL_DAYS: int = 30
    PARAM_OVERRIDE_REVIEW_DAYS: int = 90
    # 验证闭环（方向二）
    EVAL_QUERYSET_SIZE: int = 200
    EVAL_QUERYSET_WINDOW_DAYS: int = 90
    EVAL_QUERYSET_TTL_DAYS: int = 180
    EVAL_MIN_SAMPLES: int = 200
    EVAL_MIN_FEEDBACK_SAMPLES: int = 30
    EVAL_TOPK: int = 10
    OBSERVATION_MAX_DAYS: int = 21
    SHADOW_AUTO_ROLLBACK_ENABLED: bool = True
    MAX_ACTIVE_SHADOW_WINDOWS: int = 1
    SHADOW_OBSERVE_DAYS: int = 7  # 影子观察期（天）
    SHADOW_CHECK_INTERVAL_SECONDS: int = 3600  # 到期轮询间隔
    # 矛盾显式化（方向四）
    CONTRADICTION_QUORUM_BUMP: int = 1
    # 验证回流（方向五）
    PENALTY_FAIL_STREAK: int = 2
    PENALTY_FAIL_RATE: float = 0.5
    PENALTY_MIN_SAMPLES: int = 3
    PENALTY_TTL_DAYS: int = 180

    def model_post_init(self, __context):
        """DATABASE_URL / CHROMADB_PATH 未显式设置时从 LANTAI_HOME 推导（兼容旧 REMEMBRANCE_HOME）。"""
        if not self.LANTAI_HOME and self.REMEMBRANCE_HOME:
            self.LANTAI_HOME = self.REMEMBRANCE_HOME
        home = Path(self.LANTAI_HOME) if self.LANTAI_HOME else _REPO_ROOT
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
        if not self.API_KEY:
            logger.warning("API_KEY 为空：API 将以无鉴权模式运行（仅建议本机开发）")
        if not (os.environ.get("REMEMBRANCE_ENTITY_KEYWORDS")
                or os.environ.get("ENTITY_KEYWORDS")):
            logger.warning(
                "REMEMBRANCE_ENTITY_KEYWORDS 未配置：闸门仅识别通用自指模式，"
                "专有名词查询可能零召回"
            )


settings = Settings()
