"""冷启动导入 CLI（Ticket 07）：读本地 JSONL 文件，POST 到兰台 /import/jsonl。

用法：
    python scripts/import_jsonl.py history.jsonl [--host 127.0.0.1] [--port 8767] [--key KEY]

JSONL 每行一个 JSON 对象：{"content": "...", "created_at": "ISO8601",
"lane": "fact", "tags": ["a"]}；content 必填，created_at/updated_at 可省略（缺省取当前时间）。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入历史会话 JSONL 到兰台")
    parser.add_argument("file", help="JSONL 文件路径（每行一个 JSON 对象）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8767")
    parser.add_argument("--key", default="", help="X-API-Key（服务配置了 API_KEY 时必填）")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        text = fh.read()

    req = urllib.request.Request(
        f"http://{args.host}:{args.port}/import/jsonl",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if args.key:
        req.add_header("X-API-Key", args.key)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            report = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 —— CLI 边界
        print(f"请求失败: {e}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
