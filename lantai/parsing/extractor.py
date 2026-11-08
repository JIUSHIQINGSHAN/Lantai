from lantai.core.settings import settings
from lantai.llm.client import chat_json
from lantai.llm.prompts import EXTRACT_SYS, EXTRACT_USER_TEMPLATE
from lantai.services.prompt_service import get_prompt


def extract_candidate(title: str, content: str) -> dict:
    # v0.25 票 02 语言锁定：user 消息走中文任务框架模板（EXTRACT_USER_TEMPLATE，
    # 可被库内模板行覆盖）——根因实验证实输出语言跟随 user 框架语言，仅改
    # system 规则句无效（V1/V2 臂，见 docs/benchmarks/e1e2-lang-drift-2026-10/）。
    user = get_prompt("EXTRACT_USER_TEMPLATE", EXTRACT_USER_TEMPLATE).format(
        title=title, content=content[:6000]
    )
    try:
        # 票 48：ingest 链单发预算（预算模式一次机会即止，chat_json D-04 同
        # 范式）——逐 doc 串行调用挂起的上游不再占死整个 ingest job
        data = chat_json(
            get_prompt("EXTRACT_SYS", EXTRACT_SYS), user, timeout=settings.LLM_INGEST_TIMEOUT
        )
    except Exception:
        return {
            "summary": content[:400],
            "claims": [],
            "methods": [],
            "constraints": [],
            "actions": [],
            "topic": [],
            "atomic_facts": [],
            "extractor_confidence": 0.3,
        }
    data.setdefault("summary", "")
    for k in ["claims", "methods", "constraints", "actions", "topic", "atomic_facts"]:
        data.setdefault(k, [])
    data["extractor_confidence"] = float(data.get("extractor_confidence", 0.5))
    return data
