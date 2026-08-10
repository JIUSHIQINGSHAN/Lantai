from lantai.llm.client import chat_json
from lantai.llm.prompts import EXTRACT_SYS


def extract_candidate(title: str, content: str) -> dict:
    user = f"TITLE:\n{title}\n\nCONTENT:\n{content[:6000]}"
    try:
        data = chat_json(EXTRACT_SYS, user)
    except Exception:
        return {"summary": content[:400], "claims": [], "methods": [],
                "constraints": [], "actions": [], "topic": [],
                "extractor_confidence": 0.3}
    data.setdefault("summary", "")
    for k in ["claims", "methods", "constraints", "actions", "topic"]:
        data.setdefault(k, [])
    data["extractor_confidence"] = float(data.get("extractor_confidence", 0.5))
    return data
