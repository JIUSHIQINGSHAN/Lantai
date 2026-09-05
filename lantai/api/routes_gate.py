from fastapi import APIRouter

from lantai.gate.decision import decide
from lantai.models.schemas import GateReq

router = APIRouter()

@router.post("/gate")
def gate(req: GateReq):
    return decide(req.candidate_id)
