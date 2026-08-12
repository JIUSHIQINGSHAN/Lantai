# v0.12 目识·截屏入忆（screenshot → memory）spec

目识（v0.10）闭环：让「看见即记忆」可日常用——本地截图/图片 → data URI → 既有
`/add media_url` 通道 → vision_caption 生成描述入忆。零新概念，补全链路入口。

## 设计

- `scripts/screenshot_memory.ps1`：剪贴板截图（Win+Shift+S）或 `-FromFile` 图片
  → PNG bytes → base64 data URI → `POST /add`（title/lane/BaseUri/ApiKey 参数化，
  `-DryRun` 只构造不写库；pwsh7 MTA 自动用 powershell.exe STA 重入）。
- `lantai/ingestion/safety.py::validate_media_url` 增强 data URI 校验（宁 miss 不脏写）：
  - MIME 白名单：png/jpeg/webp/gif（svg 等矢量拒绝）；
  - `;base64,` 分隔、payload 非空、base64 严格解码；
  - 解码后 ≤ `settings.MEDIA_DATA_URI_MAX_BYTES`（默认 10MB）。
- `lantai/models/schemas.py`：`AddMemoryReq.media_url` max_length 2000 → 15_000_000
  （截图 data URI 可达 MB 级字符）。

## 测试要求

- data URI：合法 png 通过 / text、svg 拒绝 / 坏 base64 拒绝 / 空 payload 拒绝 /
  超限拒绝（monkeypatch 上限）。
- schema：3000+ 字符 data URI 通过（旧上限 2000 拒绝）。
- 脚本冒烟：`-FromFile` + `-DryRun` 输出 data URI 长度；无图时报错指引。

## 明确不吸收

- 不引入文件系统扫描/监听（保持「用户显式触发」）；不改变图片存储方式
  （data URI 自包含，落库即全量，不另存文件）。
