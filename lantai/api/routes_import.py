"""冷启动导入路由（Ticket 07）——薄路由，业务全在 import_service。"""
from fastapi import APIRouter

from lantai.models.schemas import ImportJsonlReq
from lantai.services.import_service import run_jsonl_import

router = APIRouter(tags=["import"])


@router.post("/import/jsonl")
def import_jsonl(req: ImportJsonlReq):
    """批量导入历史会话 JSONL（verbatim 直存，保留原始时间戳）。"""
    return run_jsonl_import(req.text)
