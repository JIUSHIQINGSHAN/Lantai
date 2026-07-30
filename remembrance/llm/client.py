import json
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from remembrance.core.settings import settings

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
def embed(texts: list[str]) -> list[list[float]]:
    resp = _client.embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
