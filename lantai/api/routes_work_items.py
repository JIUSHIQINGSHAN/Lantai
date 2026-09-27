"""案牍控制台聚合路由。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from lantai.core.auth import get_current_user
from lantai.models.work_items import (
    BatchActionResult,
    BatchDeferRequest,
    BatchOrganizeRequest,
    BatchRejectRequest,
    WorkItemDetailResponse,
    WorkItemListResponse,
)
from lantai.services.work_item_action_service import (
    batch_defer,
    batch_organize,
    batch_reject,
)
from lantai.services.work_item_service import get_work_item_detail, list_work_items
from lantai.services.worker_operation_service import run_worker

router = APIRouter(tags=["work-items"])


@router.get("/work-items", response_model=WorkItemListResponse)
def list_work_items_route(
    section: str = Query(
        "", pattern="^(|immediate_action|pending_decisions|organization_needed|runtime_status)$"
    ),
    kind: str = Query(
        "", pattern="^(|candidate|proposal|conflict|parameter|crystal|memory|worker)$"
    ),
    risk: str = Query("", pattern="^(|critical|high|medium|low)$"),
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx=Depends(get_current_user),
):
    # 归属必须下传（票 .scratch/readside-gaps/01）：此前不取身份，
    # `load_work_item_snapshot` 全表捞，memory 分支的 summary 就是
    # `content` 全文——比 `GET /memories` 漏得更多（那条截断到 160）。
    return list_work_items(
        section=section,
        kind=kind,
        risk=risk,
        query=q,
        limit=limit,
        offset=offset,
        principal=ctx,
    )


@router.get("/work-items/detail/{kind}/{source_id}", response_model=WorkItemDetailResponse)
def get_work_item_detail_route(kind: str, source_id: str, ctx=Depends(get_current_user)):
    try:
        return get_work_item_detail(kind, source_id, principal=ctx)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/work-items/batch/reject", response_model=BatchActionResult)
def batch_reject_route(req: BatchRejectRequest):
    return batch_reject(req)


@router.post("/work-items/batch/defer", response_model=BatchActionResult)
def batch_defer_route(req: BatchDeferRequest):
    return batch_defer(req)


@router.post("/work-items/batch/organize", response_model=BatchActionResult)
def batch_organize_route(req: BatchOrganizeRequest, ctx=Depends(get_current_user)):
    # 归属必须透传（票 ownership-gaps/05）：批量不放宽单条校验
    return batch_organize(req, principal=ctx)


@router.post("/workers/{worker_name}/run")
def run_worker_route(worker_name: str):
    try:
        return run_worker(worker_name)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
