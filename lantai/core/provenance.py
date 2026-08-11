"""提取来源（provenance）——借鉴 TencentDB Agent Memory Roadmap v2.0.1：

生成的记忆携带「哪套 prompt / 哪个模型 / 何时产出」，让"记忆质量变差"成为
可溯源问题而非猜测。链路：candidate（提取时落）→ proposal（继承）→
MemoryItem（promoter 落库），最终每条提取类记忆都能回答"这套记忆是谁产出的"。

未来自定义 prompt 时，prompt 名即版本标识（如 extract-v1 / extract-v2 区分效果）。
"""
from lantai.core.settings import settings
from lantai.core.time import utcnow

PROVENANCE_PROMPT_EXTRACT = "extract-v1"                # lantai/llm/prompts.py EXTRACT_SYS
PROVENANCE_PROMPT_FASTPATH_DIRECT = "fastpath-direct"   # memory_service fastpath（规则直通，零 LLM）
PROVENANCE_PROMPT_DIALOGUE_FASTPATH = "dialogue-fastpath"  # dialogue 白名单直通（零 LLM）
PROVENANCE_PROMPT_DIALOGUE_CHITCHAT = "dialogue-chitchat"  # dialogue 闲聊兜底（零 LLM）
PROVENANCE_PROMPT_DIALOGUE_IMPORT = "dialogue-session-import"  # 冷启动导入历史会话（保留原始时间戳）


def make_provenance(prompt: str) -> dict:
    """构造提取来源记录：哪套 prompt + 哪个模型 + 何时产出。"""
    return {
        "prompt": prompt,
        "model": settings.LLM_MODEL,
        "extracted_at": utcnow().isoformat(),
    }