"""冷启动导入 CLI：历史会话 JSONL 批量导入（保留原始时间戳）。

用法：
    python scripts/run_import.py --file sessions.jsonl            # 实际导入
    python scripts/run_import.py --file sessions.jsonl --dry-run  # 只预览不写库
    python scripts/run_import.py --file sessions.jsonl --json     # 只输出 JSON 汇总

JSONL 每行一条消息（腾讯 L0 同款）：{"role": "user"|"assistant",
"content": "...", "timestamp": <epoch 毫秒|秒|ISO 字符串>[, "session": "id"]}
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lantai.ingestion.import_service import import_session_jsonl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台冷启动会话导入")
    ap.add_argument("--file", required=True, help="会话 JSONL 文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只解析不写库（预览）")
    ap.add_argument("--user-id", default="default")
    ap.add_argument("--limit", type=int, default=None,
                    help="最多处理行数（默认 IMPORT_MAX_LINES）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 汇总")
    args = ap.parse_args()

    result = import_session_jsonl(args.file, dry_run=args.dry_run,
                                  user_id=args.user_id, max_lines=args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    mode = "dry-run 预览" if result["dry_run"] else "导入"
    print(f"会话导入（{mode}）: {result['path']}")
    print(f"行 {result['lines']}（解析 {result['parsed']} / 失败 {result['errors']}，"
          f"assistant 跳过 {result['skipped_assistant']}，会话 {result['sessions']}）")
    outcome = "（dry-run 不写库）" if result["dry_run"] else \
        f"导入 {result['imported']} 条"
    print(f"user 消息 {result['would_import']} 条 → {outcome}")
    if result["statuses"]:
        print("按状态:", ", ".join(f"{k}={v}" for k, v in result["statuses"].items()))
    if not result["dry_run"] and result["ingest_errors"]:
        print(f"摄取失败 {result['ingest_errors']} 条（已跳过，不拖停整批）")
    return 0


if __name__ == "__main__":
    main()