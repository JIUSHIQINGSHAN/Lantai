# v0.10 — Vision 多模态记忆（目识，作者 v18.3.0 多模态感知窄版）

来源：aiduMEI v18.3.0「多模态感知纪元」——/add 原生支持 media_url，Vision API
生成 vision_caption 作为记忆本体。

## 窄版差异（不照搬）

- 不引入独立 vision 密钥配置段：兰台单一 LLM 网关（OPENAI_API_KEY/BASE_URL），
  VISION_MODEL 空时回退 LLM_MODEL——作者的双配置段 + fallback 增加面，收益低。
- 不把失败文本入库：作者 Vision 失败返回「图片解析失败：...」字符串并继续落库，
  是脏写；兰台窄版失败抛 ValueError → 路由 422（宁 miss 不脏写，不静默丢弃）。
- media_url 只允许 http/https/data URI（防协议绕过）；本地文件 base64 由调用方
  先转 data URI（本轮不做本地文件上传）。

## 设计

- `lantai/llm/client.py::vision_caption(media_url) -> str`：OpenAI 兼容
  chat.completions 带 image_url content，temperature=0.1，返回详细描述。
  复用现有 _client 与 settings；URL scheme 校验（validate_api_url 同族）。
- `lantai/services/vision_service.py::build_vision_memory(req) -> AddMemoryReq`：
  有 media_url 时：content 为空 → content = caption；content 非空 → 保留原文，
  caption 进 metadata.vision（media_url/model/captured_at）。失败抛 ValueError。
- REST `POST /add` 支持 `media_url` 可选字段（AddMemoryReq 增量）；MCP `add`
  同步支持（工具参数加 media_url）。
- 落库时 metadata.vision 随 RawDocument.meta 存档（可溯源）。

## 明确不吸收

- 失败文本入库（脏写）；独立 vision 密钥（单一网关）；本地文件上传（调用方转
  data URI）；多模态向量（本轮不建 image embedding，captions 走既有文本通道）。
