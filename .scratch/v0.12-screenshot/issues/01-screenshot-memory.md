# 01 目识·截屏入忆（v0.12）

借鉴源：aiduMEI `tools/shot.js`（截图入忆工具）的「显式触发」思路——不引入
监听/扫描，用户截图后主动入忆；实现走兰台既有目识 data URI 通道。

## 设计

- `scripts/screenshot_memory.ps1`：剪贴板/文件 → data URI → `POST /add`（DryRun 预演）。
- `validate_media_url` 增强：data URI MIME 白名单（png/jpeg/webp/gif）+ base64 严格校验
  + 解码后 ≤ `MEDIA_DATA_URI_MAX_BYTES`（10MB）。
- `AddMemoryReq.media_url` max_length 2000 → 15_000_000（截屏 data URI 可达 MB 级）。

## 测试要求

- data URI 校验五例（合法/非位图/svg/坏 base64/空 payload）+ 超限（monkeypatch）。
- schema 长 data URI 通过。
- 脚本冒烟：DryRun + 无图报错。

## 状态：resolved（2026-08-12，随提交推送）

### 实现记录

- `lantai/core/settings.py`：`MEDIA_DATA_URI_MAX_BYTES: int = 10MB`
- `lantai/ingestion/safety.py`：`_validate_data_uri`（MIME 白名单 + base64 严格解码 + 大小上限）
- `lantai/models/schemas.py`：`media_url` max_length 15_000_000
- `scripts/screenshot_memory.ps1`（新）：剪贴板/文件 → data URI → POST /add（STA 重入、DryRun）
- `tests/test_vision.py`：+3 例（data URI 规则 / 超限 / schema 长 URI）

### 验证

- `pytest tests/test_vision.py -q` → 10 passed（原 7 + 新 3）
- 脚本冒烟：`-FromFile -DryRun` → 246 字节 PNG → 350 字符 data URI；无图报错指引 ✓
- 真实 `POST /add` 依赖 LLM 密钥（vision_caption），本机无 key 环境由测试覆盖

### 明确不吸收

- 文件系统扫描/监听、独立图片存储（data URI 自包含落库）
