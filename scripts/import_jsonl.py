"""冷启动导入 CLI（Ticket 07）：读本地 JSONL 文件，POST 到兰台 /import/jsonl。

用法：
    python scripts/import_jsonl.py history.jsonl [--host 127.0.0.1] [--port 8767] [--key KEY]

JSONL 每行一个 JSON 对象：{"content": "...", "created_at": "ISO8601",
"lane": "fact", "tags": ["a"]}；content 必填，created_at/updated_at 可省略（缺省取当前时间）。

安全边界：HTTP 请求原语与 SSRF 边界校验统一复用 scripts/check_ingest_wiring.py
的 `_request` / `_validate_target`（默认仅回环目标，`--allow-remote` 显式放行；
单一真源，避免每个脚本各留一份边界检查）。
"""

import argparse
import json
import sys

from check_ingest_wiring import _request, _validate_target


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入历史会话 JSONL 到兰台")
    parser.add_argument("file", help="JSONL 文件路径（每行一个 JSON 对象）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8767")
    parser.add_argument("--key", default="", help="X-API-Key（服务配置了 API_KEY 时必填）")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="显式放行非回环目标（默认仅允许回环，SSRF 纪律）",
    )
    args = parser.parse_args()

    try:
        _validate_target("http://" + args.host + ":" + str(args.port), args.allow_remote)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with open(args.file, encoding="utf-8") as fh:
        text = fh.read()

    url = "http://" + args.host + ":" + str(args.port) + "/import/jsonl"
    status, report = _request(url, args.key, "POST", {"text": text})
    if status == 0:
        print("请求失败：连不上服务", file=sys.stderr)
        return 1
    if status != 200:
        print(f"HTTP {status}: {report}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
