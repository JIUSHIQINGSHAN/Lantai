"""Daily Digest 读取路由（Ticket 03）——薄路由，业务全在 digest_worker。

GET /digest/today   当日盘点报告（未生成则生成一次）

归属收窄（票 .scratch/readside-gaps/04）：此前本端点一个身份都不取，
`stats` 是全库聚合、`path` 是宿主机绝对路径（含操作系统用户名）。
现在取 principal 下传：计数按 viewer 过滤，路径只回文件名。
"""

from fastapi import APIRouter, Depends

from lantai.core.auth import get_current_user
from lantai.workers.digest_worker import load_today_digest

router = APIRouter(tags=["digest"])


@router.get("/digest/today")
def digest_today(ctx=Depends(get_current_user)):
    return load_today_digest(principal=ctx)
