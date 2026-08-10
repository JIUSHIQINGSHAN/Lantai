"""验证回流路由——人工验证结果入口（Step 8）。薄路由，业务全在 reliability.py。

POST /verification        记录一次人工验证（venue_class + passed）
GET  /verification/stats  查看各信号类别可靠性统计与当前降权系数

无内嵌鉴权：由 api_server 统一注入 verify_api_key（与 routes_retrieval 一致）。
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlmodel import select

from lantai.parameters import reliability
from lantai.parameters.trust_models import SignalReliabilityStat
from lantai.storage import db

router = APIRouter(tags=["verification"])


class VerificationReq(BaseModel):
    venue_class: str = Field(min_length=1, max_length=64)
    passed: bool
    note: str = ""


@router.post("/verification")
def record_verification(req: VerificationReq) -> dict:
    """记录一次人工验证结果，返回更新后的统计与当前降权系数。"""
    stat = reliability.record_verification_result(
        req.venue_class, passed=req.passed, note=req.note)
    return {
        "venue_class": stat.venue_class,
        "pass_count": stat.pass_count,
        "fail_count": stat.fail_count,
        "fail_streak": stat.fail_streak,
        "penalty": reliability.reliability_penalty(stat.venue_class),
    }


@router.get("/verification/stats")
def verification_stats() -> dict:
    """列出全部信号类别的可靠性统计与当前降权系数（可审计）。"""
    rows = []
    with db.get_session() as s:
        stats = s.exec(select(SignalReliabilityStat)).all()
        for st in stats:
            rows.append({
                "venue_class": st.venue_class,
                "pass_count": st.pass_count,
                "fail_count": st.fail_count,
                "fail_streak": st.fail_streak,
                "last_verified_at": (
                    st.last_verified_at.isoformat()
                    if st.last_verified_at is not None else None
                ),
                "penalty": reliability.reliability_penalty(st.venue_class),
            })
    return {"stats": rows}
