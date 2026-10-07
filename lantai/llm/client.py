import json
import threading

from openai import OpenAI
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lantai.core.settings import settings
from lantai.ingestion.safety import validate_api_url

_client_instance = None


class LLMConcurrencyTimeout(RuntimeError):
    """LLM 并发闸门等位超时（票据 .scratch/llm-concurrency-gate/01）。

    拿不到许可时放弃而不是排队到死；调用方按需捕获降级，未被捕获时
    走各自既有的异常路径（tenacity 不重试它——非网络错误）。
    """


# 进程级闸门：三外呼通道（chat_json / vision_caption / embed）共用一个
# BoundedSemaphore。上游 f0.3 教训（commit d770040）：只闸一个通道等于
# 没闸——提取与向量索引会在同一次潮波冲刷里先后触发。
# 惰性单例，与 _client_instance 同款形状：首次使用时按当前 settings 构造，
# 之后设置变更不自动重建（测试里显式置 None 重建）。
_llm_gate: threading.BoundedSemaphore | None = None
_gate_lock = threading.Lock()
_gate_init_args: tuple[int, float] | None = None


def _get_gate() -> threading.BoundedSemaphore:
    global _llm_gate, _gate_init_args
    args = (settings.LLM_MAX_CONCURRENCY, settings.LLM_ACQUIRE_TIMEOUT)
    if _llm_gate is None or _gate_init_args != args:
        with _gate_lock:
            if _llm_gate is None or _gate_init_args != args:
                _llm_gate = threading.BoundedSemaphore(args[0])
                _gate_init_args = args
    return _llm_gate


def _gate_acquire():
    """闸门上下文管理器：acquire 带超时，超时抛 LLMConcurrencyTimeout。

    用 try/finally 而非 with-semaphore——BoundedSemaphore 直接用 with 时
    acquire 无法带 timeout 参数。
    """
    gate = _get_gate()
    timeout = _gate_init_args[1]
    if not gate.acquire(timeout=timeout):
        raise LLMConcurrencyTimeout(
            f"LLM concurrency gate timed out after {timeout}s "
            f"(max_concurrency={_gate_init_args[0]})"
        )
    return _GateRelease(gate)


class _GateRelease:
    """acquire 成功后的归还句柄（with 语义：异常也归还）。"""

    def __init__(self, gate: threading.BoundedSemaphore):
        self._gate = gate

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._gate.release()
        return False


def get_client() -> OpenAI:
    global _client_instance
    if _client_instance is None:
        # Check URL safety right before init (lazy)
        validate_api_url(settings.OPENAI_BASE_URL)
        _client_instance = OpenAI(
            api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL
        )
    return _client_instance


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_not_exception_type(LLMConcurrencyTimeout),
)
def chat_json(system: str, user: str) -> dict:
    with _gate_acquire():
        resp = get_client().chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    return json.loads(resp.choices[0].message.content)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_not_exception_type(LLMConcurrencyTimeout),
)
def vision_caption(media_url: str) -> str:
    """目识（vision）：图片地址/data URI -> 详细视觉描述（v0.10 多模态）。

    复用单一 LLM 网关（OPENAI_API_KEY/BASE_URL）；VISION_MODEL 空时回退
    LLM_MODEL。media_url 只允许 http/https/data（上游 Vision API 取图，
    兰台不直接 fetch）。失败抛异常（由调用方决定 422，不落失败文本）。
    """
    from lantai.ingestion.safety import validate_media_url

    validate_media_url(media_url)
    model = settings.VISION_MODEL or settings.LLM_MODEL
    with _gate_acquire():
        resp = get_client().chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请一句话概括这张图片：包含 1. 是什么 2. 图中的 OCR 文本或数字 3. 图片的适用范围",
                        },
                        {"type": "image_url", "image_url": {"url": media_url}},
                    ],
                }
            ],
        )
    return resp.choices[0].message.content or ""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_not_exception_type(LLMConcurrencyTimeout),
)
def embed(texts: list[str]) -> list[list[float]]:
    with _gate_acquire():
        resp = get_client().embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
