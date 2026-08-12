# 01 Vision 多模态记忆（目识，v0.10）

借鉴源：aiduMEI v18.3.0「多模态感知纪元」——/add 原生支持 media_url + Vision
vision_caption。

## 设计

- `lantai/llm/client.py::vision_caption(media_url) -> str`：OpenAI 兼容 Vision 调用，
  复用 _client/settings；scheme 校验 http/https/data；失败抛异常（不落失败文本）。
- settings 增 `VISION_MODEL: str = ""`（空 = 回退 LLM_MODEL）。
- `lantai/services/vision_service.py::build_vision_memory(req) -> req`：media_url 时
  caption 生成；content 空 → caption 作正文；非空 → caption 存 metadata.vision。
  失败抛 ValueError（宁 miss 不脏写）。
- `AddMemoryReq.media_url: str = ""`（可选）；REST `/add` 与 MCP `add` 透传。
- 测试：`tests/test_vision.py`——纯函数（scheme 校验/失败语义/构造）+ 真实 SQLite
  走 add 链路（仅 mock vision_caption 外部网络）。

## 测试要求

- vision_caption scheme 校验：非 http/https/data 拒绝。
- build_vision_memory：content 空用 caption；content 非空 caption 进 metadata；
  失败抛 ValueError 不落库。
- add 全链路：真实 SQLite + FTS，mock vision_caption，验证落库带 metadata.vision。


## 状态：resolved（2026-08-12，随提交推送）

### 实现记录

- `lantai/core/settings.py`：`VISION_MODEL: str = ""`（空时回退 `LLM_MODEL`）
- `lantai/core/provenance.py`：`PROVENANCE_PROMPT_VISION = "vision-caption"`；`make_provenance` 支持 `extra` 附加溯源字段
- `lantai/llm/client.py::vision_caption(media_url)`：OpenAI 兼容 chat.completions + image_url，temperature=0.1 / max_tokens=500，复用 `_client` 单一网关
- `lantai/ingestion/safety.py::validate_media_url`：http/https/data 白名单；兰台不直接 fetch 图片，零 SSRF 面
- `lantai/models/schemas.py`：`AddMemoryReq.media_url`（max=2000）；content/media_url 二选一校验（同给拒绝 / 皆空拒绝 / <10 字拒绝）
- `lantai/services/vision_service.py`（新）：`build_vision_memory`（caption 注入；空 caption 抛 ValueError 不落失败文本）；`vision_provenance_extra`（media_url + vision_model）
- `lantai/services/memory_service.py`：`add_memory` media_url 分支短路同步走提取链（provenance_prompt=vision-caption + extra）
- `scripts/mcp_server.py`：`handle_add` 透传 media_url + `TOOLS["add"]` schema 加字段
- `tests/test_vision.py`（新）：7 例——scheme 校验 / 二选一 / caption 注入 / 空 caption 拒绝 / passthrough / add 全链路（真实 SQLite+FTS，仅 mock LLM）/ 失败不落库

### 验证

- `pytest tests/test_vision.py -q` → 7 passed
- 回归 `pytest tests/test_mcp.py tests/test_graph.py -q` → 51 passed
- 命名纪律：「目识」已登记 CONTEXT.md 词汇表 + ADR-0013 映射表（先登记后使用）

### 明确不吸收（宁 miss 不脏写）

- 作者版 Vision 失败落「图片解析失败」字符串入库——脏写，拒绝；改为抛 ValueError 由调用方裁决
