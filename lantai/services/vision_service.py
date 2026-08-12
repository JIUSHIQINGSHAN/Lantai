"""目识（vision）多模态记忆服务（v0.10，借鉴 aiduMEI v18.3.0 多模态感知窄版）。

media_url（图片地址/data URI）→ Vision API 生成视觉描述 caption → 作为记忆
正文走既有提取链；溯源经 provenance（prompt=vision-caption + media_url）。

窄版差异：
- 不引入独立 vision 密钥配置段（复用单一 LLM 网关）；
- 失败抛 ValueError（路由 422），绝不把「图片解析失败」等失败文本入库
  （作者版会落失败字符串——脏写，违背宁 miss 不脏写）。
"""
from lantai.core.provenance import PROVENANCE_PROMPT_VISION
from lantai.core.settings import settings
from lantai.llm.client import vision_caption
from lantai.models.schemas import AddMemoryReq


def build_vision_memory(req: AddMemoryReq) -> AddMemoryReq:
    """media_url 非空时：生成 caption 作为正文，返回增强后的 req。

    语义（由 AddMemoryReq._check_content_or_media 保证）：
    - media_url + 空 content → caption 注入 content；
    - media_url + 非空 content → 已在 schema 层拒绝；
    - 无 media_url → 原样返回（零开销）。
    """
    media_url = (req.media_url or "").strip()
    if not media_url:
        return req
    caption = vision_caption(media_url)
    if not caption.strip():
        raise ValueError("vision caption empty — 拒绝落库（宁 miss 不脏写）")
    return req.model_copy(
        update={
            "content": caption.strip(),
            "source_type": req.source_type or "vision",
        }
    )


def vision_provenance_extra(req: AddMemoryReq) -> dict:
    """vision 溯源附加信息：图片地址 + 模型。"""
    return {
        "media_url": (req.media_url or "").strip(),
        "vision_model": settings.VISION_MODEL or settings.LLM_MODEL,
    }


__all__ = ["build_vision_memory", "vision_provenance_extra", "PROVENANCE_PROMPT_VISION"]
