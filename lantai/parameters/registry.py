"""
参数注册表（Parameter Registry）——论文可调参数白名单（default deny）。

设计原则（延续 fastpath「宁 miss 不脏写」）：
- 未显式登记为 adjustable=True 的参数，任何路径（LLM / API / DB 加载）都不可修改。
- 安全参数（凭据 / 网络 / 绑定 / 数据库路径）物理排除，永不进入白名单。
- 所有数值校验用 Decimal(str(value))，避免浮点步长误差。
"""
import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lantai.core.settings import settings

# ---------------------------------------------------------------- 类型定义

_Num = Decimal


class ParamSpec(BaseModel):
    """单个可调参数的完整规格。"""
    model_config = ConfigDict(frozen=True)

    name: str
    value_type: Literal["float", "int"]
    description: str
    source_attr: str              # settings 上的属性名（通常与 name 一致）
    group: str
    adjustable: bool = False
    minimum: _Num | None = None
    maximum: _Num | None = None
    step: _Num | None = None
    max_delta_per_apply: _Num | None = None
    risk_level: Literal["low", "medium", "high"] = "low"
    exclusion_reason: str | None = None


# ---------------------------------------------------------------- 白名单规格

def _d(v: float) -> _Num:
    return Decimal(str(v))


ADJUSTABLE_SPECS: list[ParamSpec] = [
    ParamSpec(
        name="RETRIEVAL_W_VECTOR", value_type="float", group="retrieval_weights",
        description="向量语义检索权重", source_attr="RETRIEVAL_W_VECTOR",
        adjustable=True, minimum=_d(0.30), maximum=_d(0.80), step=_d(0.05),
        max_delta_per_apply=_d(0.10), risk_level="low",
    ),
    ParamSpec(
        name="RETRIEVAL_W_BM25", value_type="float", group="retrieval_weights",
        description="jieba BM25 权重", source_attr="RETRIEVAL_W_BM25",
        adjustable=True, minimum=_d(0.10), maximum=_d(0.50), step=_d(0.05),
        max_delta_per_apply=_d(0.10), risk_level="low",
    ),
    ParamSpec(
        name="RETRIEVAL_W_FTS", value_type="float", group="retrieval_weights",
        description="FTS5 trigram 权重", source_attr="RETRIEVAL_W_FTS",
        adjustable=True, minimum=_d(0.00), maximum=_d(0.20), step=_d(0.05),
        max_delta_per_apply=_d(0.05), risk_level="low",
    ),
    ParamSpec(
        name="RETRIEVAL_W_DECAY", value_type="float", group="retrieval_weights",
        description="时效衰减权重", source_attr="RETRIEVAL_W_DECAY",
        adjustable=True, minimum=_d(0.00), maximum=_d(0.25), step=_d(0.05),
        max_delta_per_apply=_d(0.05), risk_level="low",
    ),
    ParamSpec(
        name="DEDUP_MERGE_THRESHOLD", value_type="float", group="dedup_thresholds",
        description="余弦相似度高于此值合并记忆", source_attr="DEDUP_MERGE_THRESHOLD",
        adjustable=True, minimum=_d(0.75), maximum=_d(0.95), step=_d(0.01),
        max_delta_per_apply=_d(0.05), risk_level="medium",
    ),
    ParamSpec(
        name="DEDUP_UPDATE_THRESHOLD", value_type="float", group="dedup_thresholds",
        description="余弦相似度高于此值更新记忆", source_attr="DEDUP_UPDATE_THRESHOLD",
        adjustable=True, minimum=_d(0.50), maximum=_d(0.75), step=_d(0.01),
        max_delta_per_apply=_d(0.05), risk_level="medium",
    ),
]

# ---------------------------------------------------------------- 物理排除清单

PHYSICALLY_EXCLUDED: tuple[str, ...] = (
    "API_KEY", "HOST", "PORT", "DATABASE_URL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "EMBED_MODEL",
    "RERANKER_MODEL", "RERANKER_BASE_URL", "RERANKER_ENABLED",
    "RERANKER_API_KEY", "RERANKER_TIMEOUT", "RERANKER_RETRY_DELAY",
    "RERANKER_CANDIDATE_MULTIPLIER",
    "SSRF_ALLOWED_SCHEMES", "SSRF_MAX_REDIRECTS", "SSRF_MAX_BYTES",
    "ALLOWED_API_HOSTS", "BACKUP_MANIFEST_VERSION", "CHROMADB_PATH",
)

# ---------------------------------------------------------------- 分组约束

GROUP_CONSTRAINTS: dict[str, dict] = {
    # 检索权重四路之和必须等于 1.0（容差 1e-6）；任一权重变化必须带补偿变化
    "retrieval_weights": {
        "kind": "sum_equals", "target": "1.0", "epsilon": "1e-6",
    },
    # merge 阈值必须比 update 阈值高至少 0.10
    "dedup_thresholds": {
        "kind": "ordered_gap", "higher": "DEDUP_MERGE_THRESHOLD",
        "lower": "DEDUP_UPDATE_THRESHOLD", "min_gap": "0.10",
    },
}


def get_param_registry() -> dict[str, ParamSpec]:
    """返回 name -> ParamSpec 的映射（白名单）。"""
    return {s.name: s for s in ADJUSTABLE_SPECS}


def get_adjustable_names() -> list[str]:
    return [s.name for s in ADJUSTABLE_SPECS if s.adjustable]


def get_group_constraints() -> dict[str, dict]:
    return GROUP_CONSTRAINTS


# ---------------------------------------------------------------- 快照与版本

def default_snapshot() -> dict[str, float]:
    """静态 settings 当前值快照（仅白名单参数）。"""
    return {s.name: float(getattr(settings, s.source_attr)) for s in ADJUSTABLE_SPECS}


def canonical_json(obj) -> str:
    """canonical JSON 序列化（稳定键序、紧凑分隔）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_snapshot_hash(snapshot: dict) -> str:
    """快照的 canonical hash，格式 sha256:hex。"""
    digest = hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def get_registry_version() -> str:
    """注册表 canonical JSON 的版本哈希——建议与 override 均携带，防止跨版本误用。"""
    canonical = canonical_json([s.model_dump(mode="json") for s in ADJUSTABLE_SPECS])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_text(text: str) -> str:
    """统一空白，用于 quote 子串校验（避免换行/空格差异误判）。"""
    return " ".join(text.split())
