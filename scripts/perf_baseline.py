"""性能基线工具——用 aiduMEM 50 问中文中性样本跑 POST /search"""
import json
import sys
import time

import requests

BASE_URL = "http://localhost:8767"
API_KEY = ""

# aiduMEM 50 问中文中性样本（精选 20 条）
SAMPLES = [
    "Python是什么", "什么是机器学习", "如何学习编程", "为什么需要数据库",
    "什么是API接口", "前端和后端的区别", "什么是云原生", "如何理解微服务",
    "什么是容器化", "Docker是什么", "什么是CI/CD", "什么是敏捷开发",
    "REST和GraphQL的区别", "什么是消息队列", "如何理解缓存", "什么是负载均衡",
    "什么是数据湖", "如何理解DevOps", "什么是领域驱动设计", "什么是事件驱动架构",
]


def run_baseline(base_url: str = BASE_URL, api_key: str = API_KEY) -> dict:
    """跑基线搜索，输出 P50/P95 延迟。"""
    headers = {"X-API-Key": api_key} if api_key else {}
    latencies = []

    for query in SAMPLES:
        start = time.perf_counter()
        try:
            resp = requests.post(
                f"{base_url}/search",
                json={"query": query, "top_k": 5, "use_rerank": False},
                headers=headers,
                timeout=30,
            )
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)
            print(f"  {query}: {elapsed:.0f}ms ({resp.status_code})")
        except Exception as e:
            print(f"  {query}: ERROR {e}")

    if not latencies:
        return {"error": "no successful requests"}

    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2]
    p95 = latencies[int(n * 0.95)]

    result = {
        "total_queries": n,
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "min_ms": round(latencies[0], 1),
        "max_ms": round(latencies[-1], 1),
        "avg_ms": round(sum(latencies) / n, 1),
    }
    print(f"\n{'='*40}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else BASE_URL
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    run_baseline(url, key)
