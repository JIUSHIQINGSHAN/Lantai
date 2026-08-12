import json
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.ingestion.safety import validate_api_url

# 审计 M7：base_url host 必须命中 allowlist，防止密钥/全文发往任意地址
validate_api_url(settings.OPENAI_BASE_URL)

_client = OpenAI(api_key=settings.OPENAI_API_KEY,
                 base_url=settings.OPENAI_BASE_URL)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def chat_json(system: str, user: str) -> dict:
    resp = _client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def vision_caption(media_url: str) -> str:
    """目识（vision）：图片地址/data URI -> 详细视觉描述（v0.10 多模态）。

    复用单一 LLM 网关（OPENAI_API_KEY/BASE_URL）；VISION_MODEL 空时回退
    LLM_MODEL。media_url 只允许 http/https/data（上游 Vision API 取图，
    兰台不直接 fetch）。失败抛异常（由调用方决定 422，不落失败文本）。
    """
    from lantai.ingestion.safety import validate_media_url
    validate_media_url(media_url)
    model = settings.VISION_MODEL or settings.LLM_MODEL
    resp = _client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "这是一张需要被记忆库记住的图片。请详细描述：1. 画面主体是什么 2. 图中的 OCR 文字或显著特征 3. 图片的情景氛围。"},
                {"type": "image_url", "image_url": {"url": media_url}},
            ],
        }],
    )
    return resp.choices[0].message.content or ""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def embed(texts: list[str]) -> list[list[float]]:
    resp = _client.embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
