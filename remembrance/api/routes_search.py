from fastapi import APIRouter
from remembrance.models.schemas import SearchReq
from remembrance.retrieval.hybrid import hybrid_search

router = APIRouter()

@router.post("/search")
def search(req: SearchReq):
    return {"results": hybrid_search(req.query, req.top_k, req.memory_types)}
