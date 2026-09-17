#!/usr/bin/env python3
"""check_ingest_wiring — 记忆写入接线自查（部署后第一件该跑的事）

为什么有这个脚本
────────────────
兰台有两条独立的链路，**必须都接上**才算能用：

    读（注入）：宿主每轮对话前 → POST /search → 把相关记忆塞进上下文
    写（落库）：宿主每轮对话后 → POST /dialogue 或 POST /add → 落库

只接读不接写，系统会表现得**非常正常**：检索有结果、/health 全绿、
司天指标好看——因为库里那些旧记忆确实健康。但你说的每一句新话都在
看完即丢，几周后才会隐约觉得「它怎么什么都不记得」（上游 aiduMEI
2026-09-17 生产事故，探针全绿、人工翻库才发现）。

这个脚本只问一个问题：

    **你在读，那你在写吗？**

做法：真写一条带标记的记忆 → 立刻带同一 session 检索回读 →
只看 /add 返 200 **不算数**，必须在检索结果里真的看到它（写读回环）。

安全边界：本脚本是与被检服务同机的运维自查工具，目标默认锁死回环
（127.0.0.1/::1/localhost），协议仅 http/https；要指向远端必须显式
传 --allow-remote（SSRF 纪律，与兰台服务端 ALLOWED_API_HOSTS 同思路）。

用法
────
    python scripts/check_ingest_wiring.py                     # 本机默认端口
    python scripts/check_ingest_wiring.py --url http://127.0.0.1:8000 --token xxx
    python scripts/check_ingest_wiring.py --json              # 机器可读
    python scripts/check_ingest_wiring.py --require-judgment  # CI 门禁模式

退出码：0 = 接线正常 / 1 = 接线有问题 / 2 = 连不上服务
--require-judgment 下「无判据」（样本不足/旧版本读线）也返回 1。
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("LANTAI_API_BASE", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_TOKEN = os.getenv("API_KEY", "")

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _validate_target(url: str, allow_remote: bool) -> None:
    """SSRF 边界：协议仅 http/https；默认仅回环目标，远端需显式放行。

    解析后 IP 也校验（防 hostname 伪装成回环名指向私网/元数据地址）。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅允许 http/https 协议，收到：{parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL 缺少主机名")
    if allow_remote:
        return
    if host in _LOOPBACK_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"目标主机解析失败：{host}（{exc}）")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"目标 {host} 解析到公网地址 {ip}；指向远端服务必须显式传 --allow-remote"
            )


def _request(url: str, token: str, method: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-API-Key", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            return resp.status, data
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            data = {}
        return exc.code, data
    except (urllib.error.URLError, OSError):
        return 0, {}


def main() -> int:
    parser = argparse.ArgumentParser(description="写读回环接线自查（票据 05）")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="显式放行非回环目标（默认仅允许回环，SSRF 纪律）",
    )
    parser.add_argument(
        "--require-judgment",
        action="store_true",
        help="CI 门禁：无判据/样本不足时也返回 1（默认只报告不挡门）",
    )
    args = parser.parse_args()

    try:
        _validate_target(args.url, args.allow_remote)
    except ValueError as exc:
        if not args.json:
            print(f"[FAIL] {exc}")
        else:
            print(json.dumps({"judgment": "invalid_target", "error": str(exc)}))
        return 2

    base = args.url.rstrip("/")
    marker = f"ingest-wiring-probe-{int(time.time())}"
    session_id = f"wiring-probe-{marker[-10:]}"

    status, _health = _request(f"{base}/health", args.token, "GET")
    if status == 0:
        if not args.json:
            print(f"[FAIL] 连不上服务 {base}")
        else:
            print(json.dumps({"judgment": "unreachable", "url": base}))
        return 2

    # ① 写线：真写一条（带 session_id，走完整闸门管线）
    status, add_resp = _request(
        f"{base}/add",
        args.token,
        "POST",
        {
            "title": f"接线自查 {marker}",
            "content": f"接线探针内容 {marker}（写后即删，可安全忽略）",
            "lane": "general",
            "session_id": session_id,
        },
    )
    wrote = status == 200
    if not wrote:
        if not args.json:
            print(f"[FAIL] 写线不通：POST /add 返回 {status}")
        else:
            print(json.dumps({"judgment": "write_failed", "status": status}))
        return 1
    candidate_id = (add_resp or {}).get("candidate_id")

    # 触发演化：候选过闸门才有记忆可召回（自动应用规则：高置信度直通）
    _request(f"{base}/workers/evolve/run", args.token, "POST")

    # ② 读线回环：带同一 session 检索，必须真的看到探针内容
    status, search_resp = _request(
        f"{base}/search",
        args.token,
        "POST",
        {"query": f"接线探针内容 {marker}", "top_k": 10, "force": True},
    )
    results = (search_resp or {}).get("results") or []
    seen = any(marker in json.dumps(r, ensure_ascii=False) for r in results)

    # ③ 清理探针（候选 + 可能已生成的记忆）
    if candidate_id:
        _request(f"{base}/candidates/{candidate_id}/reject", args.token, "POST", {})
    for r in results:
        mem = r.get("memory") or {}
        if isinstance(mem, dict) and marker in (mem.get("content") or ""):
            _request(f"{base}/memory/{mem.get('id')}", args.token, "DELETE")

    judgment = "ok" if seen else "read_miss"
    if args.json:
        print(
            json.dumps(
                {
                    "judgment": judgment,
                    "wrote": wrote,
                    "recalled": seen,
                    "search_results": len(results),
                },
                ensure_ascii=False,
            )
        )
    else:
        if seen:
            print("[OK] 写读回环通畅：写入的记忆已能召回")
        else:
            print(
                "[FAIL] 写线返 200 但检索召回不到——「只看 /add 返 200 不算数」。"
                "检查演化 worker 是否运行（POST /workers/evolve/run）与向量索引。"
            )
    if judgment != "ok":
        return 1 if args.require_judgment else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
