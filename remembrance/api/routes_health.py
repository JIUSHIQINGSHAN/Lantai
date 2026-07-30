from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
def health():
    return {"ok": True}

@router.get("/api/memory/health")
def memory_health():
    return {"ok": True, "service": "remembrance"}
