from fastapi import APIRouter
from remembrance.models.schemas import GateReq
from remembrance.gate.decision import decide

router = APIRouter()

@router.post("/gate")
def gate(req: GateReq):
    return decide(req.candidate_id)
