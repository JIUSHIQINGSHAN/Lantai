from fastapi import APIRouter
from lantai.models.schemas import GateReq
from lantai.gate.decision import decide

router = APIRouter()

@router.post("/gate")
def gate(req: GateReq):
    return decide(req.candidate_id)
