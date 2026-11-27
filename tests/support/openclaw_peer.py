"""Stdlib-only NDJSON peer for native adapter process and failure contracts."""

import json
import os
import sys
import time

for line in sys.stdin:
    data = json.loads(line)
    with open(os.environ["LANTAI_ADAPTER_TEST_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
    query = data.get("query", "")
    if query == "timeout":
        time.sleep(10)
    if query == "exit":
        sys.exit(0)
    if query == "malformed" or (
        data.get("type") == "backfill" and data.get("event_id") == "bad-ack"
    ):
        print("not-json", flush=True)
        continue
    if query == "array":
        print("[]", flush=True)
        continue
    if query == "oversize":
        print("x" * 1100000, flush=True)
        continue
    result = {
        "ok": True,
        "host": os.environ.get("LANTAI_HOST"),
        "owner": os.environ.get("LANTAI_HOOK_USER_ID"),
        "role": os.environ.get("LANTAI_HOOK_ROLE"),
        "echo": data,
    }
    if data.get("type") == "backfill":
        result["receipt_status"] = "acked"
    if not data.get("type"):
        result.update(
            context="<memory>" + query + "</memory>",
            event_id="bad-ack" if query == "bad-ack" else "rev_" + data.get("session_id", ""),
            evidence=[{"id": "mem_1"}, {"id": "mem_1"}, {"id": None}],
        )
    print(json.dumps(result, ensure_ascii=False), flush=True)
