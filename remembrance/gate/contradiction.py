from remembrance.llm.client import chat_json
from remembrance.llm.prompts import CONTRADICTION_SYS


def check_contradiction(new_claim: str, existing_content: str) -> dict:
    user = f"NEW:\n{new_claim}\n\nEXISTING:\n{existing_content}"
    try:
        return chat_json(CONTRADICTION_SYS, user)
    except Exception:
        return {"contradicts": False, "reason": "", "severity": "low"}
