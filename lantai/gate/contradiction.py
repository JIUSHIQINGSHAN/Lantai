from lantai.core.logger import logger
from lantai.llm.client import chat_json
from lantai.llm.prompts import CONTRADICTION_SYS


def check_contradiction(new_claim: str, existing_content: str) -> dict:
    """LLM 矛盾检测。失败时返回**带标记**的「未检出」，不得与「检出无矛盾」同形。

    原实现 `except Exception: return {"contradicts": False, ...}` 把
    「检测器不可用」（超时/429/5xx/非 JSON/未配 key）等同于「检测器说没矛盾」，
    调用方无从区分，只能放行——正是脏写路径（票 .scratch/gate-fail-open/01）。

    修法：加 `check_unavailable: True` 标记 + warning 留痕。标记是**附加键**，
    成功路径的返回形状一字未改（既有测试断言的 contradicts/reason/severity 不受影响）。
    调用方据此把「检不了」路由到待审队列，而非静默放行。

    宁 miss 不脏写：检不了就不下结论，交人裁决。
    """
    user = f"NEW:\n{new_claim}\n\nEXISTING:\n{existing_content}"
    try:
        return chat_json(CONTRADICTION_SYS, user)
    except Exception as e:
        logger.warning("contradiction check unavailable: %s", e)
        return {
            "contradicts": False,
            "reason": "",
            "severity": "low",
            "check_unavailable": True,
        }
