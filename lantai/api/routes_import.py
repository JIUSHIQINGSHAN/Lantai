"""冷启动导入路由（Ticket 07）——薄路由，业务全在 import_service。"""
from fastapi import APIRouter, Depends

from lantai.core.auth import get_current_user, SecurityContext
from lantai.models.schemas import ImportJsonlReq
from lantai.services.import_service import run_jsonl_import

router = APIRouter(tags=["import"])


@router.post("/import/jsonl")
def import_jsonl(req: ImportJsonlReq, ctx: SecurityContext = Depends(get_current_user)):
    """批量导入历史会话 JSONL（verbatim 直存，保留原始时间戳）。

    ACL 启用时按绑定 lane 集收窄：越界 lane 行记 errors 不落库（403 语义）。
    """
    return run_jsonl_import(req.text, allowed_lanes=ctx.allowed_lanes)
