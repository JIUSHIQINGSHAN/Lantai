"""Daily Digest 读取路由（Ticket 03）——薄路由，业务全在 digest_worker。

GET /digest/today   当日盘点报告（未生成则生成一次）
"""
from fastapi import APIRouter

from lantai.workers.digest_worker import load_today_digest

router = APIRouter(tags=["digest"])


@router.get("/digest/today")
def digest_today():
    return load_today_digest()
